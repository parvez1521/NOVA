import json

from app.database.db import Database
from app.database.models import new_id, now
from app.database.privacy import unsafe_memory
from app.database.repository import fts_query, record, search_terms
from app.memory.extractor import MemoryCandidate, normalize_fact


class MemoryRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def get(self, identifier: str) -> dict:
        with self.db.session() as connection:
            row = connection.execute("SELECT * FROM memories WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise LookupError("Memory not found")
            return record(row)

    def by_fact_key(self, key: str) -> dict | None:
        with self.db.session() as connection:
            return record(connection.execute("SELECT * FROM memories WHERE is_active=1 AND json_extract(metadata_json,'$.fact_key')=?", (key,)).fetchone())

    def active_all(self) -> list[dict]:
        with self.db.session() as connection:
            return [record(row) for row in connection.execute("SELECT * FROM memories WHERE is_active=1 ORDER BY category,updated_at DESC").fetchall()]

    def list(self, *, include_inactive: bool = False, limit: int = 200, offset: int = 0) -> list[dict]:
        with self.db.session() as connection:
            rows = connection.execute("SELECT * FROM memories WHERE (? OR is_active=1) ORDER BY category,updated_at DESC LIMIT ? OFFSET ?", (int(include_inactive), limit, offset)).fetchall()
            return [record(row) for row in rows]

    def upsert(self, candidate: MemoryCandidate) -> tuple[dict, str]:
        if unsafe_memory(candidate.content) or not candidate.content.strip():
            raise ValueError("Credentials cannot be saved as memory")
        timestamp = now()
        with self.db.session() as connection:
            previous = connection.execute("SELECT * FROM memories WHERE is_active=1 AND json_extract(metadata_json,'$.fact_key')=?", (candidate.fact_key,)).fetchone()
            if previous is None:
                # Basic normalized comparison catches close wording without embeddings.
                rows = connection.execute("SELECT * FROM memories WHERE is_active=1 AND category=?", (candidate.category,)).fetchall()
                words = set(normalize_fact(candidate.content).split())
                for row in rows:
                    other = set(normalize_fact(row["content"]).split())
                    if words and words == other:
                        previous = row
                        break
            if previous:
                metadata = json.loads(previous["metadata_json"])
                if metadata.get("fact_value") == candidate.fact_value or normalize_fact(previous["content"]) == normalize_fact(candidate.content):
                    promoted = candidate.confidence > previous["confidence"]
                    connection.execute("UPDATE memories SET confidence=max(confidence,?),source=?,updated_at=? WHERE id=?", (candidate.confidence, candidate.source if promoted else previous["source"], timestamp, previous["id"]))
                    return self.get(previous["id"]), "updated" if promoted else "unchanged"
                connection.execute("UPDATE memories SET is_active=0,updated_at=? WHERE id=?", (timestamp, previous["id"]))
            identifier = new_id()
            metadata = {"fact_key": candidate.fact_key, "fact_value": candidate.fact_value}
            connection.execute("INSERT INTO memories(id,category,content,source,confidence,created_at,updated_at,metadata_json) VALUES(?,?,?,?,?,?,?,?)", (identifier, candidate.category, candidate.content, candidate.source, candidate.confidence, timestamp, timestamp, json.dumps(metadata)))
            return self.get(identifier), "updated" if previous else "created"

    def edit(self, identifier: str, candidate: MemoryCandidate) -> tuple[dict, str]:
        self.get(identifier)
        with self.db.session() as connection:
            connection.execute("UPDATE memories SET is_active=0,updated_at=? WHERE id=?", (now(), identifier))
            memory, _ = self.upsert(candidate)
            return memory, "updated"

    def forget(self, identifier: str) -> None:
        self.get(identifier)
        with self.db.session() as connection:
            connection.execute("UPDATE memories SET is_active=0,updated_at=? WHERE id=?", (now(), identifier))

    def delete(self, identifier: str) -> None:
        self.get(identifier)
        with self.db.session() as connection:
            connection.execute("DELETE FROM memories WHERE id=?", (identifier,))

    def clear(self) -> int:
        with self.db.session() as connection:
            count = connection.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            connection.execute("DELETE FROM memories")
            return count

    def candidates(self, query: str, *, preferences: bool = True, categories: list[str] | None = None, limit: int = 60) -> list[dict]:
        terms = search_terms(query)
        with self.db.session() as connection:
            rows = []
            if preferences:
                rows.extend(connection.execute("SELECT * FROM memories WHERE is_active=1 AND json_extract(metadata_json,'$.fact_key') IN ('communication:response_language','communication:response_length') LIMIT 10").fetchall())
            for category in categories or []:
                rows.extend(connection.execute("SELECT * FROM memories WHERE is_active=1 AND category=? ORDER BY confidence DESC,updated_at DESC LIMIT 10", (category,)).fetchall())
            if terms:
                if self.db.fts_available:
                    rows.extend(connection.execute("SELECT m.* FROM memory_search JOIN memories m ON m.rowid=memory_search.rowid WHERE memory_search MATCH ? AND m.is_active=1 LIMIT ?", (fts_query(query), limit)).fetchall())
                else:
                    predicate = " OR ".join("content LIKE ?" for _ in terms)
                    rows.extend(connection.execute(f"SELECT * FROM memories WHERE is_active=1 AND ({predicate}) LIMIT ?", (*[f"%{term}%" for term in terms], limit)).fetchall())
            return list({row["id"]: record(row) for row in rows}.values())

    def used(self, identifiers: list[str]) -> None:
        if identifiers:
            with self.db.session() as connection:
                connection.executemany("UPDATE memories SET last_used_at=? WHERE id=?", [(now(), identifier) for identifier in identifiers])

    def export(self) -> list[dict]:
        with self.db.session() as connection:
            return [record(row) for row in connection.execute("SELECT * FROM memories ORDER BY created_at").fetchall()]
