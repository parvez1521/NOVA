import asyncio
from collections.abc import AsyncIterator, Sequence
from typing import Any

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.llm.base import ChatMessage, LLMProvider, ProviderHealth
from app.llm.router import ProviderRouter
from app.main import create_app


class WebSocketFakeProvider(LLMProvider):
    provider_name = "ollama"
    model_name = "qwen3:4b"

    async def is_available(self) -> bool:
        return True

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(self.provider_name, self.model_name, True, True, True, True)

    async def generate(self, messages: Sequence[ChatMessage], **_: Any) -> str:
        del messages
        return "Hello bhai"

    async def generate_stream(self, messages: Sequence[ChatMessage], **_: Any) -> AsyncIterator[str]:
        del messages
        yield "Hello"
        await asyncio.sleep(0)
        yield " bhai"


def test_websocket_streams_structured_events() -> None:
    settings = Settings(ollama_enabled=True, openrouter_enabled=False)
    provider = WebSocketFakeProvider()
    router = ProviderRouter(settings, providers={"ollama": provider})
    app = create_app(settings, router=router)

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            ready = websocket.receive_json()
            websocket.send_json({"type": "agent.message", "request_id": "11111111-1111-4111-8111-111111111111", "content": "Hello Nova"})
            events = [ready]
            while events[-1]["type"] != "agent.completed":
                events.append(websocket.receive_json())

    event_types = [event["type"] for event in events]
    tokens = [event["content"] for event in events if event["type"] == "llm.token"]
    assert event_types[:5] == ["system.ready", "agent.started", "agent.thinking", "llm.started", "llm.token"]
    assert tokens == ["Hello", " bhai"]
    assert event_types[-2:] == ["llm.completed", "agent.completed"]
    assert events[-1]["content"] == "Hello bhai"


def test_websocket_stop_cancels_active_generation() -> None:
    class SlowProvider(WebSocketFakeProvider):
        async def generate_stream(self, messages: Sequence[ChatMessage], **_: Any) -> AsyncIterator[str]:
            del messages
            yield "first"
            await asyncio.Event().wait()

    settings = Settings(ollama_enabled=True, openrouter_enabled=False)
    router = ProviderRouter(settings, providers={"ollama": SlowProvider()})
    app = create_app(settings, router=router)
    request_id = "22222222-2222-4222-8222-222222222222"

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            websocket.receive_json()
            websocket.send_json({"type": "agent.message", "request_id": request_id, "content": "Keep going"})
            while websocket.receive_json()["type"] != "llm.token":
                pass
            websocket.send_json({"type": "agent.stop", "request_id": request_id})
            cancelled = websocket.receive_json()

    assert cancelled["type"] == "agent.cancelled"
    assert cancelled["request_id"] == request_id
