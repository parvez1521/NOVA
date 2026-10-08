import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.database.db import Database
from app.database.repository import ConversationRepository, conversation_title
from app.main import create_app


def test_initialization_migration_indexes_and_reopen(tmp_path):
    path = tmp_path / "nova.db"
    db = Database(path)
    db.initialize()
    with db.session() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"conversations", "messages", "memories", "settings"} <= tables
    repo = ConversationRepository(db)
    conversation = repo.create()
    repo.add_message(conversation["id"], "user", "What is an AI agent?")
    db.close()
    reopened = Database(path)
    reopened.initialize()
    assert ConversationRepository(reopened).latest()["id"] == conversation["id"]
    assert ConversationRepository(reopened).messages(conversation["id"])[0]["content"] == "What is an AI agent?"
    assert path.stat().st_mode & 0o777 == 0o600
    reopened.close()


def test_conversation_lifecycle_messages_and_cascade(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    repo = ConversationRepository(db)
    first = repo.latest()
    repo.add_message(first["id"], "user", "What is an AI agent?")
    assistant = repo.add_message(first["id"], "assistant", "An agent acts toward goals.", provider="ollama", model="qwen3:4b-q4_K_M")
    assert repo.get(first["id"])["title"] == "AI agent discussion"
    assert [message["role"] for message in repo.messages(first["id"])] == ["user", "assistant"]
    assert repo.messages(first["id"], limit=1)[0]["id"] == assistant["id"]
    repo.rename(first["id"], "My agents")
    assert repo.get(first["id"])["title"] == "My agents"
    repo.archive(first["id"])
    assert repo.list() == []
    assert repo.list(archived=True)[0]["id"] == first["id"]
    second = repo.latest()
    assert second["id"] != first["id"]
    repo.archive(first["id"], False)
    assert repo.select(first["id"])["id"] == first["id"]
    repo.delete(first["id"])
    with db.session() as connection:
        assert connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
    db.close()


@pytest.mark.parametrize("fts", [True, False])
def test_title_message_search_and_safe_query(tmp_path, fts):
    db = Database(tmp_path / "search.db")
    repo = ConversationRepository(db)
    one = repo.create("Premiere Pro help")
    repo.add_message(one["id"], "user", "Please explain an invoice to NOVA")
    db.fts_available = db.fts_available and fts
    assert repo.search("Premiere")[0]["conversation"]["id"] == one["id"]
    result = repo.search("invoice")[0]
    assert result["matching_message"]["content"] == "Please explain an invoice to NOVA"
    assert result["timestamp"] and result["preview"]
    repo.search('" OR 1=1; DROP TABLE messages; --')
    assert len(repo.messages(one["id"])) == 1
    db.close()


@pytest.mark.parametrize("content", ["Remember my API key is sk-...", "My password is hunter2", "Authorization: Bearer abcdef", "Credit card number: 4111 1111 1111 1111", "-----BEGIN PRIVATE KEY-----\nprivate", 'OPENROUTER_API_KEY=opaque-private-value', '{"access_token":"opaque-private-value"}', 'Cookie: session=opaque-private-value'])
def test_credentials_are_omitted_from_messages_titles_metadata_and_exports(tmp_path, content):
    db = Database(tmp_path / "private.db")
    repo = ConversationRepository(db)
    conversation = repo.create()
    message = repo.add_message(conversation["id"], "user", content, metadata={"api_key": content, "status": "running", "debug": "unsafe"})
    assert message["content"] == "[Sensitive input omitted]"
    assert repo.get(conversation["id"])["title"] == "General chat"
    exported = json.dumps(repo.export())
    assert content not in exported and "api_key" not in exported and "debug" not in exported
    db.close()


def test_reasoning_never_persisted(tmp_path):
    db = Database(tmp_path / "reasoning.db")
    repo = ConversationRepository(db)
    conversation = repo.create()
    repo.add_message(conversation["id"], "assistant", "<think>private thought</think>Done 😂", metadata={"reasoning": "private thought", "auth_header": "private"})
    assert repo.messages(conversation["id"])[0]["content"] == "Done 😂"
    assert "private thought" not in json.dumps(repo.export())
    db.close()


@pytest.mark.parametrize("text,title", [("What is an AI agent?", "AI agent discussion"), ("Help me with Premiere Pro", "Premiere Pro help"), ("Tell me about Python", "Python discussion"), ("Hello Nova", "General chat")])
def test_deterministic_titles(text, title):
    assert conversation_title(text) == title


def test_history_api_validation_and_confirmation(tmp_path):
    app = create_app(Settings(_env_file=None, database_path=str(tmp_path / "api.db")))
    with TestClient(app) as client:
        state = client.get("/api/persistence").json()
        assert state["database"]["available"] and state["conversation"]
        created = client.post("/api/conversations", json={"title": "New project"})
        assert created.status_code == 201
        identifier = created.json()["id"]
        assert client.get(f"/api/conversations/{identifier}/messages").json() == []
        assert client.patch(f"/api/conversations/{identifier}", json={"title": "Renamed"}).json()["title"] == "Renamed"
        assert client.post(f"/api/conversations/{identifier}/archive").json()["archived"]
        assert not client.post(f"/api/conversations/{identifier}/restore").json()["archived"]
        assert client.delete(f"/api/conversations/{identifier}").status_code == 409
        assert client.delete(f"/api/conversations/{identifier}?confirmed=true").status_code == 200
        assert client.get(f"/api/conversations/{identifier}").status_code == 404
        assert client.get("/api/conversations/bad-id").status_code == 422
        assert client.patch(f"/api/conversations/{state['conversation']['id']}", json={"title": "   "}).status_code == 422
        assert client.get("/api/health/live").json() == {"status": "ok"}
        assert client.get("/api/health").json()["capabilities"]["persistence"]["available"]
