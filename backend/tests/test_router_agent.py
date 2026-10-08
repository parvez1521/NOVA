from collections.abc import AsyncIterator, Sequence
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.agent.agent import Agent
from app.core.config import Settings
from app.llm.base import ChatMessage, LLMProvider, ProviderHealth
from app.llm.errors import FreeProvidersUnavailableError, LLMRateLimitError, NoLLMProviderError
from app.llm.models import ModelCapabilities, ModelDescriptor
from app.llm.router import ProviderRouter


class FakeProvider(LLMProvider):
    def __init__(self, name: str, model: str, available: bool, tokens: list[str] | None = None) -> None:
        self.provider_name = name
        self.model_name = model
        self.available = available
        self.tokens = tokens or ["Hello", " bhai"]

    async def is_available(self) -> bool:
        return self.available

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(self.provider_name, self.model_name, self.available, self.available, self.available, self.available)

    async def generate(self, messages: Sequence[ChatMessage], **_: Any) -> str:
        del messages
        return "".join(self.tokens)

    async def generate_stream(self, messages: Sequence[ChatMessage], **_: Any) -> AsyncIterator[str]:
        del messages
        for token in self.tokens:
            yield token


class ScriptedProvider(FakeProvider):
    def __init__(self, name: str, model: str, available: bool, *, local: bool = False,
                 pricing_state: str = "FREE", quality: float = 0, rate_limit_failures: int = 0,
                 tokens: list[str] | None = None) -> None:
        super().__init__(name, model, available, tokens)
        self.local = local
        self.pricing_state = pricing_state
        self.quality = quality
        self.rate_limit_failures = rate_limit_failures
        self.generate_calls = 0

    async def discover_models(self) -> list[ModelDescriptor]:
        return [ModelDescriptor(
            provider=self.provider_name,
            model=self.model_name,
            capabilities=ModelCapabilities(text=True, streaming=True),
            availability="AVAILABLE" if self.available else "UNAVAILABLE",
            pricing_state="LOCAL_FREE" if self.local else self.pricing_state,
            local=self.local,
            quality=self.quality,
        )]

    async def generate_stream(self, messages: Sequence[ChatMessage], **options: Any) -> AsyncIterator[str]:
        del messages, options
        self.generate_calls += 1
        if self.rate_limit_failures:
            self.rate_limit_failures -= 1
            raise LLMRateLimitError(provider=self.provider_name)
        for token in self.tokens:
            yield token


def make_settings(**overrides: Any) -> Settings:
    return Settings(
        _env_file=None,
        openrouter_api_key=SecretStr("test-key"),
        **overrides,
    )


@pytest.mark.asyncio
async def test_router_prefers_ollama() -> None:
    settings = make_settings(llm_routing_policy="local_first")
    local = FakeProvider("ollama", "qwen3:4b", True)
    fallback = FakeProvider("openrouter", "openrouter/free", True)
    router = ProviderRouter(settings, providers={"ollama": local, "openrouter": fallback})

    selection = await router.select()

    assert selection.provider is local


@pytest.mark.asyncio
async def test_router_falls_back_to_openrouter() -> None:
    settings = make_settings(llm_routing_policy="local_first")
    local = FakeProvider("ollama", "qwen3:4b", False)
    fallback = FakeProvider("openrouter", "openrouter/free", True)
    router = ProviderRouter(settings, providers={"ollama": local, "openrouter": fallback})

    selection = await router.select()

    assert selection.provider is fallback


@pytest.mark.asyncio
async def test_router_returns_structured_no_provider_error() -> None:
    settings = make_settings(llm_routing_policy="local_first")
    providers = {
        "ollama": FakeProvider("ollama", "qwen3:4b", False),
        "openrouter": FakeProvider("openrouter", "openrouter/free", False),
    }
    router = ProviderRouter(settings, providers=providers)

    with pytest.raises(NoLLMProviderError) as error:
        await router.select()

    assert error.value.code == "NO_LLM_PROVIDER"


@pytest.mark.asyncio
async def test_local_only_never_falls_back_online() -> None:
    settings = make_settings(llm_routing_policy="local_only")
    providers = {
        "ollama": FakeProvider("ollama", "qwen3:4b", False),
        "openrouter": FakeProvider("openrouter", "openrouter/free", True),
    }
    router = ProviderRouter(settings, providers=providers)

    with pytest.raises(NoLLMProviderError):
        await router.select()


@pytest.mark.asyncio
async def test_zero_budget_blocks_unknown_cloud_and_prefers_local() -> None:
    settings = make_settings(llm_routing_policy="local_first", free_only=True, zero_budget_mode=True)
    local = FakeProvider("ollama", "qwen3:4b", True)
    cloud = FakeProvider("gemini", "gemini-test", True)
    router = ProviderRouter(settings, providers={"ollama": local, "gemini": cloud})
    selection = await router.select(request="How are you?")
    assert selection.provider is local


@pytest.mark.asyncio
async def test_router_requires_vision_capability() -> None:
    settings = make_settings(free_only=False, zero_budget_mode=False)
    text = FakeProvider("ollama", "qwen3:4b", True)
    router = ProviderRouter(settings, providers={"ollama": text})
    with pytest.raises(NoLLMProviderError):
        await router.select(request="Analyze this screenshot", required_capabilities={"text", "vision"})


@pytest.mark.asyncio
async def test_rate_limited_cloud_falls_back_to_local_without_paid_selection() -> None:
    settings = make_settings(
        model_routing_mode="BEST_AVAILABLE",
        privacy_mode="CLOUD_ALLOWED",
        free_only=True,
        zero_budget=True,
    )
    local = ScriptedProvider("ollama", "local", True, local=True, quality=1, tokens=["local answer"])
    cloud = ScriptedProvider("openrouter", "free-cloud", True, quality=100, rate_limit_failures=1)
    router = ProviderRouter(settings, providers={"ollama": local, "openrouter": cloud})
    agent = Agent(router, settings)

    events = [event async for event in agent.stream_message("Hello Nova")]

    assert events[-1].provider == "ollama"
    assert events[-1].content == "local answer"
    assert cloud.generate_calls == 1
    assert local.generate_calls == 1


@pytest.mark.asyncio
async def test_rate_limit_fallback_tries_bounded_free_models() -> None:
    settings = make_settings(model_routing_mode="BEST_AVAILABLE", privacy_mode="CLOUD_ALLOWED", free_only=True, zero_budget=True)
    first = ScriptedProvider("free-a", "first-free", True, quality=100, rate_limit_failures=1)
    second = ScriptedProvider("free-b", "second-free", True, quality=90, tokens=["second answer"])
    router = ProviderRouter(settings, providers={"free-a": first, "free-b": second})
    agent = Agent(router, settings)

    events = [event async for event in agent.stream_message("Hello Nova")]

    assert events[-1].model == "second-free"
    assert first.generate_calls == 1
    assert second.generate_calls == 1


@pytest.mark.asyncio
async def test_all_rate_limited_free_models_return_bounded_user_error() -> None:
    settings = make_settings(model_routing_mode="BEST_AVAILABLE", privacy_mode="CLOUD_ALLOWED", free_only=True, zero_budget=True)
    provider = ScriptedProvider("openrouter", "free-cloud", True, rate_limit_failures=10)
    router = ProviderRouter(settings, providers={"openrouter": provider})
    agent = Agent(router, settings)

    with pytest.raises(FreeProvidersUnavailableError) as error:
        _ = [event async for event in agent.stream_message("Hello Nova")]

    assert error.value.code == "FREE_PROVIDERS_TEMPORARILY_UNAVAILABLE"
    assert provider.generate_calls == 1


@pytest.mark.asyncio
async def test_zero_budget_never_selects_paid_cloud_model() -> None:
    settings = make_settings(model_routing_mode="BEST_AVAILABLE", privacy_mode="CLOUD_ALLOWED", free_only=True, zero_budget=True)
    paid = ScriptedProvider("openrouter", "paid-model", True, pricing_state="PAID")
    router = ProviderRouter(settings, providers={"openrouter": paid})

    with pytest.raises(NoLLMProviderError):
        await router.select(request="Answer plainly")


def test_model_and_routing_api_are_sanitized() -> None:
    settings = make_settings()
    local = FakeProvider("ollama", "qwen3:4b", True)
    router = ProviderRouter(settings, providers={"ollama": local})
    from app.main import create_app
    with TestClient(create_app(settings, router=router)) as client:
        response = client.get("/api/models")
        assert response.status_code == 200
        assert response.json()["models"][0]["pricing_state"] == "LOCAL_FREE"
        assert response.json()["activation"]["gemini"]["configured"] is False
        assert "api_key" not in response.text
        assert client.post("/api/models/live-test", json={"provider": "gemini"}).status_code == 409
        update = client.put("/api/routing", json={"mode": "LOCAL_ONLY", "zero_budget": True})
        assert update.status_code == 200
        assert update.json()["mode"] == "LOCAL_ONLY"


@pytest.mark.asyncio
async def test_agent_streams_and_commits_bounded_context() -> None:
    settings = make_settings(conversation_history_limit=4)
    local = FakeProvider("ollama", "qwen3:4b", True, ["Hello", " NOVA"])
    fallback = FakeProvider("openrouter", "openrouter/free", False)
    router = ProviderRouter(settings, providers={"ollama": local, "openrouter": fallback})
    agent = Agent(router, settings)

    events = [event async for event in agent.stream_message("Hello Nova")]

    assert [event.type for event in events] == ["llm.started", "llm.token", "llm.token", "llm.completed"]
    assert events[-1].content == "Hello NOVA"
    assert agent.context.history == [
        {"role": "user", "content": "Hello Nova"},
        {"role": "assistant", "content": "Hello NOVA"},
    ]
