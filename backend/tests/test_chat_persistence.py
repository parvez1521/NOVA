import asyncio

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.llm.errors import LLMResponseError
from app.llm.router import ProviderRouter
from app.main import create_app
from tests.test_websocket_streaming import WebSocketFakeProvider


def chat_app(path, provider=None):
    settings = Settings(_env_file=None, database_path=str(path), openrouter_enabled=False)
    return create_app(settings, router=ProviderRouter(settings, providers={"ollama": provider or WebSocketFakeProvider()}))


def send(ws, content, **payload):
    ws.send_json({"type": "agent.message", "content": content, **payload})
    events = []
    while not events or events[-1]["type"] not in {"agent.completed", "agent.cancelled", "system.error"}:
        events.append(ws.receive_json())
    return events


def test_text_history_survives_application_restart_and_rehydrates_context(tmp_path):
    path = tmp_path / "restart.db"
    app = chat_app(path)
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        identifier = ws.receive_json()["conversation_id"]
        assert send(ws, "What is an AI agent?")[-1]["type"] == "agent.completed"
        messages = client.get(f"/api/conversations/{identifier}/messages").json()
        assert [message["role"] for message in messages] == ["user", "assistant"]
        assert messages[1]["content"] == "Hello bhai"
    captured = []
    provider = WebSocketFakeProvider()

    async def stream(messages, **kwargs):
        captured.extend(messages)
        yield "Restored reply"

    provider.generate_stream = stream
    with TestClient(chat_app(path, provider)) as client, client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["conversation_id"] == identifier
        assert client.get("/api/persistence").json()["messages"][0]["content"] == "What is an AI agent?"
        send(ws, "Continue")
    assert {"role": "user", "content": "What is an AI agent?"} in captured
    assert {"role": "assistant", "content": "Hello bhai"} in captured


def test_cancellation_and_failure_save_only_user(tmp_path):
    provider = WebSocketFakeProvider()

    async def slow(*args, **kwargs):
        yield "Incomplete"
        await asyncio.Event().wait()

    provider.generate_stream = slow
    with TestClient(chat_app(tmp_path / "cancel.db", provider)) as client, client.websocket_connect("/ws") as ws:
        identifier = ws.receive_json()["conversation_id"]
        ws.send_json({"type": "agent.message", "content": "Keep going"})
        while ws.receive_json()["type"] != "llm.token":
            pass
        ws.send_json({"type": "agent.stop"})
        assert ws.receive_json()["type"] == "agent.cancelled"
        messages = client.get(f"/api/conversations/{identifier}/messages").json()
        assert len(messages) == 1 and messages[0]["metadata"]["status"] == "cancelled"

    async def fail(*args, **kwargs):
        raise LLMResponseError("Generation failed", provider="ollama")
        yield "unreachable"

    provider.generate_stream = fail
    with TestClient(chat_app(tmp_path / "failure.db", provider)) as client, client.websocket_connect("/ws") as ws:
        identifier = ws.receive_json()["conversation_id"]
        assert send(ws, "Fail safely")[-1]["type"] == "system.error"
        messages = client.get(f"/api/conversations/{identifier}/messages").json()
        assert len(messages) == 1 and messages[0]["metadata"]["status"] == "failed"


def test_new_conversation_clears_context_and_history_can_be_reopened(tmp_path):
    provider = WebSocketFakeProvider()
    captured = []

    async def stream(messages, **kwargs):
        captured.append(messages)
        yield "Done"

    provider.generate_stream = stream
    with TestClient(chat_app(tmp_path / "new.db", provider)) as client, client.websocket_connect("/ws") as ws:
        first = ws.receive_json()["conversation_id"]
        send(ws, "First topic")
        second = client.post("/api/conversations", json={}).json()["id"]
        ws.send_json({"type": "conversation.select", "conversation_id": second})
        assert ws.receive_json()["type"] == "conversation.updated"
        send(ws, "Second topic", conversation_id=second)
        assert not any(message["content"] == "First topic" for message in captured[-1])
        ws.send_json({"type": "conversation.select", "conversation_id": first})
        ws.receive_json()
        send(ws, "Continue first", conversation_id=first)
        assert any(message["content"] == "First topic" for message in captured[-1])


def test_disabling_history_keeps_chat_working(tmp_path):
    with TestClient(chat_app(tmp_path / "off.db")) as client, client.websocket_connect("/ws") as ws:
        identifier = ws.receive_json()["conversation_id"]
        settings = client.get("/api/settings/memory").json()
        client.put("/api/settings/memory", json={**settings, "save_chat_history": False})
        assert send(ws, "Do not save this")[-1]["type"] == "agent.completed"
        assert client.get(f"/api/conversations/{identifier}/messages").json() == []
