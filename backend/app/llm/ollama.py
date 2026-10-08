"""Asynchronous Ollama chat provider."""

from __future__ import annotations

import asyncio
import json
import shutil
import time
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
    LLMThinkingUnsupportedError,
)
from app.llm.models import ModelCapabilities, ModelDescriptor
from app.computer.vision import is_loopback_url


class OllamaProvider(LLMProvider):
    provider_name = "ollama"

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        enabled: bool = True,
        timeout: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model_name = model
        self.enabled = enabled
        self.timeout = timeout
        self.transport = transport
        self._thinking_values: list[object] | None = None
        self._metadata_at = 0.0
        self._metadata_lock = asyncio.Lock()

    async def _check_thinking_mode(self, think: bool) -> None:
        """Reject known incompatible tags before they leak untagged reasoning.

        Cache small /api/show metadata, not model weights or request content.
        Non-Qwen providers and legacy metadata retain existing API behavior.
        """
        if not self.model_name.lower().startswith("qwen3"):
            return
        async with self._metadata_lock:
            if time.monotonic() - self._metadata_at > 60:
                info = await self.get_model_info()
                metadata = info.get("thinking")
                values = metadata.get("values") if isinstance(metadata, dict) else None
                self._thinking_values = values if isinstance(values, list) else None
                self._metadata_at = time.monotonic()
            if self._thinking_values and think not in self._thinking_values:
                raise LLMThinkingUnsupportedError(
                    f"Ollama model '{self.model_name}' cannot use think={str(think).lower()}. "
                    "Use a compatible hybrid tag such as qwen3:4b-q4_K_M.",
                    provider=self.provider_name,
                )

    def _client(self, *, timeout: float | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout or self.timeout, transport=self.transport)

    @staticmethod
    def _model_names(payload: dict[str, Any]) -> set[str]:
        names: set[str] = set()
        for item in payload.get("models", []):
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                names.add(item["name"])
        return names

    @staticmethod
    def _error_from_status(status_code: int, detail: str, *, provider: str, model: str) -> Exception:
        if status_code in {401, 403}:
            return LLMAuthenticationError(detail, provider=provider)
        if status_code == 404:
            return LLMModelNotFoundError(f"Ollama model '{model}' was not found.", provider=provider)
        if status_code == 429:
            return LLMRateLimitError(detail, provider=provider)
        return LLMResponseError(detail, provider=provider)

    async def is_available(self) -> bool:
        health = await self.health_check()
        return health.available

    async def health_check(self) -> ProviderHealth:
        if not self.enabled:
            return ProviderHealth(self.provider_name, self.model_name, False, False, detail="Ollama is disabled")

        try:
            async with self._client(timeout=min(self.timeout, 3.0)) as client:
                response = await client.get(f"{self.base_url}/api/tags")
            if response.status_code >= 400:
                detail = response.text[:240]
                error = self._error_from_status(response.status_code, detail, provider=self.provider_name, model=self.model_name)
                return ProviderHealth(
                    self.provider_name,
                    self.model_name,
                    True,
                    False,
                    server_reachable=True,
                    model_available=False,
                    detail=str(error),
                )

            payload = response.json()
            model_names = self._model_names(payload)
            model_available = self.model_name in model_names
            detail = "Ollama and the configured model are available" if model_available else f"Ollama model '{self.model_name}' is not installed"
            return ProviderHealth(
                self.provider_name,
                self.model_name,
                True,
                model_available,
                server_reachable=True,
                model_available=model_available,
                detail=detail,
            )
        except (httpx.ConnectError, httpx.NetworkError) as exc:
            detail = "Local LLM unavailable — Ollama is not running."
            if self.base_url.startswith(("http://localhost", "http://127.0.0.1")) and shutil.which("ollama") is None:
                detail = "Local LLM unavailable — Ollama is not installed."
            return ProviderHealth(
                self.provider_name,
                self.model_name,
                True,
                False,
                server_reachable=False,
                model_available=False,
                detail=detail,
            )
        except httpx.TimeoutException:
            return ProviderHealth(
                self.provider_name,
                self.model_name,
                True,
                False,
                server_reachable=False,
                model_available=False,
                detail="Ollama health check timed out",
            )
        except (httpx.HTTPError, ValueError, json.JSONDecodeError) as exc:
            return ProviderHealth(
                self.provider_name,
                self.model_name,
                True,
                False,
                detail=f"Ollama health check failed: {exc}",
            )

    async def discover_models(self) -> list[ModelDescriptor]:
        """Enumerate installed models without downloading or guessing abilities."""
        if not self.enabled:
            return []
        try:
            async with self._client(timeout=min(self.timeout, 5.0)) as client:
                response = await client.get(f"{self.base_url}/api/tags")
            if response.status_code >= 400:
                return []
            payload = response.json()
            result = []
            for item in payload.get("models", [])[:64]:
                if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                    continue
                capabilities = ModelCapabilities(text=True, streaming=True)
                try:
                    metadata = await self.get_model_info(item["name"])
                    if isinstance(metadata, dict):
                        thinking = metadata.get("thinking")
                        if isinstance(thinking, dict) and isinstance(thinking.get("values"), list):
                            capabilities.reasoning = True in thinking["values"]
                        known = metadata.get("capabilities")
                        if isinstance(known, list):
                            capabilities.text = "completion" in known
                            capabilities.vision = "vision" in known
                            capabilities.reasoning = "thinking" in known
                            capabilities.tools = "tools" in known
                            capabilities.function_calling = "tools" in known
                            # Ollama /api/chat supports JSON schemas for completion
                            # models; this is an adapter feature, not a guessed name.
                            capabilities.structured_output = "completion" in known
                        model_info = metadata.get("model_info")
                        if isinstance(model_info, dict):
                            context = next((value for key, value in model_info.items() if key.endswith(".context_length") and isinstance(value, int)), None)
                            if context:
                                capabilities.context_length = context
                except (LLMProviderUnavailable, LLMResponseError, LLMTimeoutError, LLMModelNotFoundError,
                        LLMAuthenticationError, LLMRateLimitError):
                    pass
                result.append(ModelDescriptor(
                    provider=self.provider_name,
                    model=item["name"],
                    name=item["name"],
                    capabilities=capabilities,
                    availability="AVAILABLE",
                    pricing_state="LOCAL_FREE" if is_loopback_url(self.base_url) else "UNKNOWN",
                    local=is_loopback_url(self.base_url),
                    last_checked=time.time(),
                    pricing_source="local Ollama installation",
                    detail="Advanced capabilities remain UNKNOWN until Ollama metadata proves them.",
                ))
            return result
        except (httpx.HTTPError, ValueError, TypeError):
            return []

    async def get_model_info(self, model: str | None = None) -> dict[str, Any]:
        """Return Ollama's metadata for the configured model."""

        if not self.enabled:
            raise LLMProviderUnavailable("Ollama is disabled", provider=self.provider_name)
        target = model or self.model_name
        try:
            async with self._client(timeout=min(self.timeout, 5)) as client:
                response = await client.post(f"{self.base_url}/api/show", json={"model": target})
            if response.status_code >= 400:
                raise self._error_from_status(response.status_code, response.text[:240], provider=self.provider_name, model=self.model_name)
            payload = response.json()
            if not isinstance(payload, dict):
                raise LLMResponseError("Ollama model metadata was malformed", provider=self.provider_name)
            return payload
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(provider=self.provider_name, cause=exc) from exc
        except httpx.ConnectError as exc:
            raise LLMProviderUnavailable("Local LLM unavailable — Ollama is not running.", provider=self.provider_name, cause=exc) from exc
        except (httpx.NetworkError, httpx.HTTPError) as exc:
            raise LLMProviderUnavailable("Ollama could not be reached.", provider=self.provider_name, cause=exc) from exc
        except ValueError as exc:
            raise LLMResponseError("Ollama model metadata was malformed", provider=self.provider_name, cause=exc) from exc

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

    async def generate_structured(self, messages, schema, *, max_tokens=1400):
        chunks=[]
        async for chunk in self.generate_stream(messages,temperature=0,max_tokens=max_tokens,think=False,response_schema=schema,context_size=12288):
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
        response_schema: dict[str, Any] | None = None,
        context_size: int | None = None,
    ) -> AsyncIterator[str]:
        if not self.enabled:
            raise LLMProviderUnavailable("Ollama is disabled", provider=self.provider_name)
        if think is not None:
            await self._check_thinking_mode(think)

        options: dict[str, Any] = {"temperature": temperature}
        if max_tokens is not None:
            options["num_predict"] = max_tokens
        if context_size is not None:
            options["num_ctx"] = context_size
        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": list(messages),
            "stream": True,
            "options": options,
        }
        if think is not None:
            payload["think"] = think
        if tools:
            payload["tools"] = list(tools)
        if response_schema is not None:
            payload["format"] = response_schema

        answer = AnswerStreamFilter()
        emitted = False
        saw_reasoning = False
        done_reason: str | None = None
        try:
            async with self._client() as client:
                async with client.stream("POST", f"{self.base_url}/api/chat", json=payload) as response:
                    if response.status_code >= 400:
                        detail = (await response.aread()).decode("utf-8", errors="replace")[:240]
                        raise self._error_from_status(response.status_code, detail, provider=self.provider_name, model=self.model_name)

                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            chunk = json.loads(line)
                        except json.JSONDecodeError as exc:
                            raise LLMResponseError("Ollama returned malformed streaming JSON", provider=self.provider_name, cause=exc) from exc
                        if not isinstance(chunk, dict):
                            raise LLMResponseError("Ollama returned a non-object stream chunk", provider=self.provider_name)
                        if isinstance(chunk.get("error"), str):
                            raise LLMResponseError(chunk["error"], provider=self.provider_name)
                        message = chunk.get("message")
                        if isinstance(message, dict) and message.get("thinking"):
                            saw_reasoning = True
                            if think is False:
                                raise LLMThinkingUnsupportedError(provider=self.provider_name)
                        content = message.get("content") if isinstance(message, dict) else None
                        if isinstance(content, str) and content:
                            visible = answer.feed(content)
                            if visible:
                                emitted = emitted or bool(visible.strip())
                                yield visible
                        if chunk.get("done"):
                            done_reason = chunk.get("done_reason")
                            break
            answer.flush()
            if not emitted:
                detail = "The model returned no final answer."
                if saw_reasoning and done_reason == "length":
                    detail += " Its combined reasoning/output budget ended; use LLM_THINKING_MODE=off or raise LLM_COMPLEX_MAX_TOKENS."
                raise LLMResponseError(detail, provider=self.provider_name)
        except asyncio.CancelledError:
            raise
        except LLMResponseError:
            raise
        except (LLMProviderUnavailable, LLMModelNotFoundError, LLMAuthenticationError, LLMRateLimitError):
            raise
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(provider=self.provider_name, cause=exc) from exc
        except httpx.ConnectError as exc:
            raise LLMProviderUnavailable("Local LLM unavailable — Ollama is not running.", provider=self.provider_name, cause=exc) from exc
        except httpx.NetworkError as exc:
            raise LLMProviderUnavailable("Ollama could not be reached.", provider=self.provider_name, cause=exc) from exc
        except httpx.HTTPError as exc:
            raise LLMResponseError("Ollama returned an HTTP error", provider=self.provider_name, cause=exc) from exc
