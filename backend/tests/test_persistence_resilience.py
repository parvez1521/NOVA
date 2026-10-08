import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.database.db import Database, DatabaseUnavailable
from app.database.repository import ConversationRepository
from app.memory.extractor import MemoryExtractor
from app.memory.repository import MemoryRepository
from app.memory.retriever import MemoryRetriever


def test_concurrent_duplicate_inserts_are_serialized(tmp_path):
    db = Database(tmp_path / "concurrent.db")
    repo = MemoryRepository(db)
    candidate = MemoryExtractor().extract("I prefer Hinglish", explicit=True)
    with ThreadPoolExecutor(max_workers=8) as workers:
        results = list(workers.map(lambda _: repo.upsert(candidate), range(30)))
    assert len(repo.list()) == 1
    assert sum(action == "created" for _, action in results) == 1
    db.close()


def test_temporary_write_lock_returns_bounded_warning_and_recovers(tmp_path):
    path = tmp_path / "locked.db"
    db = Database(path)
    repo = ConversationRepository(db)
    conversation = repo.create()
    external = sqlite3.connect(path)
    external.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(DatabaseUnavailable):
            repo.add_message(conversation["id"], "user", "Should not be saved halfway")
        assert not db.status()["available"]
    finally:
        external.rollback(); external.close()
    assert repo.messages(conversation["id"]) == []
    repo.add_message(conversation["id"], "user", "Recovered")
    assert db.status()["available"] and len(repo.messages(conversation["id"])) == 1
    db.close()


def test_nested_transaction_rolls_back_atomic_memory_edit(tmp_path):
    db = Database(tmp_path / "atomic.db")
    repo = MemoryRepository(db)
    original, _ = repo.upsert(MemoryExtractor().extract("I prefer Hinglish", explicit=True))
    with pytest.raises(ValueError), db.session():
        repo.forget(original["id"])
        raise ValueError("Rollback the edit")
    assert repo.get(original["id"])["is_active"]
    db.close()


def test_fts_disabled_memory_relevance_fallback(tmp_path):
    db = Database(tmp_path / "fallback.db")
    repo = MemoryRepository(db)
    repo.upsert(MemoryExtractor().extract("I use Premiere Pro", explicit=True))
    db.fts_available = False
    result = MemoryRetriever(repo).retrieve("Premiere workflow")
    assert result[0]["content"] == "User uses Premiere Pro."
    db.close()


def test_newer_schema_is_rejected_gracefully(tmp_path):
    path = tmp_path / "newer.db"
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA user_version=999"); connection.close()
    db = Database(path)
    with pytest.raises(DatabaseUnavailable):
        db.initialize()
    assert not db.status()["available"]
