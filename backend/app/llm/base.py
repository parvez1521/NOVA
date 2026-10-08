"""Provider-neutral asynchronous LLM contracts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any

from app.llm.models import ModelCapabilities, ModelDescriptor


ChatMessage = dict[str, str]


@dataclass(slots=True)
class ProviderHealth:
    provider: str
    model: str
    configured: bool
    available: bool
    server_reachable: bool | None = None
    model_available: bool | None = None
    detail: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "model": self.model,
            "configured": self.configured,
            "available": self.available,
            "server_reachable": self.server_reachable,
            "model_available": self.model_available,
            "detail": self.detail,
        }


class LLMProvider(ABC):
    """Stable interface consumed by the agent and router."""

    provider_name: str
    model_name: str

    async def discover_models(self) -> list[ModelDescriptor]:
        """Compatibility for custom providers. No guessed advanced abilities."""
        health = await self.health_check()
        return [ModelDescriptor(provider=self.provider_name, model=self.model_name,
            capabilities=ModelCapabilities(text=True, streaming=True),
            availability="AVAILABLE" if health.available else "UNAVAILABLE")]

    @abstractmethod
    async def is_available(self) -> bool:
        """Return whether this provider can serve a request now."""

    @abstractmethod
    async def health_check(self) -> ProviderHealth:
        """Return structured provider health without leaking secrets."""

    @abstractmethod
    async def generate(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        think: bool | None = None,
        tools: Sequence[dict[str, Any]] | None = None,
    ) -> str:
        """Generate a complete response."""

    @abstractmethod
    async def generate_stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        think: bool | None = None,
        tools: Sequence[dict[str, Any]] | None = None,
    ) -> AsyncIterator[str]:
        """Yield response content as soon as chunks arrive."""

    async def healthcheck(self) -> ProviderHealth:
        """Compatibility alias retained for the Phase 1 provider contract."""

        return await self.health_check()

    async def generate_structured(self, messages: Sequence[ChatMessage], schema: dict[str, Any], *, max_tokens: int = 1400) -> str:
        """Optional structured planning output; standard chat streaming is unchanged."""
        return await self.generate(messages, temperature=0, max_tokens=max_tokens, think=False)

    async def stream_chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        think: bool | None = None,
        tools: Sequence[dict[str, Any]] | None = None,
    ) -> AsyncIterator[str]:
        """Compatibility alias for callers using the original method name."""

        async for chunk in self.generate_stream(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            think=think,
            tools=tools,
        ):
            yield chunk
