import json

import httpx
import pytest
from pydantic import ValidationError

from app.agent.agent import Agent
from app.agent.thinking import classify_thinking
from app.core.config import Settings
from app.llm.answer import AnswerStreamFilter
from app.llm.errors import LLMResponseError, LLMThinkingUnsupportedError
from app.llm.ollama import OllamaProvider
from app.llm.openrouter import OpenRouterProvider
from app.llm.router import ProviderRouter
from app.main import create_app
from fastapi.testclient import TestClient


@pytest.mark.parametrize("prompt", [
    "Hey Nova, who are you?", "How are you?", "Tell me a joke.", "What is 2 + 2?",
    "Explain what AI is.", "Good morning Nova.", "Open YouTube.", "What time is it?",
    "Chrome kholo.", "Hello Nova", "Nova, tell me a joke",
])
def test_smart_simple_requests_do_not_think(prompt):
    decision = classify_thinking(prompt)
    assert not decision.enabled
    assert decision.max_tokens in {256, 512}


@pytest.mark.parametrize("prompt", [
    "Analyze this code and find the bug.", "Compare these architectures.",
    "Plan a complex software project.", "Solve this difficult math problem.",
    "Reason through this complicated issue.", "Analyze this algorithm and explain its complexity.",
])
def test_smart_complex_requests_think(prompt):
    decision = classify_thinking(prompt)
    assert decision.enabled
    assert decision.max_tokens == 1024


@pytest.mark.parametrize("mode, expected", [("off", False), ("on", True)])
@pytest.mark.parametrize("prompt", ["Hello Nova", "Analyze this algorithm and explain its complexity"])
def test_forced_modes_override_heuristics(mode, expected, prompt):
    assert classify_thinking(prompt, mode=mode).enabled is expected


def test_thinking_configuration_and_budgets():
    settings = Settings(_env_file=None)
    assert settings.llm_thinking_mode == "smart"
    assert settings.llm_thinking_default is False
    assert (settings.llm_max_tokens, settings.llm_simple_max_tokens, settings.llm_complex_max_tokens) == (512, 256, 1024)
    decision = classify_thinking("Analyze the code", complex_max_tokens=777)
    assert decision.max_tokens == 777
    with pytest.raises(ValidationError):
        Settings(_env_file=None, llm_thinking_mode="maybe")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, llm_simple_max_tokens=0)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,expected,budget", [("off", False, 256), ("on", True, 1024)])
@pytest.mark.parametrize("prompt", ["Hey Nova, who are you?", "Analyze this algorithm and explain its complexity."])
async def test_forced_modes_reach_ollama_payload(mode, expected, budget, prompt):
    calls = []
    async def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3:4b-q4_K_M"}]})
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"thinking": {"values": [False, True]}})
        calls.append(json.loads(request.content))
        return httpx.Response(200, content=b'{"message":{"content":"A real answer."},"done":true}\n')
    settings = Settings(_env_file=None, llm_thinking_mode=mode, openrouter_enabled=False)
    provider = OllamaProvider("http://mock", "qwen3:4b-q4_K_M", transport=httpx.MockTransport(handler))
    agent = Agent(ProviderRouter(settings, providers={"ollama": provider}), settings)
    events = [event async for event in agent.stream_message(prompt)]
    assert calls[0]["think"] is expected
    assert calls[0]["options"]["num_predict"] == budget
    assert events[-1].content == "A real answer."


def test_answer_filter_blocks_split_inline_reasoning_without_delaying_plain_tokens():
    parser = AnswerStreamFilter()
    assert parser.feed("Hello") == "Hello"
    assert parser.feed("<thi") == ""
    assert parser.feed("nk>private reasoning") == ""
    assert parser.feed(" continues</thi") == ""
    assert parser.feed("nk>Final") == "Final"
    assert parser.feed(" answer.") == " answer."
    assert parser.flush() == ""


def transport_for_thinking(calls):
    async def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3:4b-q4_K_M"}]})
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"thinking": {"values": [False, True], "default": True}})
        body = json.loads(request.content)
        calls.append(body)
        # Reproduce the exact old failure only if the caller omitted/used true.
        if body.get("think") is not False:
            content = '{"message":{"thinking":"hidden reasoning","content":""},"done":false}\n{"done":true,"done_reason":"length"}\n'
        else:
            content = '{"message":{"content":"Hello"},"done":false}\n{"message":{"content":" bhai."},"done":false}\n{"done":true,"done_reason":"stop"}\n'
        return httpx.Response(200, content=content.encode())
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_exact_empty_answer_reasoning_budget_regression():
    calls = []
    provider = OllamaProvider("http://mock", "qwen3:4b-q4_K_M", transport=transport_for_thinking(calls))
    settings = Settings(_env_file=None, openrouter_enabled=False)
    agent = Agent(ProviderRouter(settings, providers={"ollama": provider}), settings)
    events = [event async for event in agent.stream_message("Hey Nova, who are you?")]
    assert calls[0]["think"] is False
    assert calls[0]["options"]["num_predict"] == 256
    assert events[-1].type == "llm.completed"
    assert events[-1].content == "Hello bhai."
    assert events[-1].thinking_enabled is False
    assert events[-1].ttft_ms is not None
    assert events[-1].token_count == 2
    assert events[-1].total_generation_latency_ms >= events[-1].ttft_ms


@pytest.mark.asyncio
async def test_exhausted_complex_reasoning_never_completes_empty_or_commits_context():
    calls = []
    provider = OllamaProvider("http://mock", "qwen3:4b-q4_K_M", transport=transport_for_thinking(calls))
    settings = Settings(_env_file=None, openrouter_enabled=False)
    agent = Agent(ProviderRouter(settings, providers={"ollama": provider}), settings)
    events = []
    with pytest.raises(LLMResponseError):
        async for event in agent.stream_message("Analyze this algorithm and explain its complexity."):
            events.append(event)
    assert calls[0]["think"] is True
    assert calls[0]["options"]["num_predict"] == 1024
    assert not any(event.type in {"llm.token", "llm.completed"} for event in events)
    assert agent.context.history == []


@pytest.mark.asyncio
async def test_thinking_and_content_fields_only_answer_is_streamed():
    async def handler(request):
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"thinking": {"values": [False, True]}})
        assert json.loads(request.content)["think"] is True
        return httpx.Response(200, content=(
            '{"message":{"thinking":"private reason","content":""}}\n'
            '{"message":{"content":"<thi"}}\n'
            '{"message":{"content":"nk>private inline</think>Answer"}}\n'
            '{"message":{"content":" only."}}\n'
            '{"done":true}\n'
        ).encode())
    provider = OllamaProvider("http://mock", "qwen3:4b", transport=httpx.MockTransport(handler))
    answer = await provider.generate([], think=True)
    assert answer == "Answer only."


@pytest.mark.asyncio
async def test_thinking_only_model_is_rejected_before_any_content_leaks():
    paths = []
    async def handler(request):
        paths.append(request.url.path)
        return httpx.Response(200, json={"thinking": {"values": [True], "default": True}})
    provider = OllamaProvider("http://mock", "qwen3:4b", transport=httpx.MockTransport(handler))
    with pytest.raises(LLMThinkingUnsupportedError):
        await provider.generate([], think=False)
    assert paths == ["/api/show"]


@pytest.mark.asyncio
async def test_compatibility_stream_chat_forwards_think():
    calls = []
    provider = OllamaProvider("http://mock", "qwen3:4b-q4_K_M", transport=transport_for_thinking(calls))
    answer = "".join([token async for token in provider.stream_chat([], think=False)])
    assert answer == "Hello bhai."
    assert calls[0]["think"] is False


@pytest.mark.asyncio
async def test_openrouter_ignores_vendor_specific_switch_and_hides_reasoning():
    async def handler(request):
        assert "think" not in json.loads(request.content)
        return httpx.Response(200, content=(
            'data: {"choices":[{"delta":{"reasoning":"secret","content":"<think>private</think>Hello"}}]}\n\n'
            'data: [DONE]\n\n'
        ).encode())
    provider = OpenRouterProvider("test-key", "openrouter/free", transport=httpx.MockTransport(handler))
    assert await provider.generate([], think=False) == "Hello"


def test_websocket_thinking_metadata_and_answer_events_preserved():
    calls = []
    provider = OllamaProvider("http://mock", "qwen3:4b-q4_K_M", transport=transport_for_thinking(calls))
    settings = Settings(_env_file=None, openrouter_enabled=False)
    app = create_app(settings, router=ProviderRouter(settings, providers={"ollama": provider}))
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "system.ready"
        ws.send_json({"type": "agent.message", "content": "What is 2 + 2?"})
        events = []
        while not events or events[-1]["type"] != "agent.completed":
            events.append(ws.receive_json())
    assert [event["type"] for event in events] == ["agent.started", "agent.thinking", "llm.started", "llm.token", "llm.token", "llm.completed", "agent.completed"]
    assert events[2]["thinking_enabled"] is False
    assert events[-2]["ttft_ms"] is not None
    assert "hidden reasoning" not in json.dumps(events)
