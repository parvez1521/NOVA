"""Optional OpenRouter-compatible chat provider."""

from __future__ import annotations

import asyncio
import json
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from app.llm.base import ChatMessage, LLMProvider, ProviderHealth
from app.llm.answer import AnswerStreamFilter
from app.llm.errors import (
    LLMAuthenticationError,
    LLMModelNotFoundError,
    LLMProviderUnavailable,
    LLMRateLimitError,
    LLMResponseError,
    LLMTimeoutError,
)
from app.llm.models import ModelCapabilities, ModelDescriptor


class OpenRouterFreeModelCatalog:
    """Live model catalog with a sanitized local cache and explicit pricing states."""

    endpoint = "https://openrouter.ai/api/v1/models"
    free_router_model = "openrouter/free"

    def __init__(self, api_key: str | None, *, cache_path: Path | None = None, ttl: float = 900,
                 timeout: float = 10, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.api_key = api_key or ""
        self.cache_path = cache_path
        self.ttl = ttl
        self.timeout = timeout
        self.transport = transport
        self._models: list[ModelDescriptor] = []
        self._checked_at = 0.0
        self._live = False
        self._lock = asyncio.Lock()

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "HTTP-Referer": "https://nova.local", "X-Title": "NOVA AI Companion"}
        if self.api_key.strip():
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    @staticmethod
    def _pricing(item: dict) -> tuple[str, str]:
        pricing = item.get("pricing")
        if not isinstance(pricing, dict):
            return "UNKNOWN", "OpenRouter pricing missing"
        if "prompt" not in pricing or "completion" not in pricing or pricing.get("overrides"):
            return "UNKNOWN", "OpenRouter pricing missing or conditional"
        try:
            prices = [Decimal(str(value)) for key, value in pricing.items() if key not in {"discount", "overrides"}]
        except (InvalidOperation, TypeError, ValueError):
            return "UNKNOWN", "OpenRouter pricing malformed"
        if not prices or any(not price.is_finite() or price < 0 for price in prices):
            return "UNKNOWN", "OpenRouter pricing invalid"
        if all(price == 0 for price in prices):
            return "FREE", "OpenRouter /models prompt/completion/request prices are zero"
        return "PAID", "OpenRouter /models pricing"

    @classmethod
    def _descriptor(cls, item: dict) -> ModelDescriptor | None:
        model = item.get("id")
        architecture = item.get("architecture") if isinstance(item.get("architecture"), dict) else {}
        inputs = architecture.get("input_modalities") if isinstance(architecture.get("input_modalities"), list) else []
        outputs = architecture.get("output_modalities") if isinstance(architecture.get("output_modalities"), list) else []
        params = item.get("supported_parameters") if isinstance(item.get("supported_parameters"), list) else []
        if not isinstance(model, str) or not model:
            return None
        pricing, source = cls._pricing(item)
        input_set, output_set = set(inputs), set(outputs)
        raw_context_length = item.get("context_length")
        context_length = (
            raw_context_length
            if isinstance(raw_context_length, int) and not isinstance(raw_context_length, bool) and raw_context_length >= 1
            else None
        )
        caps = ModelCapabilities(
            text="text" in input_set and "text" in output_set,
            streaming=True if "text" in output_set else None,
            reasoning=True if isinstance(item.get("reasoning"), dict) or "reasoning" in params else None,
            vision=bool(input_set.intersection({"image", "video"})) if inputs else None,
            audio_input="audio" in input_set if inputs else None,
            audio_output="audio" in output_set or "speech" in output_set if outputs else None,
            tools="tools" in params or "tool_choice" in params,
            function_calling="tools" in params or "tool_choice" in params,
            structured_output="response_format" in params or "structured_outputs" in params,
            computer_use=None,
            search="web_search_options" in params,
            context_length=context_length,
        )
        benchmarks = item.get("benchmarks") if isinstance(item.get("benchmarks"), dict) else {}
        artificial = benchmarks.get("artificial_analysis") if isinstance(benchmarks.get("artificial_analysis"), dict) else {}
        quality = artificial.get("intelligence_index")
        return ModelDescriptor(provider="openrouter", model=model, name=str(item.get("name") or model),
            capabilities=caps, availability="AVAILABLE", pricing_state=pricing, local=False,
            last_checked=time.time(), pricing_source=source, supported_methods=params,
            input_modalities=inputs, output_modalities=outputs, output_limit=(item.get("top_provider") or {}).get("max_completion_tokens") if isinstance(item.get("top_provider"), dict) else None,
             quality=float(quality) if isinstance(quality, (int, float)) else None)

    @classmethod
    def _free_router_descriptor(cls, models: list[ModelDescriptor]) -> ModelDescriptor | None:
        """Expose OpenRouter's official free router only when live data proves it has candidates."""

        if any(model.model == cls.free_router_model for model in models):
            return None
        if not any(
            model.availability == "AVAILABLE"
            and model.pricing_state == "FREE"
            and model.capabilities.text is True
            and model.capabilities.streaming is True
            for model in models
        ):
            return None
        return ModelDescriptor(
            provider="openrouter",
            model=cls.free_router_model,
            name="OpenRouter Free Router",
            capabilities=ModelCapabilities(text=True, streaming=True),
            availability="AVAILABLE",
            pricing_state="FREE",
            local=False,
            last_checked=time.time(),
            pricing_source="OpenRouter free-model router backed by the live zero-price catalog",
            detail="Official openrouter/free route; capability-specific support is limited to text streaming unless catalog data proves more.",
        )

    def _load_cache(self) -> list[ModelDescriptor]:
        if not self.cache_path or not self.cache_path.is_file():
            return []
        try:
            payload = json.loads(self.cache_path.read_text())
            self._checked_at = float(payload.get("last_checked", 0))
            self._models = [ModelDescriptor.model_validate(item).model_copy(update={"availability": "UNKNOWN"}) for item in payload.get("models", [])]
            return self._models
        except (OSError, ValueError, TypeError):
            return []

    def _save_cache(self, models: list[ModelDescriptor]) -> None:
        if not self.cache_path:
            return
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps({"last_checked": self._checked_at, "models": [m.model_dump() for m in models]}, separators=(",", ":")))
        except OSError:
            pass

    async def discover(self, *, force: bool = False) -> list[ModelDescriptor]:
        async with self._lock:
            return await self._discover(force=force)

    async def _discover(self, *, force: bool = False) -> list[ModelDescriptor]:
        if self._live and not force and time.time() - self._checked_at < self.ttl:
            return self._models
        if not self._models:
            self._load_cache()
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
                rows = []
                for offset in range(0, 10000, 500):
                    response = await client.get(self.endpoint, headers=self._headers(),
                        params={"output_modalities": "all", "limit": 500, "offset": offset})
                    response.raise_for_status()
                    payload = response.json()
                    if not isinstance(payload.get("data"), list): raise ValueError("catalog")
                    rows.extend(payload["data"])
                    if not payload.get("links", {}).get("next"): break
                else: raise ValueError("catalog too large")
            models = [descriptor for item in rows if isinstance(item, dict) and (descriptor := self._descriptor(item))]
            free_router = self._free_router_descriptor(models)
            if free_router is not None:
                models.append(free_router)
            self._models, self._checked_at = models, time.time()
            self._live = True
            self._save_cache(models)
            return models
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            self._live = False
            self._models = [model.model_copy(update={"availability": "UNKNOWN", "pricing_state": "UNKNOWN",
                "detail": "Live catalog unavailable; cached metadata cannot authorize a cloud call."}) for model in self._models]
            return self._models


class OpenRouterProvider(LLMProvider):
    provider_name = "openrouter"
    endpoint = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(
        self,
        api_key: str | None,
        model: str,
        *,
        enabled: bool = True,
        timeout: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
        pricing_state: str = "UNKNOWN",
        free_only: bool = False,
        catalog: OpenRouterFreeModelCatalog | None = None,
    ) -> None:
        self.api_key = api_key or ""
        self.model_name = model
        self.enabled = enabled
        self.timeout = timeout
        self.transport = transport
        self.pricing_state = pricing_state
        self.free_only = free_only
        self.catalog = catalog or OpenRouterFreeModelCatalog(api_key, timeout=min(timeout, 5), transport=transport)

    @property
    def configured(self) -> bool:
        return self.enabled and bool(self.api_key.strip())

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self.timeout, transport=self.transport)

    @staticmethod
    def _error_from_status(status_code: int, detail: str, *, provider: str, model: str) -> Exception:
        if status_code in {401, 403}:
            return LLMAuthenticationError("OpenRouter authentication failed.", provider=provider)
        if status_code == 404:
            return LLMModelNotFoundError(f"OpenRouter model '{model}' was not found.", provider=provider)
        if status_code == 429:
            return LLMRateLimitError("OpenRouter rate limit reached.", provider=provider)
        return LLMResponseError(detail or "OpenRouter returned an HTTP error", provider=provider)

    async def is_available(self) -> bool:
        return self.configured

    async def health_check(self) -> ProviderHealth:
        if not self.enabled:
            return ProviderHealth(self.provider_name, self.model_name, False, False, detail="OpenRouter is disabled")
        if not self.api_key.strip():
            return ProviderHealth(self.provider_name, self.model_name, False, False, detail="OpenRouter API key is not configured")
        discovered = await self.discover_models()
        entry = next((item for item in discovered if item.model == self.model_name), None)
        available = bool(entry and entry.availability == "AVAILABLE")
        return ProviderHealth(
            self.provider_name,
            self.model_name,
            True,
            available,
            server_reachable=None,
            model_available=available,
            detail="Current catalog verified" if available else "Model catalog unavailable or model not listed",
        )

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://nova.local",
            "X-Title": "NOVA AI Companion",
        }

    async def discover_models(self) -> list[ModelDescriptor]:
        if not self.configured: return []
        return await self.catalog.discover()

    async def generate(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        think: bool | None = None,
        tools: Sequence[dict[str, Any]] | None = None,
    ) -> str:
        chunks: list[str] = []
        async for chunk in self.generate_stream(messages, temperature=temperature, max_tokens=max_tokens, think=think, tools=tools):
            chunks.append(chunk)
        return "".join(chunks)

    async def generate_stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        think: bool | None = None,
        tools: Sequence[dict[str, Any]] | None = None,
    ) -> AsyncIterator[str]:
        del think
        if not self.enabled:
            raise LLMProviderUnavailable("OpenRouter is disabled", provider=self.provider_name)
        if not self.api_key.strip():
            raise LLMAuthenticationError("OpenRouter API key is not configured", provider=self.provider_name)
        if self.free_only:
            live = await self.catalog.discover(force=True)
            descriptor = next((item for item in live if item.model == self.model_name), None)
            self.pricing_state = descriptor.pricing_state if descriptor and descriptor.availability == "AVAILABLE" else "UNKNOWN"
        if self.free_only and self.pricing_state != "FREE":
            from app.llm.errors import ModelPolicyError
            raise ModelPolicyError("OpenRouter model pricing is not verified as free.", provider=self.provider_name)

        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": list(messages),
            "stream": True,
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = list(tools)
        payload["provider"] = {"require_parameters": True}
        if self.free_only:
            # Server-side cap also covers changes between listing and inference.
            payload["provider"]["max_price"] = {"prompt": 0, "completion": 0, "image": 0, "request": 0}

        answer = AnswerStreamFilter()
        try:
            async with self._client() as client:
                async with client.stream("POST", self.endpoint, headers=self._headers(), json=payload) as response:
                    if response.status_code >= 400:
                        detail = (await response.aread()).decode("utf-8", errors="replace")[:240]
                        raise self._error_from_status(response.status_code, detail, provider=self.provider_name, model=self.model_name)

                    async for line in response.aiter_lines():
                        line = line.strip()
                        if not line or line.startswith(":"):
                            continue
                        if line.startswith("data:"):
                            line = line[5:].strip()
                        if line == "[DONE]":
                            break
                        try:
                            chunk = json.loads(line)
                        except json.JSONDecodeError as exc:
                            raise LLMResponseError("OpenRouter returned malformed streaming JSON", provider=self.provider_name, cause=exc) from exc
                        if not isinstance(chunk, dict):
                            raise LLMResponseError("OpenRouter returned a non-object stream chunk", provider=self.provider_name)
                        if isinstance(chunk.get("error"), dict):
                            message = chunk["error"].get("message") or "OpenRouter returned an error"
                            raise LLMResponseError(str(message), provider=self.provider_name)
                        choices = chunk.get("choices")
                        if not isinstance(choices, list) or not choices:
                            continue
                        delta = choices[0].get("delta") if isinstance(choices[0], dict) else None
                        content = delta.get("content") if isinstance(delta, dict) else None
                        if isinstance(content, str) and content:
                            visible = answer.feed(content)
                            if visible:
                                yield visible
        except asyncio.CancelledError:
            raise
        except (LLMProviderUnavailable, LLMModelNotFoundError, LLMAuthenticationError, LLMRateLimitError, LLMResponseError):
            raise
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(provider=self.provider_name, cause=exc) from exc
        except httpx.ConnectError as exc:
            raise LLMProviderUnavailable("OpenRouter could not be reached.", provider=self.provider_name, cause=exc) from exc
        except httpx.NetworkError as exc:
            raise LLMProviderUnavailable("OpenRouter network access is unavailable.", provider=self.provider_name, cause=exc) from exc
        except httpx.HTTPError as exc:
            raise LLMResponseError("OpenRouter returned an HTTP error", provider=self.provider_name, cause=exc) from exc
