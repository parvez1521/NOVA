"""Capability, privacy, pricing and latency-aware model routing."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import time

from app.core.config import Settings
from app.llm.base import LLMProvider, ProviderHealth
from app.llm.errors import FreeProvidersUnavailableError, LLMModelNotFoundError, LLMRateLimitError, NoLLMProviderError
from app.llm.gemini import GoogleGeminiProvider
from app.llm.models import (LatencyStats, ModelDescriptor, ModelRegistry, detect_task,
    task_requirements)
from app.llm.ollama import OllamaProvider
from app.llm.openrouter import OpenRouterFreeModelCatalog, OpenRouterProvider


@dataclass(slots=True)
class ProviderSelection:
    provider: LLMProvider
    health: ProviderHealth
    descriptor: ModelDescriptor | None = None
    fallback_chain: list[ModelDescriptor] = field(default_factory=list)
    reason: str = ""


class ProviderRouter:
    """Choose exactly one suitable model per request and retain safe fallbacks."""

    max_free_attempts = 3
    rate_limit_cooldown_seconds = 30.0

    def __init__(self, settings: Settings, *, providers: dict[str, LLMProvider] | None = None) -> None:
        self.settings = settings
        self.registry = ModelRegistry()
        self.latency = LatencyStats()
        self._last_refresh = 0.0
        self._selection_chains: dict[str, list[ModelDescriptor]] = {}
        self._temporary_unavailable: dict[str, tuple[float, str]] = {}
        self._custom_providers = providers is not None
        if providers is not None:
            self.providers = providers
            self.catalog = None
        else:
            catalog = OpenRouterFreeModelCatalog(
                settings.openrouter_api_key.get_secret_value() if settings.openrouter_api_key else None,
                cache_path=Path(settings.project_root) / "backend/data/openrouter-models.json",
                timeout=min(settings.llm_request_timeout, 15),
            )
            self.catalog = catalog
            self.providers = {
                "ollama": OllamaProvider(settings.ollama_base_url, settings.ollama_model,
                    enabled=settings.ollama_enabled, timeout=settings.llm_request_timeout),
                "openrouter": OpenRouterProvider(
                    settings.openrouter_api_key.get_secret_value() if settings.openrouter_api_key else None,
                    settings.openrouter_model, enabled=settings.openrouter_enabled,
                    timeout=settings.llm_request_timeout, free_only=settings.free_only or settings.zero_budget_mode,
                    catalog=catalog),
                "gemini": GoogleGeminiProvider(
                    settings.gemini_api_key.get_secret_value() if settings.gemini_api_key else None,
                    settings.gemini_model, enabled=settings.gemini_enabled,
                    timeout=settings.llm_request_timeout, free_only=settings.free_only or settings.zero_budget_mode,
                    free_model_allowlist=settings.gemini_free_model_ids),
            }

    async def refresh_models(self, *, force: bool = False) -> list[ModelDescriptor]:
        import time
        if not force and self._last_refresh and time.monotonic() - self._last_refresh < 30:
            return self.registry.all()
        for name, provider in self.providers.items():
            try:
                discovered = await provider.discover_models()
            except Exception:
                discovered = []
            for descriptor in discovered:
                if name == "ollama":
                    descriptor.local = True
                    descriptor.pricing_state = "LOCAL_FREE"
                elif name == "openrouter" and descriptor.model == "openrouter/free" and descriptor.pricing_state == "UNKNOWN":
                    # This is OpenRouter's documented free-router route, not a
                    # guessed property of arbitrary catalog models.
                    descriptor.pricing_state = "FREE"
            if not discovered:
                # Keep injected test providers and configured special free route usable.
                try:
                    health = await provider.health_check()
                except Exception:
                    continue
                if health.configured or health.available:
                    pricing = "LOCAL_FREE" if name == "ollama" else "FREE" if provider.model_name == "openrouter/free" else "UNKNOWN"
                    from app.llm.models import ModelCapabilities
                    discovered = [ModelDescriptor(provider=name, model=provider.model_name,
                        capabilities=ModelCapabilities(text=True, streaming=True),
                        availability="AVAILABLE" if health.available else "UNAVAILABLE",
                        pricing_state=pricing, local=name == "ollama", detail="Provider metadata fallback")]
            self.registry.replace_provider(name, discovered)
        import time
        self._last_refresh = time.monotonic()
        return self.registry.all()

    def invalidate(self) -> None:
        self._last_refresh = 0.0

    def _temporarily_unavailable(self, key: str) -> bool:
        state = self._temporary_unavailable.get(key)
        if state is None:
            return False
        until, _ = state
        if until <= time.monotonic():
            self._temporary_unavailable.pop(key, None)
            return False
        return True

    def record_failure(self, provider: str, model: str, error: Exception) -> None:
        """Record bounded provider cooldowns without retaining prompts or secrets."""

        key = f"{provider}:{model}"
        self.latency.fail(key)
        if isinstance(error, LLMRateLimitError):
            self._temporary_unavailable[key] = (
                time.monotonic() + self.rate_limit_cooldown_seconds,
                "RATE_LIMITED",
            )

    def rate_limit_backoff(self, attempt: int) -> float:
        """Return a small bounded delay only when moving between same-provider models."""

        return min(0.25 * (2 ** max(attempt - 1, 0)), 1.0)

    def runtime_status(self, provider: str) -> dict[str, object]:
        now = time.monotonic()
        rate_limited: list[dict[str, object]] = []
        for key, (until, reason) in list(self._temporary_unavailable.items()):
            if until <= now:
                self._temporary_unavailable.pop(key, None)
                continue
            name, _, model = key.partition(":")
            if name == provider:
                rate_limited.append({"model": model, "state": reason, "retry_after_seconds": round(until - now, 1)})
        return {
            "status": "RATE_LIMITED" if rate_limited else "AVAILABLE",
            "rate_limited_models": rate_limited,
        }

    async def health_snapshot(self) -> dict[str, object]:
        await self.refresh_models()
        health: dict[str, object] = {}
        for name, provider in self.providers.items():
            health[name] = (await provider.health_check()).as_dict()
        health["routing_policy"] = self.settings.llm_routing_policy
        health["routing_mode"] = self._effective_mode()
        health["free_only"] = self.settings.free_only
        health["zero_budget_mode"] = self.settings.zero_budget_mode
        health["models"] = self.registry.snapshot()
        health["latency"] = {descriptor.key: self.latency.summary(descriptor.key) for descriptor in self.registry.all()}
        return health

    def _effective_mode(self) -> str:
        legacy = self.settings.llm_routing_policy.casefold()
        if legacy == "local_only": return "LOCAL_ONLY"
        if legacy == "online_only": return "BEST_AVAILABLE"
        return self.settings.model_routing_mode

    def _allowed(self, descriptor: ModelDescriptor, *, mode: str, privacy: str) -> bool:
        if descriptor.availability != "AVAILABLE" or not self.latency.healthy(descriptor.key): return False
        if self._temporarily_unavailable(descriptor.key): return False
        if mode in {"LOCAL_ONLY", "OFFLINE"} or privacy == "STRICT_LOCAL":
            if not descriptor.local: return False
        if mode == "BEST_AVAILABLE" and self.settings.llm_routing_policy.casefold() == "online_only" and descriptor.local:
            return False
        if self.settings.zero_budget_mode or self.settings.free_only or mode == "FREE_ONLY":
            if descriptor.pricing_state not in {"LOCAL_FREE", "FREE"}: return False
        return True

    @staticmethod
    def _has_capabilities(descriptor: ModelDescriptor, required: set[str]) -> bool:
        return all(getattr(descriptor.capabilities, item, None) is True for item in required)

    def _score(self, descriptor: ModelDescriptor, *, mode: str, privacy: str, task: str) -> tuple:
        local_preference = 70 if descriptor.local and privacy == "LOCAL_FIRST" and mode not in {"BEST_AVAILABLE"} else 0
        free_bonus = 30 if descriptor.pricing_state in {"LOCAL_FREE", "FREE"} else 0
        preferred = 30 if descriptor.model in {self.settings.preferred_local_model, self.settings.preferred_cloud_model} else 0
        task_bonus = 10 if task in {"reasoning", "coding"} and descriptor.capabilities.reasoning is True else 0
        quality = descriptor.quality or 0
        policy_order = 0
        if descriptor.provider == "gemini" and descriptor.model in self.settings.gemini_free_model_ids:
            policy_order = len(self.settings.gemini_free_model_ids) - self.settings.gemini_free_model_ids.index(descriptor.model)
        # latency bucket prevents one noisy request from constantly switching models.
        return (local_preference + free_bonus + preferred + task_bonus, policy_order, -self.latency.bucket(descriptor.key), quality,
                descriptor.capabilities.context_length or 0, descriptor.model)

    def _provider_for(self, descriptor: ModelDescriptor) -> LLMProvider:
        base = self.providers[descriptor.provider]
        if base.model_name == descriptor.model:
            if isinstance(base, OpenRouterProvider):
                base.pricing_state = descriptor.pricing_state
            if isinstance(base, GoogleGeminiProvider):
                base.free_only = self.settings.free_only or self.settings.zero_budget_mode
                base.pricing_state = descriptor.pricing_state
            return base
        if isinstance(base, OllamaProvider):
            return OllamaProvider(base.base_url, descriptor.model, enabled=base.enabled, timeout=base.timeout, transport=base.transport)
        if isinstance(base, OpenRouterProvider):
            return OpenRouterProvider(base.api_key, descriptor.model, enabled=base.enabled, timeout=base.timeout,
                transport=base.transport, pricing_state=descriptor.pricing_state,
                free_only=self.settings.free_only or self.settings.zero_budget_mode, catalog=base.catalog)
        if isinstance(base, GoogleGeminiProvider):
            return GoogleGeminiProvider(base.api_key, descriptor.model, enabled=base.enabled, timeout=base.timeout,
                free_only=self.settings.free_only or self.settings.zero_budget_mode, transport=base.transport,
                pricing_state=descriptor.pricing_state, free_model_allowlist=base.free_model_allowlist)
        return base

    async def select(self, mode: str = "normal", *, request: str = "", task_type: str | None = None,
                     required_capabilities: set[str] | None = None, privacy_mode: str | None = None) -> ProviderSelection:
        descriptors = await self.refresh_models()
        effective_mode = self._effective_mode()
        privacy = privacy_mode or self.settings.privacy_mode
        task = task_type or detect_task(request)
        if mode.casefold() == "complex" and not task_type:
            task = "reasoning"
        required = required_capabilities or task_requirements(task)  # type: ignore[arg-type]
        candidates = [item for item in descriptors if self._allowed(item, mode=effective_mode, privacy=privacy)
                      and self._has_capabilities(item, required)]
        if not candidates:
            raise NoLLMProviderError(f"No eligible model is available for task '{task}' under {effective_mode}, free-only={self.settings.free_only or self.settings.zero_budget_mode}.")
        candidates.sort(key=lambda item: self._score(item, mode=effective_mode, privacy=privacy, task=task), reverse=True)
        selected = candidates[0]
        provider = self._provider_for(selected)
        health = await provider.health_check()
        if not health.available:
            candidates = candidates[1:]
            if not candidates:
                raise NoLLMProviderError(f"Selected model '{selected.model}' became unavailable.")
            selected = candidates[0]
            provider = self._provider_for(selected)
            health = await provider.health_check()
        self._selection_chains[f"{provider.provider_name}:{provider.model_name}"] = candidates
        return ProviderSelection(provider, health, selected, candidates, f"task={task}; mode={effective_mode}; privacy={privacy}")

    async def select_provider(self, mode: str = "normal", **kwargs: Any) -> LLMProvider:
        return (await self.select(mode, **kwargs)).provider

    async def fallback(self, current: LLMProvider) -> ProviderSelection | None:
        chain = self._selection_chains.get(f"{current.provider_name}:{current.model_name}", [])
        for index, descriptor in enumerate(chain):
            if descriptor.provider == current.provider_name and descriptor.model == current.model_name:
                for next_descriptor in chain[index + 1:]:
                    if not self._allowed(next_descriptor, mode=self._effective_mode(), privacy=self.settings.privacy_mode):
                        continue
                    provider = self._provider_for(next_descriptor)
                    health = await provider.health_check()
                    if health.available:
                        return ProviderSelection(provider, health, next_descriptor, chain[index + 1:], "fallback")
        return None

    def record_latency(self, provider: str, model: str, *, ttft_ms: float | None, generation_ms: float, success: bool = True) -> None:
        key = f"{provider}:{model}"
        if success:
            self.latency.record(key, ttft_ms, generation_ms)
        else:
            self.latency.fail(key)

    async def live_test(self, provider_name: str, prompt: str) -> dict[str, object]:
        """Run one explicit provider test; policy and catalog gates still apply."""
        import time
        descriptors = await self.refresh_models(force=True)
        candidates = [item for item in descriptors if item.provider == provider_name and
                      item.availability == "AVAILABLE" and item.capabilities.text is True and
                      item.capabilities.streaming is True and
                      self._allowed(item, mode="BEST_AVAILABLE", privacy="CLOUD_ALLOWED")]
        if not candidates:
            raise NoLLMProviderError(f"{provider_name.title()} has no currently eligible model under the active pricing and privacy policy.")
        preferred = getattr(self.settings, f"{provider_name}_model", "")
        if not preferred and provider_name == "openrouter":
            preferred = "openrouter/free"
        gemini_order = {model: index for index, model in enumerate(self.settings.gemini_free_model_ids)}
        candidates.sort(key=lambda item: (
            item.model != preferred,
            gemini_order.get(item.model, len(gemini_order)) if provider_name == "gemini" else 0,
            item.model,
        ))
        rate_limited = False
        unavailable = False
        for selected in candidates[: self.max_free_attempts]:
            if self._temporarily_unavailable(selected.key):
                continue
            provider = self._provider_for(selected)
            started = time.perf_counter()
            try:
                response = await provider.generate([{"role": "user", "content": prompt}], temperature=0.2, max_tokens=256, think=False)
            except LLMRateLimitError as error:
                rate_limited = True
                self.record_failure(provider.provider_name, provider.model_name, error)
                continue
            except LLMModelNotFoundError as error:
                unavailable = True
                self.record_failure(provider.provider_name, provider.model_name, error)
                continue
            latency = round((time.perf_counter() - started) * 1000, 2)
            if not response.strip():
                raise NoLLMProviderError(f"{provider_name.title()} returned an empty response.")
            self.record_latency(provider.provider_name, provider.model_name, ttft_ms=None, generation_ms=latency)
            return {"provider": provider.provider_name, "model": provider.model_name, "response_preview": response[:500],
                    "response_non_empty": True, "latency_ms": latency, "pricing_state": selected.pricing_state,
                    "pricing_source": selected.pricing_source, "capabilities": selected.capabilities.model_dump()}
        if rate_limited:
            raise FreeProvidersUnavailableError(
                f"All eligible free {provider_name.title()} models are temporarily rate-limited. Try again shortly.",
                provider=provider_name,
            )
        if unavailable:
            raise NoLLMProviderError(f"No discovered {provider_name.title()} free model accepted a generation request.")
        raise NoLLMProviderError(f"{provider_name.title()} returned no eligible free response.")
