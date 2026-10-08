"""Safe model registry and routing settings endpoints."""

import time
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import Settings
from app.llm.router import ProviderRouter
from app.llm.errors import LLMError


class RoutingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["LOCAL_ONLY", "FREE_ONLY", "BALANCED", "BEST_AVAILABLE", "OFFLINE"] | None = None
    free_only: bool | None = None
    zero_budget: bool | None = None
    privacy_mode: Literal["LOCAL_FIRST", "STRICT_LOCAL", "CLOUD_ALLOWED"] | None = None
    jury_mode: bool | None = None
    preferred_local_model: str | None = Field(None, max_length=200)
    preferred_cloud_model: str | None = Field(None, max_length=200)


class LiveModelTest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["gemini", "openrouter"]
    prompt: str = Field("Hello Nova, who are you?", min_length=1, max_length=500)


def model_routes(router: ProviderRouter, settings: Settings) -> APIRouter:
    api = APIRouter(tags=["models"])

    def routing() -> dict[str, object]:
        return {
            "mode": settings.model_routing_mode,
            "free_only": settings.free_only,
            "zero_budget": settings.zero_budget_mode,
            "privacy_mode": settings.privacy_mode,
            "jury_mode": settings.jury_mode,
            "preferred_local_model": settings.preferred_local_model,
            "preferred_cloud_model": settings.preferred_cloud_model,
        }

    @api.get("/api/models")
    async def models(refresh: bool = False):
        descriptors = await router.refresh_models(force=refresh)
        return {"models": [item.model_dump() for item in descriptors], "routing": routing(), "activation": activation(descriptors)}

    @api.post("/api/models/refresh")
    async def refresh_models():
        descriptors = await router.refresh_models(force=True)
        return {"models": [item.model_dump() for item in descriptors], "routing": routing(), "activation": activation(descriptors)}

    @api.get("/api/routing")
    async def get_routing():
        return routing()

    def activation(descriptors) -> dict[str, object]:
        def provider_models(provider: str) -> list:
            return [item for item in descriptors if item.provider == provider and not item.local]

        gemini_models = provider_models("gemini")
        openrouter_models = provider_models("openrouter")
        gemini_configured = bool(settings.gemini_api_key and settings.gemini_api_key.get_secret_value())
        gemini_free = [item for item in gemini_models if item.availability == "AVAILABLE" and item.pricing_state == "FREE"]
        openrouter_free = [item for item in openrouter_models if item.availability == "AVAILABLE" and item.pricing_state == "FREE"]
        gemini_order = {model: index for index, model in enumerate(settings.gemini_free_model_ids)}
        gemini_free.sort(key=lambda item: gemini_order.get(item.model, len(gemini_order)))
        return {
            "gemini": {
                "configured": gemini_configured,
                "status": "CONNECTED" if gemini_configured and gemini_free else "FREE_POLICY_BLOCKED" if gemini_configured else "NOT_CONFIGURED",
                "model": settings.gemini_model or (gemini_free[0].model if gemini_free else "account-selected"),
                "free_eligible": bool(gemini_free),
                "free_eligible_count": len(gemini_free),
                "pricing_status": "VERIFIED_FREE" if gemini_free else "UNKNOWN_OR_BLOCKED",
                "pricing_sources": sorted({item.pricing_source for item in gemini_free if item.pricing_source}),
                "allowlist": list(settings.gemini_free_model_ids),
            },
            "openrouter": {
                "configured": settings.openrouter_configured,
                "status": router.runtime_status("openrouter")["status"] if settings.openrouter_configured else "NOT_CONFIGURED",
                "model": settings.openrouter_model or "openrouter/free",
                "free_eligible": bool(openrouter_free),
                "free_eligible_count": len(openrouter_free),
                "rate_limit": router.runtime_status("openrouter"),
                "pricing_status": "VERIFIED_FREE" if openrouter_free else "UNKNOWN_OR_BLOCKED",
            },
        }

    @api.post("/api/models/live-test")
    async def live_test(test: LiveModelTest):
        try:
            return await router.live_test(test.provider, test.prompt)
        except LLMError as error:
            from fastapi import HTTPException
            raise HTTPException(409, detail=error.user_message, headers={"X-NOVA-Error-Code": error.code}) from error

    @api.put("/api/routing")
    async def update_routing(update: RoutingUpdate):
        values = update.model_dump(exclude_none=True)
        mapping = {"mode": "model_routing_mode", "zero_budget": "zero_budget_mode"}
        for key, value in values.items():
            setattr(settings, mapping.get(key, key), value)
        for provider in router.providers.values():
            if hasattr(provider, "free_only"):
                provider.free_only = settings.free_only or settings.zero_budget_mode
        router.invalidate()
        return routing()

    return api
