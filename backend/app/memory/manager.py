"""Local command execution and per-WebSocket confirmation state."""

import re
import time
from dataclasses import dataclass, field

from app.database.db import Database
from app.database.models import MemorySettings
from app.database.privacy import unsafe_memory
from app.memory.commands import MemoryCommandParser
from app.memory.extractor import MemoryExtractor
from app.memory.repository import MemoryRepository
from app.memory.retriever import MemoryRetriever


@dataclass
class MemorySession:
    pending: dict | None = None


@dataclass
class MemoryResult:
    answer: str | None = None
    events: list[tuple[str, dict]] = field(default_factory=list)
    retrieved: list[dict] = field(default_factory=list)
    inserted: int = 0
    updated: int = 0
    deleted: int = 0
    latency_ms: float = 0
    memory_id: str | None = None


class MemoryManager:
    def __init__(self, db: Database) -> None:
        self.repository = MemoryRepository(db)
        self.parser = MemoryCommandParser()
        self.extractor = MemoryExtractor()
        self.retriever = MemoryRetriever(self.repository)

    def process(self, text: str, settings: MemorySettings, session: MemorySession) -> MemoryResult:
        started = time.perf_counter()
        result = self._process(text, settings, session)
        result.latency_ms = round((time.perf_counter() - started) * 1000, 3)
        return result

    def is_command(self, text: str, session: MemorySession) -> bool:
        normalized = " ".join(re.sub(r"[,.!?।]", " ", self.parser.normalize(text).casefold()).split())
        return self.parser.parse(text) is not None or bool(session.pending and (normalized in {"yes", "yes clear my saved memories", "yes delete all my memories", "confirm", "haan", "no", "cancel", "no cancel", "nahi", "nahin"} or re.fullmatch(r"(?:option )?\d+", normalized)))

    def _process(self, text: str, settings: MemorySettings, session: MemorySession) -> MemoryResult:
        result = MemoryResult()
        normalized = " ".join(re.sub(r"[,.!?।]", " ", self.parser.normalize(text).casefold()).split())
        pending = session.pending
        if pending and time.monotonic() > pending["expires"]:
            session.pending = None; pending = None
        if pending:
            if normalized in {"no", "cancel", "no cancel", "nahi", "nahin"}:
                session.pending = None
                result.answer = "Cancelled. Your memories are unchanged."
                return result
            if pending["action"] == "clear" and normalized in {"yes", "yes clear my saved memories", "yes delete all my memories", "confirm", "haan"}:
                session.pending = None
                result.deleted = self.repository.clear()
                result.events.append(("memory.deleted", {"count": result.deleted, "cleared": True}))
                result.answer = "Done. All saved memories are cleared; your chats are still here."
                return result
            if pending["action"] == "choose" and re.fullmatch(r"(?:option )?\d+", normalized):
                index = int(normalized.removeprefix("option ")) - 1
                if 0 <= index < len(pending["choices"]):
                    identifier = pending["choices"][index]
                    self.repository.forget(identifier)
                    session.pending = None
                    result.deleted = 1
                    result.events.append(("memory.deleted", {"memory_id": identifier}))
                    result.answer = "Done, woh memory remove kar diya."
                    return result
            session.pending = None  # Unrelated requests cannot accidentally confirm later.
        command = self.parser.parse(text)
        if command:
            if command.action == "clear":
                session.pending = {"action": "clear", "expires": time.monotonic() + 300}
                result.answer = "That will delete all saved memories. Are you sure?"
                result.events.append(("memory.confirmation", {"action": "clear", "prompt": result.answer}))
                return result
            if command.action == "view":
                memories = self.repository.active_all()
                result.answer = "Here's what I remember about you:\n" + "\n".join("• " + memory["content"] for memory in memories[:30]) if memories else "I don't have any saved memories about you yet."
                if len(memories) > 30:
                    result.answer += f"\nPlus {len(memories) - 30} more saved facts; you can inspect all of them in Memory settings."
                return result
            if command.action == "preference":
                if not settings.memory_enabled:
                    result.answer = "Memory is disabled, so I'm not using saved reply preferences."
                    return result
                memory = self.repository.by_fact_key("communication:response_language")
                if not memory:
                    result.answer = "I don't have a saved response-language preference for you."
                else:
                    language = memory["metadata"].get("fact_value")
                    result.answer = {"hinglish": "Hinglish mein reply karunga.", "english": "I'll reply in English.", "hindi": "मैं हिंदी में जवाब दूँगा।"}.get(language, memory["content"])
                    result.retrieved = [{"memory_id": memory["id"], "content": memory["content"], "category": memory["category"], "relevance_score": 1.0}]
                    self.repository.used([memory["id"]])
                return result
            if command.action == "remember":
                if not settings.memory_enabled:
                    result.answer = "Memory is disabled. You can enable it in Memory settings."
                    return result
                candidate = self.extractor.extract(command.content, explicit=True)
                if candidate is None:
                    result.answer = "I won't save credentials, private secrets, or unsafe instructions as memory."
                    return result
                memory, action = self.repository.upsert(candidate)
                self._changed(result, memory, action)
                result.answer = "Yaad rakh liya."
                return result
            if command.action == "forget":
                candidate = self.extractor.extract(command.content, explicit=True)
                language = bool(re.search(r"(?:response|reply|communication) language|language preference|preference about (?:response )?language", command.content, re.I))
                exact = []
                structured = candidate and not candidate.fact_key.startswith("profile:note:")
                if language or structured:
                    matched = self.repository.by_fact_key("communication:response_language" if language else candidate.fact_key)
                    if matched and (language or matched["metadata"].get("fact_value") == candidate.fact_value):
                        exact = [matched]
                elif command.content.casefold() in {"my preferences", "my preference"}:
                    exact = self.repository.candidates("", preferences=False, categories=["preference", "communication"])
                elif command.content.casefold() in {"that", "this"}:
                    exact = self.repository.list(limit=5)
                else:
                    relevant = self.retriever.retrieve(command.content, top_k=10, preferences=False, mark_used=False)
                    exact = [self.repository.get(item["memory_id"]) for item in relevant if item["relevance_score"] >= 0.6]
                if len(exact) == 1:
                    self.repository.forget(exact[0]["id"])
                    result.deleted = 1
                    result.events.append(("memory.deleted", {"memory_id": exact[0]["id"]}))
                    result.answer = "Done, woh preference memory se remove kar diya."
                elif exact:
                    choices = exact[:5]
                    session.pending = {"action": "choose", "choices": [memory["id"] for memory in choices], "expires": time.monotonic() + 300}
                    labels = [memory["content"] for memory in choices]
                    result.answer = "Which memory should I forget?\n" + "\n".join(f"{index + 1}. {content}" for index, content in enumerate(labels))
                    result.events.append(("memory.choices", {"choices": labels}))
                else:
                    result.answer = "I couldn't find a matching saved memory."
                return result
        if settings.memory_enabled and settings.memory_auto_extraction and not unsafe_memory(text):
            candidate = self.extractor.extract(text)
            if candidate and candidate.confidence >= settings.memory_min_confidence:
                memory, action = self.repository.upsert(candidate)
                self._changed(result, memory, action)
        if settings.memory_enabled:
            result.retrieved = self.retriever.retrieve(text, top_k=settings.memory_top_k)
        return result

    @staticmethod
    def _changed(result: MemoryResult, memory: dict, action: str) -> None:
        result.memory_id = memory["id"]
        if action == "created":
            result.inserted += 1
        elif action == "updated":
            result.updated += 1
        if action != "unchanged":
            result.events.append((f"memory.{action}", {"memory_id": memory["id"], "category": memory["category"]}))
