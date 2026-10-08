"""Conversation and settings repositories. SQL values are always parameters."""

import json
import re

from app.database.db import Database
from app.database.models import MemorySettings, new_id, now
from app.database.privacy import safe_metadata, safe_text


def record(row) -> dict | None:
    if row is None:
        return None
    result = dict(row)
    result["metadata"] = json.loads(result.pop("metadata_json", "{}"))
    for key in ("archived", "is_active"):
        if key in result:
            result[key] = bool(result[key])
    return result


def search_terms(query: str) -> list[str]:
    return re.findall(r"[^\W_]+", query.casefold(), re.UNICODE)[:12]


def fts_query(query: str) -> str:
    return " OR ".join(f'"{word}"*' for word in search_terms(query))


def conversation_title(content: str) -> str:
    content = safe_text(content).strip(" .!?।")
    if re.match(r"^(?:hello|hi|hey|namaste)(?:\s+nova)?$", content, re.I) or content == "[Sensitive input omitted]":
        return "General chat"
    patterns = [(r"^(?:tell me (?:about|what)|what is|what's|explain)\s+(?:an?\s+)?(.+?)(?:\s+is)?$", "discussion"),
                (r"^(?:help me (?:with|using)|help with)\s+(.+)$", "help")]
    for pattern, suffix in patterns:
        if match := re.match(pattern, content, re.I):
            topic = match[1].strip()
            topic = re.sub(r"\bai\b", "AI", topic, flags=re.I)
            return f"{topic[:48]} {suffix}"[:60]
    return content[:57] + ("…" if len(content) > 57 else "") or "General chat"


class SettingsRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def get(self, key: str, default=None):
        with self.db.session() as connection:
            row = connection.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set(self, key: str, value) -> None:
        if key not in {"active_conversation_id", "memory_settings", "computer_settings"}:
            raise ValueError("Unsupported persistence setting")
        if key == "memory_settings":
            value = MemorySettings.model_validate(value).model_dump()
        with self.db.session() as connection:
            connection.execute("INSERT INTO settings(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at", (key, json.dumps(value), now()))


class ConversationRepository:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.settings = SettingsRepository(db)

    def create(self, title: str | None = None) -> dict:
        if title is not None and not safe_text(title):
            raise ValueError("Title cannot be blank")
        identifier, timestamp = new_id(), now()
        metadata = {"title_source": "manual" if title else "generated"}
        with self.db.session() as connection:
            connection.execute("INSERT INTO conversations(id,title,created_at,updated_at,metadata_json) VALUES(?,?,?,?,?)", (identifier, safe_text(title or "General chat")[:60], timestamp, timestamp, json.dumps(metadata)))
        self.settings.set("active_conversation_id", identifier)
        return self.get(identifier)

    def get(self, identifier: str) -> dict:
        with self.db.session() as connection:
            row = connection.execute("""SELECT c.*,
                (SELECT COUNT(*) FROM messages WHERE conversation_id=c.id) AS message_count,
                (SELECT substr(content,1,140) FROM messages WHERE conversation_id=c.id ORDER BY timestamp DESC,rowid DESC LIMIT 1) AS last_message_preview,
                (SELECT timestamp FROM messages WHERE conversation_id=c.id ORDER BY timestamp DESC,rowid DESC LIMIT 1) AS last_message_at
                FROM conversations c WHERE id=?""", (identifier,)).fetchone()
            if row is None:
                raise LookupError("Conversation not found")
            return record(row)

    def latest(self) -> dict:
        identifier = self.settings.get("active_conversation_id")
        if identifier:
            try:
                conversation = self.get(identifier)
                if not conversation["archived"]:
                    return conversation
            except LookupError:
                pass
        with self.db.session() as connection:
            row = connection.execute("SELECT id FROM conversations WHERE archived=0 ORDER BY updated_at DESC LIMIT 1").fetchone()
        return self.select(row[0]) if row else self.create()

    def select(self, identifier: str) -> dict:
        conversation = self.get(identifier)
        if conversation["archived"]:
            raise ValueError("Restore the archived conversation before opening it")
        self.settings.set("active_conversation_id", identifier)
        return conversation

    def list(self, *, archived: bool = False, limit: int = 100, offset: int = 0) -> list[dict]:
        with self.db.session() as connection:
            identifiers = connection.execute("SELECT id FROM conversations WHERE archived=? ORDER BY updated_at DESC LIMIT ? OFFSET ?", (int(archived), limit, offset)).fetchall()
            return [self.get(row[0]) for row in identifiers]

    def rename(self, identifier: str, title: str) -> dict:
        if not title.strip():
            raise ValueError("Title cannot be blank")
        self.get(identifier)
        with self.db.session() as connection:
            connection.execute("UPDATE conversations SET title=?,updated_at=?,metadata_json=? WHERE id=?", (safe_text(title.strip())[:60], now(), json.dumps({"title_source": "manual"}), identifier))
        return self.get(identifier)

    def archive(self, identifier: str, archived: bool = True) -> dict:
        self.get(identifier)
        with self.db.session() as connection:
            connection.execute("UPDATE conversations SET archived=?,updated_at=? WHERE id=?", (int(archived), now(), identifier))
        return self.get(identifier)

    def delete(self, identifier: str) -> None:
        self.get(identifier)
        with self.db.session() as connection:
            connection.execute("DELETE FROM conversations WHERE id=?", (identifier,))

    def clear(self) -> None:
        with self.db.session() as connection:
            connection.execute("DELETE FROM conversations")
            connection.execute("DELETE FROM settings WHERE key='active_conversation_id'")

    def add_message(self, conversation_id: str, role: str, content: str, *, input_type: str = "text", provider: str | None = None, model: str | None = None, metadata: dict | None = None) -> dict:
        if role not in {"user", "assistant"} or input_type not in {"text", "voice"}:
            raise ValueError("Invalid message role or input type")
        conversation = self.get(conversation_id)
        cleaned = safe_text(content)
        if not cleaned:
            raise ValueError("No visible content to save")
        identifier, timestamp = new_id(), now()
        safe = safe_metadata(metadata)
        if cleaned == "[Sensitive input omitted]":
            safe["redacted"] = True
        with self.db.session() as connection:
            connection.execute("INSERT INTO messages(id,conversation_id,role,content,timestamp,provider,model,input_type,metadata_json) VALUES(?,?,?,?,?,?,?,?,?)", (identifier, conversation_id, role, cleaned, timestamp, safe_text(provider) if provider else None, safe_text(model) if model else None, input_type, json.dumps(safe)))
            title = conversation["title"]
            if role == "user" and conversation["message_count"] == 0 and conversation["metadata"].get("title_source") != "manual":
                title = conversation_title(cleaned)
            connection.execute("UPDATE conversations SET updated_at=?,title=? WHERE id=?", (timestamp, title, conversation_id))
        return {"id": identifier, "conversation_id": conversation_id, "role": role, "content": cleaned, "timestamp": timestamp, "provider": provider, "model": model, "input_type": input_type, "metadata": safe}

    def messages(self, identifier: str, *, limit: int = 100, offset: int = 0) -> list[dict]:
        self.get(identifier)
        with self.db.session() as connection:
            rows = connection.execute("SELECT * FROM messages WHERE conversation_id=? ORDER BY timestamp DESC,rowid DESC LIMIT ? OFFSET ?", (identifier, limit, offset)).fetchall()
            return [record(row) for row in reversed(rows)]

    def message_status(self, identifier: str, status: str) -> None:
        if status not in {"completed", "cancelled", "failed", "running"}:
            raise ValueError("Invalid status")
        with self.db.session() as connection:
            row = connection.execute("SELECT metadata_json FROM messages WHERE id=?", (identifier,)).fetchone()
            if row:
                metadata = json.loads(row[0])
                metadata["status"] = status
                connection.execute("UPDATE messages SET metadata_json=? WHERE id=?", (json.dumps(metadata), identifier))

    def annotate(self, identifier: str, values: dict) -> None:
        with self.db.session() as connection:
            row = connection.execute("SELECT metadata_json FROM messages WHERE id=?", (identifier,)).fetchone()
            if row:
                metadata = json.loads(row[0])
                metadata.update(safe_metadata(values))
                connection.execute("UPDATE messages SET metadata_json=? WHERE id=?", (json.dumps(metadata), identifier))

    def search(self, query: str, *, limit: int = 50, archived: bool = False) -> list[dict]:
        terms = search_terms(query)
        if not terms:
            return []
        with self.db.session() as connection:
            if self.db.fts_available:
                matches = connection.execute("""SELECT c.id AS conversation_id,m.id AS message_id,m.content,m.timestamp FROM message_search
                    JOIN messages m ON m.rowid=message_search.rowid JOIN conversations c ON c.id=m.conversation_id
                    WHERE message_search MATCH ? AND c.archived=? ORDER BY m.timestamp DESC LIMIT ?""", (fts_query(query), int(archived), limit)).fetchall()
                titles = connection.execute("SELECT c.id FROM conversation_search JOIN conversations c ON c.rowid=conversation_search.rowid WHERE conversation_search MATCH ? AND c.archived=? LIMIT ?", (fts_query(query), int(archived), limit)).fetchall()
            else:
                predicate = " OR ".join("m.content LIKE ? ESCAPE '\\'" for _ in terms)
                matches = connection.execute(f"SELECT c.id AS conversation_id,m.id AS message_id,m.content,m.timestamp FROM messages m JOIN conversations c ON c.id=m.conversation_id WHERE c.archived=? AND ({predicate}) ORDER BY m.timestamp DESC LIMIT ?", (int(archived), *[f"%{term}%" for term in terms], limit)).fetchall()
                predicate = " OR ".join("title LIKE ?" for _ in terms)
                titles = connection.execute(f"SELECT id FROM conversations WHERE archived=? AND ({predicate}) LIMIT ?", (int(archived), *[f"%{term}%" for term in terms], limit)).fetchall()
            result = [{"conversation": self.get(row["conversation_id"]), "matching_message": {"id": row["message_id"], "content": row["content"]}, "timestamp": row["timestamp"], "preview": row["content"][:180]} for row in matches]
            matched = {item["conversation"]["id"] for item in result}
            for row in titles:
                if row[0] not in matched:
                    conversation = self.get(row[0])
                    result.append({"conversation": conversation, "matching_message": None, "timestamp": conversation["updated_at"], "preview": conversation["last_message_preview"] or conversation["title"]})
            return sorted(result, key=lambda item: item["timestamp"], reverse=True)[:limit]

    def export(self) -> list[dict]:
        with self.db.session() as connection:
            rows = connection.execute("SELECT id FROM conversations ORDER BY created_at").fetchall()
            result = []
            for row in rows:
                conversation = self.get(row[0])
                messages = connection.execute("SELECT * FROM messages WHERE conversation_id=? ORDER BY timestamp,rowid", (row[0],)).fetchall()
                result.append({**conversation, "messages": [record(message) for message in messages]})
            return result
