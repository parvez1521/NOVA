"""Versioned, transactional SQLite schema and optional external-content FTS5."""

import sqlite3

SCHEMA_VERSION = 2
SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY, title TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1)),
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK(role IN ('user','assistant')), content TEXT NOT NULL,
    timestamp TEXT NOT NULL, provider TEXT, model TEXT,
    input_type TEXT NOT NULL CHECK(input_type IN ('text','voice')),
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    category TEXT NOT NULL CHECK(category IN ('preference','profile','work','project','workflow','technical','communication','goal')),
    content TEXT NOT NULL, source TEXT NOT NULL,
    confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_used_at TEXT,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1)),
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS messages_conversation ON messages(conversation_id, timestamp);
CREATE INDEX IF NOT EXISTS messages_timestamp ON messages(timestamp);
CREATE INDEX IF NOT EXISTS memories_category ON memories(category);
CREATE INDEX IF NOT EXISTS memories_active ON memories(is_active, category);
CREATE INDEX IF NOT EXISTS conversations_updated ON conversations(updated_at);
CREATE INDEX IF NOT EXISTS conversations_title ON conversations(title COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS memories_content ON memories(content COLLATE NOCASE);
CREATE UNIQUE INDEX IF NOT EXISTS memories_active_fact ON memories(json_extract(metadata_json, '$.fact_key')) WHERE is_active=1;
"""

TASKS_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY, goal TEXT NOT NULL, status TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '', mode TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS tasks_updated ON tasks(updated_at);
CREATE INDEX IF NOT EXISTS tasks_status ON tasks(status);
"""


def migrate(connection: sqlite3.Connection) -> bool:
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise sqlite3.DatabaseError("Unsupported newer database schema")
    if version < 1:
        connection.executescript("BEGIN IMMEDIATE;\n" + SCHEMA + "\nPRAGMA user_version=1;\nCOMMIT;")
        version = 1
    if version < 2:
        connection.executescript("BEGIN IMMEDIATE;\n" + TASKS_SCHEMA + "\nPRAGMA user_version=2;\nCOMMIT;")
    try:
        for table, index, column in [("messages", "message_search", "content"), ("conversations", "conversation_search", "title"), ("memories", "memory_search", "content")]:
            exists = connection.execute("SELECT 1 FROM sqlite_master WHERE name=?", (index,)).fetchone()
            connection.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS {index} USING fts5({column}, content='{table}', content_rowid='rowid', tokenize='unicode61')")
            connection.executescript(f"""
                CREATE TRIGGER IF NOT EXISTS {table}_fts_insert AFTER INSERT ON {table} BEGIN
                    INSERT INTO {index}(rowid,{column}) VALUES(new.rowid,new.{column}); END;
                CREATE TRIGGER IF NOT EXISTS {table}_fts_delete AFTER DELETE ON {table} BEGIN
                    INSERT INTO {index}({index},rowid,{column}) VALUES('delete',old.rowid,old.{column}); END;
                CREATE TRIGGER IF NOT EXISTS {table}_fts_update AFTER UPDATE ON {table} BEGIN
                    INSERT INTO {index}({index},rowid,{column}) VALUES('delete',old.rowid,old.{column});
                    INSERT INTO {index}(rowid,{column}) VALUES(new.rowid,new.{column}); END;
            """)
            if not exists:
                connection.execute(f"INSERT INTO {index}({index}) VALUES('rebuild')")
        connection.commit()
        return True
    except sqlite3.OperationalError as exc:
        if "no such module: fts5" not in str(exc).lower():
            raise
        connection.rollback()
        return False
