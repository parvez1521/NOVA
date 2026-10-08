import asyncio
import base64
import json

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.llm.router import ProviderRouter
from app.main import create_app
from app.voice.manager import VoiceManager
from app.voice.stt.whisper import normalize_spoken_command
from tests.test_voice import MockSTT, MockTTS, wav
from tests.test_websocket_streaming import WebSocketFakeProvider


class CapturingProvider(WebSocketFakeProvider):
    def __init__(self):
        self.requests = []

    async def generate_stream(self, messages, **kwargs):
        self.requests.append(messages)
        yield "Hello bhai"
        await asyncio.sleep(0)


def app_for(path, provider=None, stt=None, tts=None):
    settings = Settings(_env_file=None, database_path=str(path), openrouter_enabled=False)
    provider = provider or CapturingProvider()
    manager = VoiceManager(settings, stt=stt or MockSTT(), tts=tts or MockTTS())
    return create_app(settings, router=ProviderRouter(settings, providers={"ollama": provider}), voice=manager)


def collect(ws):
    result = []
    while True:
        event = ws.receive_json(); result.append(event)
        if event["type"] in {"agent.completed", "agent.cancelled"} or (event["type"] == "system.error" and not event.get("recoverable")):
            return result


def message(ws, content, **kwargs):
    ws.send_json({"type": "agent.message", "content": content, **kwargs})
    return collect(ws)


def test_local_remember_restart_retrieval_update_forget_and_memory_context(tmp_path):
    path = tmp_path / "nova.db"
    provider = CapturingProvider()
    with TestClient(app_for(path, provider)) as client, client.websocket_connect("/ws") as ws:
        conversation_id = ws.receive_json()["conversation_id"]
        events = message(ws, "Remember that I prefer Hinglish.")
        assert events[-1]["content"] == "Yaad rakh liya."
        assert provider.requests == []
        assert any(event["type"] == "memory.created" for event in events)
        assert len(client.get(f"/api/conversations/{conversation_id}/messages").json()) == 2
    restarted_provider = CapturingProvider()
    with TestClient(app_for(path, restarted_provider)) as client, client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["conversation_id"] == conversation_id
        assert len(client.get("/api/memories").json()) == 1
        events = message(ws, "How should you reply to me?")
        started = next(event for event in events if event["type"] == "llm.started")
        assert started["retrieved_memory_count"] == 1
        assert events[-1]["content"].startswith("Hinglish mein")
        assert started["provider"] == "local"
        events = message(ws, "Tell me what an AI agent is.")
        assert "User prefers Hinglish replies." in restarted_provider.requests[-1][0]["content"]
        assert "confidence" not in restarted_provider.requests[-1][0]["content"]
        assert "Do not answer entirely in English" in restarted_provider.requests[-1][0]["content"]
        message(ws, "Remember that I prefer English.")
        message(ws, "Actually remember that I prefer Hinglish.")
        memories = client.get("/api/memories").json()
        assert len(memories) == 1 and "Hinglish" in memories[0]["content"]
        message(ws, "Forget that I prefer Hinglish.")
        assert client.get("/api/memories").json() == []
        events = message(ws, "What language do I prefer?")
        assert next(event for event in events if event["type"] == "llm.started")["retrieved_memory_count"] == 0
        message(ws, "Tell me what an AI agent is.")
        assert not any("User prefers Hinglish replies." in item["content"] for item in restarted_provider.requests[-1])


def test_forgetting_preference_removes_memory_influenced_context_but_keeps_history(tmp_path):
    provider = CapturingProvider()
    with TestClient(app_for(tmp_path / "influenced.db", provider)) as client, client.websocket_connect("/ws") as ws:
        identifier = ws.receive_json()["conversation_id"]
        message(ws, "Remember I prefer Hinglish")
        message(ws, "Tell me a joke")
        message(ws, "Forget that I prefer Hinglish")
        message(ws, "What is 2 + 2?")
        assert not any(item["content"] == "Tell me a joke" for item in provider.requests[-1])
        assert any(item["content"] == "Tell me a joke" for item in client.get(f"/api/conversations/{identifier}/messages").json())


def test_voice_remember_and_forget_use_same_chat_database_and_tts(tmp_path):
    class CommandSTT(MockSTT):
        text = "Nova, remember that I prefer Hinglish."

        async def transcribe(self, audio):
            result = await super().transcribe(audio)
            result.text = result.raw_transcript = result.normalized_transcript = self.text
            return result

    stt, tts, provider = CommandSTT(), MockTTS(), CapturingProvider()
    with TestClient(app_for(tmp_path / "voice.db", provider, stt, tts)) as client, client.websocket_connect("/ws") as ws:
        identifier = ws.receive_json()["conversation_id"]
        for index, utterance in enumerate(["Nova, remember that I prefer Hinglish.", "Nova, forget that I prefer Hinglish."]):
            stt.text = utterance
            request_id = ["33333333-3333-4333-8333-333333333333", "44444444-4444-4444-8444-444444444444"][index]
            ws.send_json({"type": "voice.start", "request_id": request_id}); ws.receive_json()
            ws.send_json({"type": "voice.audio", "request_id": request_id, "audio": base64.b64encode(wav()).decode()})
            events = collect(ws)
            assert events[-1]["type"] == "agent.completed"
            messages = client.get(f"/api/conversations/{identifier}/messages").json()
            expected = normalize_spoken_command(utterance)
            assert messages[-2]["content"] == expected
            assert messages[-2]["input_type"] == messages[-1]["input_type"] == "voice"
            assert messages[-1]["content"] == events[-1]["content"]
            assert any(event["type"] == "tts.completed" for event in events)
        assert provider.requests == [] and client.get("/api/memories").json() == []
        assert tts.spoken[0] == "Yaad rakh liya."


def test_temporary_chat_survives_restart_without_becoming_memory(tmp_path):
    path = tmp_path / "temporary.db"
    with TestClient(app_for(path)) as client, client.websocket_connect("/ws") as ws:
        identifier = ws.receive_json()["conversation_id"]
        message(ws, "Tomorrow I need to edit a video.")
        assert client.get("/api/memories").json() == []
    with TestClient(app_for(path)) as client:
        assert client.get(f"/api/conversations/{identifier}/messages").json()[0]["content"] == "Tomorrow I need to edit a video."
        assert client.get("/api/memories").json() == []


def test_automatic_memory_indicator_and_deleted_fact_not_rehydrated(tmp_path):
    provider = CapturingProvider()
    with TestClient(app_for(tmp_path / "auto.db", provider)) as client, client.websocket_connect("/ws") as ws:
        ws.receive_json()
        events = message(ws, "I prefer short answers.")
        assert any(event["type"] == "memory.created" for event in events)
        assert next(event for event in events if event["type"] == "llm.started")["memory_inserted"] == 1
        memory = client.get("/api/memories").json()[0]
        assert memory["confidence"] == 0.9 and memory["source"] == "automatic"
        assert client.delete(f"/api/memories/{memory['id']}?confirmed=true").status_code == 200
        message(ws, "Tell me a joke")
        assert not any(item["content"] == "I prefer short answers." for item in provider.requests[-1])


def test_credentials_not_stored_or_sent_to_provider_and_view_has_no_metadata(tmp_path):
    provider = CapturingProvider()
    with TestClient(app_for(tmp_path / "private.db", provider)) as client, client.websocket_connect("/ws") as ws:
        identifier = ws.receive_json()["conversation_id"]
        result = message(ws, "Remember my API key is sk-...")
        assert "won't save" in result[-1]["content"]
        assert provider.requests == [] and client.get("/api/memories").json() == []
        assert "sk-..." not in json.dumps(client.get(f"/api/conversations/{identifier}/messages").json())
        message(ws, 'My password is hunter2')
        assert "hunter2" not in json.dumps(provider.requests)
        message(ws, "Remember that I like AI 😂")
        result = message(ws, "What do you remember about me?")
        assert "User likes AI." in result[-1]["content"]
        for memory in client.get("/api/memories").json():
            assert memory["id"] not in result[-1]["content"]
        assert "confidence" not in result[-1]["content"]


def test_clear_command_requires_confirmation_and_preserves_chat(tmp_path):
    with TestClient(app_for(tmp_path / "clear.db")) as client, client.websocket_connect("/ws") as ws:
        identifier = ws.receive_json()["conversation_id"]
        message(ws, "Remember I use Premiere Pro")
        result = message(ws, "Forget everything you remember about me")
        assert any(event["type"] == "memory.confirmation" for event in result)
        assert len(client.get("/api/memories").json()) == 1
        message(ws, "Yes, clear my saved memories.")
        assert client.get("/api/memories?include_inactive=true").json() == []
        assert len(client.get(f"/api/conversations/{identifier}/messages").json()) == 6


def test_memory_api_exports_clear_controls_and_settings_survive_restart(tmp_path):
    path = tmp_path / "api.db"
    with TestClient(app_for(path)) as client:
        original = client.get("/api/settings/memory").json()
        changed = {**original, "memory_top_k": 3, "memory_min_confidence": 0.9}
        assert client.put("/api/settings/memory", json=changed).json() == changed
        created = client.post("/api/memories", json={"content": "I use Premiere Pro"}).json()
        assert created["content"] == "User uses Premiere Pro."
        assert client.get("/api/memories/search?q=Premiere").json()[0]["memory_id"] == created["id"]
        edited = client.patch(f"/api/memories/{created['id']}", json={"content": "I use Python", "category": "technical"}).json()
        assert edited["category"] == "technical"
        assert client.delete(f"/api/memories/{edited['id']}").status_code == 409
        assert client.post("/api/memories/clear", json={"confirmed": False}).status_code == 422
        assert client.post("/api/memories/clear", json={}).status_code == 422
        assert client.post("/api/memories", json={"content": "My password is abc"}).status_code == 422
        assert client.put("/api/settings/memory", json={**changed, "memory_top_k": 0}).status_code == 422
        assert client.post("/api/data/clear", json={"scope": "everything", "confirmed": False}).status_code == 422
        for kind in ("chats", "memories"):
            assert client.get(f"/api/export/{kind}").status_code == 200
            markdown = client.get(f"/api/export/{kind}?format=markdown")
            assert markdown.status_code == 200 and "attachment" in markdown.headers["content-disposition"]
        assert client.post("/api/data/clear", json={"scope": "chats", "confirmed": True}).status_code == 200
        assert len(client.get("/api/memories").json()) == 1
        result = client.post("/api/data/clear", json={"scope": "everything", "confirmed": True}).json()
        assert result["messages"] == [] and result["settings"] == changed
        assert client.get("/api/memories?include_inactive=true").json() == []
    with TestClient(app_for(path)) as client:
        assert client.get("/api/settings/memory").json() == changed


def test_memory_enabled_with_history_disabled_is_independent(tmp_path):
    with TestClient(app_for(tmp_path / "independent.db")) as client, client.websocket_connect("/ws") as ws:
        identifier = ws.receive_json()["conversation_id"]
        settings = client.get("/api/settings/memory").json()
        client.put("/api/settings/memory", json={**settings, "save_chat_history": False})
        message(ws, "Remember I prefer Hinglish")
        assert client.get(f"/api/conversations/{identifier}/messages").json() == []
        assert len(client.get("/api/memories").json()) == 1


def test_unavailable_database_does_not_crash_assistant_and_can_recover(tmp_path):
    path = tmp_path / "directory-not-a-db"; path.mkdir()
    with TestClient(app_for(path)) as client, client.websocket_connect("/ws") as ws:
        ws.receive_json()
        events = message(ws, "Hello Nova")
        assert events[-1]["type"] == "agent.completed"
        assert any(event.get("code") == "PERSISTENCE_UNAVAILABLE" and event.get("recoverable") for event in events)
        assert not client.get("/api/persistence").json()["database"]["available"]
        path.rmdir()
        events = message(ws, "Saving works now")
        assert events[-1]["type"] == "agent.completed"
        assert client.get("/api/persistence").json()["database"]["available"]
        assert client.get("/api/persistence").json()["messages"][0]["content"] == "Saving works now"
