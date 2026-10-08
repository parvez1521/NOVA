"""Official Gemini REST API. Listing is unmetered; generation is policy gated."""

from __future__ import annotations

import json
import re
import time
from collections.abc import AsyncIterator
from decimal import Decimal, InvalidOperation
from typing import Iterable

import httpx

from app.llm.answer import AnswerStreamFilter
from app.llm.base import LLMProvider, ProviderHealth
from app.llm.errors import (LLMAuthenticationError, LLMModelNotFoundError, LLMProviderUnavailable,
    LLMRateLimitError, LLMResponseError, LLMTimeoutError, ModelPolicyError)
from app.llm.models import ModelCapabilities, ModelDescriptor


class GoogleGeminiProvider(LLMProvider):
    provider_name = "gemini"
    base_url = "https://generativelanguage.googleapis.com/v1beta"
    pricing_documentation = "https://ai.google.dev/gemini-api/docs/pricing"

    def __init__(self, api_key: str | None, model: str = "", *, enabled=True, timeout=60.,
                 free_only=True, transport=None, pricing_state="UNKNOWN",
                 free_model_allowlist: Iterable[str] = ()):
        self.api_key = api_key or ""
        self.model_name = model.removeprefix("models/")
        self.enabled, self.timeout, self.free_only, self.transport = enabled, timeout, free_only, transport
        self.pricing_state = pricing_state
        self.free_model_allowlist = tuple(dict.fromkeys(
            item.strip().removeprefix("models/") for item in free_model_allowlist if item and item.strip()
        ))

    @property
    def configured(self): return self.enabled and bool(self.api_key.strip())

    def _client(self, timeout=None):
        return httpx.AsyncClient(timeout=timeout or self.timeout, transport=self.transport)

    def _headers(self): return {"x-goog-api-key": self.api_key, "Content-Type": "application/json"}

    def _status(self, response):
        if response.status_code < 400: return
        errors = {401: LLMAuthenticationError, 403: LLMAuthenticationError, 404: LLMModelNotFoundError,
                  429: LLMRateLimitError, 503: LLMProviderUnavailable}
        # Do not forward provider bodies/URLs, which can echo user text or keys.
        raise errors.get(response.status_code, LLMResponseError)(provider=self.provider_name)

    @classmethod
    def _explicit_pricing(cls, item: dict) -> tuple[str, str] | None:
        """Parse provider pricing only when the response explicitly supplies it."""

        state = item.get("pricing_state") or item.get("pricingState")
        if isinstance(state, str) and state.upper() in {"FREE", "PAID", "UNKNOWN"}:
            normalized = state.upper()
            if normalized != "UNKNOWN":
                return normalized, "Gemini live model metadata"

        pricing = item.get("pricing")
        if not isinstance(pricing, dict):
            return None
        values = [pricing.get(key) for key in ("prompt", "input", "completion", "output", "request") if key in pricing]
        if not values:
            return None
        try:
            prices = [Decimal(str(value)) for value in values]
        except (InvalidOperation, TypeError, ValueError):
            return "UNKNOWN", "Gemini live model metadata pricing is malformed"
        if any(not price.is_finite() or price < 0 for price in prices):
            return "UNKNOWN", "Gemini live model metadata pricing is invalid"
        if all(price == 0 for price in prices):
            return "FREE", "Gemini live model metadata"
        return "PAID", "Gemini live model metadata"

    def _pricing(self, item: dict, model: str) -> tuple[str, str, str]:
        explicit = self._explicit_pricing(item)
        if explicit is not None:
            state, source = explicit
            return state, source, "Provider metadata is authoritative for explicit pricing."
        if model in self.free_model_allowlist:
            return (
                "FREE",
                f"Google Gemini pricing documentation ({self.pricing_documentation})",
                "Exact model ID is in the configured official Free Tier allowlist and was confirmed by live discovery.",
            )
        return (
            "UNKNOWN",
            "Gemini models.list",
            "Live model metadata does not establish free-tier eligibility and this model is not allowlisted.",
        )

    async def discover_models(self):
        if not self.configured: return []
        models, page = [], None
        try:
            async with self._client(5) as client:
                for _ in range(20):
                    params = {"pageSize": 1000}
                    if page: params["pageToken"] = page
                    response = await client.get(self.base_url + "/models", headers=self._headers(), params=params)
                    self._status(response)
                    payload = response.json()
                    if not isinstance(payload.get("models"), list): raise ValueError("models")
                    for item in payload["models"]:
                        name = item.get("name", "").removeprefix("models/")
                        if not re.fullmatch(r"[A-Za-z0-9._-]+", name): continue
                        methods = item.get("supportedGenerationMethods", [])
                        generates = "generateContent" in methods
                        raw_context_length = item.get("inputTokenLimit")
                        context_length = raw_context_length if isinstance(raw_context_length, int) and not isinstance(raw_context_length, bool) and raw_context_length >= 1 else None
                        pricing_state, pricing_source, detail = self._pricing(item, name)
                        models.append(ModelDescriptor(provider="gemini", model=name,
                            name=item.get("displayName", name), availability="AVAILABLE",
                            capabilities=ModelCapabilities(text=True if generates else None,
                                streaming=True if generates else None, reasoning=item.get("thinking"),
                                context_length=context_length),
                            supported_methods=methods, output_limit=item.get("outputTokenLimit"),
                            last_checked=time.time(), pricing_state=pricing_state, pricing_source=pricing_source,
                            detail=detail))
                    page = payload.get("nextPageToken")
                    if not page: return models
                raise ValueError("pagination")
        except httpx.TimeoutException as exc: raise LLMTimeoutError(provider="gemini") from exc
        except httpx.HTTPError as exc: raise LLMProviderUnavailable(provider="gemini") from exc
        except (ValueError, TypeError, AttributeError) as exc: raise LLMResponseError(provider="gemini") from exc

    async def health_check(self):
        if not self.configured:
            return ProviderHealth("gemini", self.model_name, False, False, detail="Gemini API key is not configured or disabled")
        try:
            models = await self.discover_models()
            available = any(m.model == self.model_name for m in models) if self.model_name else bool(models)
            return ProviderHealth("gemini", self.model_name, True, available, True, available,
                "Account model listing verified; pricing remains UNKNOWN")
        except (LLMProviderUnavailable, LLMAuthenticationError, LLMResponseError, LLMTimeoutError, LLMRateLimitError):
            return ProviderHealth("gemini", self.model_name, True, False, detail="Gemini discovery unavailable")

    async def is_available(self): return (await self.health_check()).available

    async def generate(self, messages, **options):
        return "".join([chunk async for chunk in self.generate_stream(messages, **options)])

    async def generate_structured(self, messages, schema, *, max_tokens=1400):
        return await self.generate(messages, temperature=0, max_tokens=max_tokens, response_schema=schema)

    async def generate_stream(self, messages, *, temperature=.7, max_tokens=None, think=None,
                              tools=None, response_schema=None) -> AsyncIterator[str]:
        if not self.configured: raise LLMAuthenticationError(provider="gemini")
        if self.free_only and self.pricing_state != "FREE":
            raise ModelPolicyError("Gemini model pricing is not verified as free.", provider="gemini")
        if not re.fullmatch(r"[A-Za-z0-9._-]+", self.model_name): raise LLMModelNotFoundError(provider="gemini")
        payload = {"contents": [], "generationConfig": {"temperature": temperature}}
        system = []
        for message in messages:
            if message["role"] == "system": system.append(message["content"])
            else: payload["contents"].append({"role": "model" if message["role"] == "assistant" else "user",
                                             "parts": [{"text": message["content"]}]})
        if system: payload["systemInstruction"] = {"parts": [{"text": "\n".join(system)}]}
        if max_tokens: payload["generationConfig"]["maxOutputTokens"] = max_tokens
        if response_schema is not None:
            payload["generationConfig"].update(responseMimeType="application/json", responseJsonSchema=response_schema)
        if tools:
            payload["tools"] = [{"functionDeclarations": [tool.get("function", tool) for tool in tools]}]
        # Thinking configuration differs by model; never invent a budget switch.
        answer, emitted = AnswerStreamFilter(), False
        try:
            async with self._client() as client:
                async with client.stream("POST", f"{self.base_url}/models/{self.model_name}:streamGenerateContent",
                    headers=self._headers(), params={"alt": "sse"}, json=payload) as response:
                    if response.status_code == 404:
                        # Gemini's live model metadata currently advertises
                        # generateContent while omitting streamGenerateContent.
                        # A non-stream response is still yielded through the
                        # provider stream contract; no second generation is
                        # attempted after a successful response.
                        fallback = await client.post(
                            f"{self.base_url}/models/{self.model_name}:generateContent",
                            headers=self._headers(), json=payload,
                        )
                        self._status(fallback)
                        data = fallback.json()
                        if not isinstance(data, dict) or data.get("promptFeedback", {}).get("blockReason"):
                            raise LLMResponseError("Gemini could not return an answer.", provider="gemini")
                        candidates = data.get("candidates", [])
                        if not candidates:
                            raise LLMResponseError("Gemini returned no final answer.", provider="gemini")
                        for part in candidates[0].get("content", {}).get("parts", []):
                            if part.get("thought") or not isinstance(part.get("text"), str):
                                continue
                            visible = answer.feed(part["text"])
                            if visible:
                                emitted = emitted or bool(visible.strip())
                                yield visible
                        if not emitted:
                            raise LLMResponseError("Gemini returned no final answer.", provider="gemini")
                        return
                    self._status(response)
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"): continue
                        data = json.loads(line[5:].strip())
                        if not isinstance(data, dict): raise ValueError("event")
                        if data.get("error") or data.get("promptFeedback", {}).get("blockReason"):
                            raise LLMResponseError("Gemini could not return an answer.", provider="gemini")
                        candidates = data.get("candidates", [])
                        if not candidates: continue
                        candidate = candidates[0]
                        if candidate.get("finishReason") not in (None, "STOP", "MAX_TOKENS"):
                            raise LLMResponseError("Gemini stopped without a usable answer.", provider="gemini")
                        for part in candidate.get("content", {}).get("parts", []):
                            if part.get("thought") or not isinstance(part.get("text"), str): continue
                            visible = answer.feed(part["text"])
                            if visible:
                                emitted = emitted or bool(visible.strip())
                                yield visible
            if not emitted: raise LLMResponseError("Gemini returned no final answer.", provider="gemini")
        except httpx.TimeoutException as exc: raise LLMTimeoutError(provider="gemini") from exc
        except httpx.HTTPError as exc: raise LLMProviderUnavailable(provider="gemini") from exc
        except (ValueError, TypeError, AttributeError, IndexError) as exc: raise LLMResponseError(provider="gemini") from exc
