"""Minimal conversation agent with provider selection and streaming."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import aclosing
from dataclasses import dataclass
from typing import Literal

from app.agent.context import ConversationContext
from app.agent.thinking import ThinkingDecision, classify_thinking
from app.core.config import Settings
from app.llm.base import LLMProvider
from app.llm.errors import FreeProvidersUnavailableError, LLMError, LLMRateLimitError, LLMResponseError
from app.llm.router import ProviderRouter


AgentEventType = Literal["llm.started", "llm.token", "llm.completed"]


@dataclass(slots=True)
class AgentStreamEvent:
    type: AgentEventType
    provider: str
    model: str
    content: str = ""
    thinking_enabled: bool = False
    thinking_reason: str = ""
    ttft_ms: float | None = None
    total_generation_latency_ms: float | None = None
    token_count: int = 0


class Agent:
    """Conversation-only agent designed to grow into tool orchestration later."""

    def __init__(self, router: ProviderRouter, settings: Settings, *, context: ConversationContext | None = None) -> None:
        self.router = router
        self.settings = settings
        self.context = context or ConversationContext(history_limit=settings.conversation_history_limit)

    async def _stream_with_provider(
        self,
        provider: LLMProvider,
        messages: list[dict[str, str]],
        decision: ThinkingDecision,
    ) -> AsyncIterator[str]:
        async with aclosing(provider.generate_stream(
            messages,
            temperature=self.settings.llm_temperature,
            max_tokens=decision.max_tokens,
            think=decision.enabled,
        )) as stream:
            async for token in stream:
                yield token

    async def stream_message(
        self,
        user_content: str,
        *,
        mode: str = "normal",
        memory_context: str | None = None,
    ) -> AsyncIterator[AgentStreamEvent]:
        """Yield provider metadata, tokens, then a completed response."""

        normalized = user_content.strip()
        if not normalized:
            raise ValueError("Message content cannot be empty")

        messages = self.context.messages_for(normalized, memory_context=memory_context)
        selection = await self.router.select(mode, request=normalized)
        decision = classify_thinking(
            normalized,
            mode=self.settings.llm_thinking_mode,
            default=self.settings.llm_thinking_default,
            simple_max_tokens=self.settings.llm_simple_max_tokens,
            normal_max_tokens=self.settings.llm_max_tokens,
            complex_max_tokens=self.settings.llm_complex_max_tokens,
            requested_mode=mode,
        )
        # Includes the initial provider attempt; free failover is bounded to
        # the router's small total-attempt limit.
        fallback_attempts = 1
        rate_limited_failures = 0

        while True:
            provider = selection.provider
            yield AgentStreamEvent(
                "llm.started", provider.provider_name, provider.model_name,
                thinking_enabled=decision.enabled, thinking_reason=decision.reason,
            )
            response_parts: list[str] = []
            started_at = time.perf_counter()
            first_token_at: float | None = None
            try:
                async with aclosing(self._stream_with_provider(provider, messages, decision)) as stream:
                    async for token in stream:
                        if first_token_at is None:
                            first_token_at = time.perf_counter()
                        response_parts.append(token)
                        yield AgentStreamEvent(
                            "llm.token", provider.provider_name, provider.model_name, token,
                            thinking_enabled=decision.enabled, thinking_reason=decision.reason,
                        )
                generation_finished_at = time.perf_counter()
                response = "".join(response_parts)
                if not response.strip():
                    raise LLMResponseError(
                        "The model finished without an answer. Retry with a shorter request or enable a larger complex-request budget.",
                        provider=provider.provider_name,
                    )
                self.context.commit(normalized, response)
                yield AgentStreamEvent(
                    "llm.completed", provider.provider_name, provider.model_name, response,
                    thinking_enabled=decision.enabled, thinking_reason=decision.reason,
                    ttft_ms=round((first_token_at - started_at) * 1000, 2) if first_token_at else None,
                    total_generation_latency_ms=round((generation_finished_at - started_at) * 1000, 2),
                    token_count=len(response_parts),
                )
                self.router.record_latency(provider.provider_name, provider.model_name,
                    ttft_ms=round((first_token_at - started_at) * 1000, 2) if first_token_at else None,
                    generation_ms=round((generation_finished_at - started_at) * 1000, 2))
                return
            except LLMError as error:
                self.router.record_latency(provider.provider_name, provider.model_name, ttft_ms=None,
                    generation_ms=(time.perf_counter() - started_at) * 1000, success=False)
                self.router.record_failure(provider.provider_name, provider.model_name, error)
                if isinstance(error, LLMRateLimitError):
                    rate_limited_failures += 1
                if response_parts or fallback_attempts >= self.router.max_free_attempts:
                    if rate_limited_failures and not response_parts:
                        raise FreeProvidersUnavailableError(
                            "All eligible free AI providers are temporarily unavailable. Try again shortly.",
                            provider=provider.provider_name,
                        ) from error
                    raise
                fallback = await self.router.fallback(provider)
                if fallback is None:
                    if rate_limited_failures:
                        raise FreeProvidersUnavailableError(
                            "All eligible free AI providers are temporarily unavailable. Try again shortly.",
                            provider=provider.provider_name,
                        ) from error
                    raise
                if isinstance(error, LLMRateLimitError):
                    if fallback.provider.provider_name == provider.provider_name:
                        await asyncio.sleep(self.router.rate_limit_backoff(rate_limited_failures))
                selection = fallback
                fallback_attempts += 1
