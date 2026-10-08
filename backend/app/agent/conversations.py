"""Persistent conversation lifecycle, independent of providers and audio adapters."""

from pathlib import Path

from app.core.config import Settings
from app.database.db import Database, DatabaseUnavailable
from app.database.models import MemorySettings
from app.database.repository import ConversationRepository, SettingsRepository
from app.memory.manager import MemoryManager


class ConversationManager:
    def __init__(self, config: Settings) -> None:
        path = Path(config.database_path).expanduser()
        self.db = Database(":memory:" if config.database_path == ":memory:" else path if path.is_absolute() else config.project_root / path)
        self.conversations = ConversationRepository(self.db)
        self.settings_repository = SettingsRepository(self.db)
        self.memory = MemoryManager(self.db)
        self.settings = MemorySettings(
            save_chat_history=config.save_chat_history, memory_enabled=config.memory_enabled,
            memory_auto_extraction=config.memory_auto_extraction,
            memory_min_confidence=config.memory_min_confidence, memory_top_k=config.memory_top_k,
        )

    def initialize(self) -> None:
        self.db.initialize()
        stored = self.settings_repository.get("memory_settings")
        if stored:
            self.settings = MemorySettings.model_validate(stored)
        self.conversations.latest()

    def state(self) -> dict:
        try:
            conversation = self.conversations.latest()
            return {"conversation": conversation, "messages": self.conversations.messages(conversation["id"]),
                    "settings": self.settings.model_dump(), "database": self.db.status()}
        except DatabaseUnavailable:
            return {"conversation": None, "messages": [], "settings": self.settings.model_dump(), "database": self.db.status()}

    def update_settings(self, settings: MemorySettings) -> dict:
        self.settings_repository.set("memory_settings", settings.model_dump())
        self.settings = settings
        return settings.model_dump()

    def context_messages(self, identifier: str, limit: int) -> list[dict]:
        messages = self.conversations.messages(identifier, limit=limit * 2)
        excluded = set()
        for message in messages:
            metadata = message["metadata"]
            identifiers = metadata.get("memory_ids", []) + ([metadata["memory_id"]] if metadata.get("memory_id") else [])
            active = True
            for memory_id in identifiers:
                try:
                    active = active and self.memory.repository.get(memory_id)["is_active"]
                except LookupError:
                    active = False
            if metadata.get("memory_command") or not active:
                excluded.add(metadata.get("request_id"))
        return [{"role": message["role"], "content": message["content"]} for message in messages
                if message["metadata"].get("status", "completed") == "completed"
                and not message["metadata"].get("memory_command")
                and message["metadata"].get("request_id") not in excluded][-limit:]

    def begin(self, identifier: str, content: str, input_type: str, request_id: str, *, memory_command: bool = False) -> str | None:
        if not self.settings.save_chat_history:
            return None
        message = self.conversations.add_message(identifier, "user", content, input_type=input_type,
            metadata={"status": "running", "request_id": request_id, "memory_command": memory_command})
        return message["id"]

    def complete(self, identifier: str, content: str, input_type: str, request_id: str, user_message_id: str | None,
                 provider: str | None, model: str | None, *, memory_command: bool = False, memory_ids: list[str] | None = None) -> dict | None:
        if not self.settings.save_chat_history or not user_message_id:
            return None
        with self.db.session():
            self.conversations.add_message(identifier, "assistant", content, input_type=input_type, provider=provider, model=model,
                metadata={"status": "completed", "request_id": request_id, "memory_command": memory_command, "memory_ids": memory_ids or []})
            self.conversations.message_status(user_message_id, "completed")
        return self.conversations.get(identifier)
