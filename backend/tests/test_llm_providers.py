import json

import httpx
import pytest

from app.core.config import Settings
from app.llm.errors import LLMRateLimitError, LLMResponseError, NoLLMProviderError
from app.llm.gemini import GoogleGeminiProvider
from app.llm.ollama import OllamaProvider
from app.llm.openrouter import OpenRouterProvider
from app.llm.openrouter import OpenRouterFreeModelCatalog
from app.llm.router import ProviderRouter


@pytest.mark.asyncio
async def test_ollama_health_and_streaming_parser() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3:4b"}]})
        assert request.url.path == "/api/chat"
        body = json.loads(request.read().decode())
        assert body["stream"] is True
        return httpx.Response(
            200,
            content=(
                '{"message":{"content":"Hello"},"done":false}\n'
                '{"message":{"content":" bhai"},"done":false}\n'
                '{"done":true}\n'
            ).encode(),
            headers={"content-type": "application/x-ndjson"},
        )

    provider = OllamaProvider(
        "http://ollama.test",
        "qwen3:4b",
        transport=httpx.MockTransport(handler),
    )

    health = await provider.health_check()
    tokens = [token async for token in provider.generate_stream([{"role": "user", "content": "Hello"}])]

    assert health.available is True
    assert health.model_available is True
    assert tokens == ["Hello", " bhai"]


@pytest.mark.asyncio
async def test_ollama_missing_model_is_reported_without_raising() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": []})

    provider = OllamaProvider("http://ollama.test", "qwen3:4b", transport=httpx.MockTransport(handler))
    health = await provider.health_check()

    assert health.available is False
    assert health.server_reachable is True
    assert health.model_available is False
    assert "not installed" in (health.detail or "")


@pytest.mark.asyncio
async def test_ollama_server_down_is_reported_without_raising() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    provider = OllamaProvider("http://ollama.test", "qwen3:4b", transport=httpx.MockTransport(handler))
    health = await provider.health_check()

    assert health.available is False
    assert health.server_reachable is False
    assert "not running" in (health.detail or "")


@pytest.mark.asyncio
async def test_openrouter_sse_streaming_parser() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer test-key"
        return httpx.Response(
            200,
            content=(
                'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
                'data: {"choices":[{"delta":{"content":" from fallback"}}]}\n\n'
                "data: [DONE]\n\n"
            ).encode(),
            headers={"content-type": "text/event-stream"},
        )

    provider = OpenRouterProvider(
        "test-key",
        "openrouter/free",
        transport=httpx.MockTransport(handler),
    )
    health = await provider.health_check()
    tokens = [token async for token in provider.generate_stream([{"role": "user", "content": "Hello"}])]

    assert (await provider.is_available()) is True
    assert health.server_reachable is None
    assert tokens == ["Hello", " from fallback"]


@pytest.mark.asyncio
async def test_malformed_stream_is_normalized() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json\n")

    provider = OllamaProvider("http://ollama.test", "qwen3:4b", transport=httpx.MockTransport(handler))

    with pytest.raises(LLMResponseError) as error:
        _ = [token async for token in provider.generate_stream([])]

    assert error.value.code == "LLM_RESPONSE_ERROR"


@pytest.mark.asyncio
async def test_gemini_discovery_and_sse_stream() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"models": [{
                "name": "models/gemini-test", "displayName": "Gemini Test",
                "supportedGenerationMethods": ["generateContent"], "inputTokenLimit": 8192,
                "outputTokenLimit": 1024, "thinking": True,
            }]})
        assert request.url.path.endswith(":streamGenerateContent")
        assert request.headers["x-goog-api-key"] == "gemini-key"
        return httpx.Response(200, content=b'data: {"candidates":[{"content":{"parts":[{"text":"Hello Gemini"}]},"finishReason":"STOP"}]}\n\n')

    provider = GoogleGeminiProvider("gemini-key", "gemini-test", free_only=False, transport=httpx.MockTransport(handler))
    models = await provider.discover_models()
    assert models[0].pricing_state == "UNKNOWN"
    assert models[0].capabilities.reasoning is True
    assert "Hello Gemini" == await provider.generate([])


@pytest.mark.asyncio
async def test_gemini_falls_back_to_generate_content_when_stream_endpoint_is_unavailable() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"models": [{
                "name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"],
                "inputTokenLimit": 8192,
            }]})
        if request.url.path.endswith(":streamGenerateContent"):
            return httpx.Response(404, json={"error": {"status": "NOT_FOUND"}})
        assert request.url.path.endswith(":generateContent")
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "Hello fallback"}]}}]})

    provider = GoogleGeminiProvider(
        "gemini-key", "gemini-2.5-flash", free_only=True,
        pricing_state="FREE", free_model_allowlist=("gemini-2.5-flash",),
        transport=httpx.MockTransport(handler),
    )
    assert await provider.generate([]) == "Hello fallback"


@pytest.mark.asyncio
async def test_gemini_free_allowlist_is_exact_and_policy_gated() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/models")
        return httpx.Response(200, json={"models": [
            {"name": "models/gemini-2.5-flash", "displayName": "Gemini Flash", "supportedGenerationMethods": ["generateContent"], "inputTokenLimit": 8192},
            {"name": "models/gemini-2.5-flash-lite", "displayName": "Gemini Flash-Lite", "supportedGenerationMethods": ["generateContent"], "inputTokenLimit": 8192},
            {"name": "models/gemini-2.5-pro", "displayName": "Gemini Pro", "supportedGenerationMethods": ["generateContent"], "inputTokenLimit": 8192,
             "pricing": {"prompt": "0.000001", "completion": "0.000002"}},
        ]})

    provider = GoogleGeminiProvider(
        "gemini-key",
        free_only=True,
        free_model_allowlist=("gemini-2.5-flash", "gemini-2.5-flash-lite"),
        transport=httpx.MockTransport(handler),
    )
    models = await provider.discover_models()

    assert {model.model for model in models if model.pricing_state == "FREE"} == {
        "gemini-2.5-flash", "gemini-2.5-flash-lite",
    }
    paid = next(model for model in models if model.model == "gemini-2.5-pro")
    assert paid.pricing_state == "PAID"
    assert paid.model not in {model.model for model in models if model.pricing_state == "FREE"}

    settings = Settings(
        _env_file=None,
        gemini_api_key="test-key",
        gemini_free_model_allowlist="gemini-2.5-flash,gemini-2.5-flash-lite",
        model_routing_mode="BEST_AVAILABLE",
        privacy_mode="CLOUD_ALLOWED",
        preferred_cloud_model="gemini-2.5-flash",
        free_only=True,
        zero_budget_mode=True,
    )
    router = ProviderRouter(settings, providers={"gemini": provider})
    selection = await router.select(request="Answer plainly")
    assert selection.descriptor is not None
    assert selection.descriptor.model == "gemini-2.5-flash"
    assert selection.descriptor.pricing_state == "FREE"

    async def paid_only_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/models")
        return httpx.Response(200, json={"models": [{
            "name": "models/gemini-2.5-pro", "supportedGenerationMethods": ["generateContent"],
            "inputTokenLimit": 8192, "pricing": {"prompt": "0.000001", "completion": "0.000002"},
        }]})

    paid_provider = GoogleGeminiProvider(
        "gemini-key", free_only=True, free_model_allowlist=("gemini-2.5-flash",),
        transport=httpx.MockTransport(paid_only_handler),
    )
    paid_router = ProviderRouter(settings, providers={"gemini": paid_provider})
    with pytest.raises(NoLLMProviderError):
        await paid_router.select(request="Answer plainly")


@pytest.mark.asyncio
async def test_openrouter_free_router_is_catalog_backed_and_rate_limits_are_classified(tmp_path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"data": [
                {"id": "free/text", "name": "Free Text", "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]}, "pricing": {"prompt": "0", "completion": "0"}},
                {"id": "paid/text", "name": "Paid Text", "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]}, "pricing": {"prompt": "0.000001", "completion": "0.000002"}},
            ], "links": {"next": None}})
        assert request.method == "POST"
        body = json.loads(request.content)
        assert body["model"] == "free/text"
        return httpx.Response(429, json={"error": {"message": "rate limited"}})

    catalog = OpenRouterFreeModelCatalog("test-key", cache_path=tmp_path / "models.json", transport=httpx.MockTransport(handler))
    models = await catalog.discover(force=True)
    assert any(model.model == "openrouter/free" and model.pricing_state == "FREE" for model in models)

    provider = OpenRouterProvider("test-key", "free/text", free_only=True, catalog=catalog, transport=httpx.MockTransport(handler))
    with pytest.raises(LLMRateLimitError) as error:
        _ = [token async for token in provider.generate_stream([{"role": "user", "content": "Hello"}])]
    assert error.value.code == "LLM_RATE_LIMIT"


@pytest.mark.asyncio
async def test_openrouter_catalog_records_current_pricing_and_capabilities(tmp_path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/models"
        return httpx.Response(200, json={"data": [
            {"id": "free/text", "name": "Free Text", "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]}, "pricing": {"prompt": "0", "completion": "0", "request": "0"}, "supported_parameters": ["tools"]},
            {"id": "paid/vision", "name": "Paid Vision", "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["text"]}, "pricing": {"prompt": "0.000001", "completion": "0.000002"}, "supported_parameters": ["response_format"], "context_length": 32000},
            {"id": "unknown/context", "name": "Unknown Context", "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]}, "pricing": {"prompt": "0", "completion": "0"}, "context_length": 0},
        ], "links": {"next": None}})

    catalog = OpenRouterFreeModelCatalog("test-key", cache_path=tmp_path / "models.json", transport=httpx.MockTransport(handler))
    models = await catalog.discover()
    assert {model.pricing_state for model in models} == {"FREE", "PAID"}
    vision = next(model for model in models if model.model == "paid/vision")
    assert vision.capabilities.vision is True and vision.capabilities.context_length == 32000
    unknown_context = next(model for model in models if model.model == "unknown/context")
    assert unknown_context.capabilities.context_length is None
