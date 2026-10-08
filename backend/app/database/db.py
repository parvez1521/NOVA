"""Standard-library SQLite, WAL, bounded waits and serialized worker-thread access."""

import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from app.database.migrations import SCHEMA_VERSION, migrate


class DatabaseUnavailable(RuntimeError):
    pass


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._connection: sqlite3.Connection | None = None
        self._lock = threading.RLock()
        self.fts_available = False
        self.last_error: str | None = None
        self._depth = 0

    def initialize(self) -> None:
        with self._lock:
            if self._connection is not None:
                return
            connection = None
            try:
                if self.path != ":memory:":
                    path = Path(self.path)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
                    os.close(descriptor)
                    path.chmod(0o600)
                connection = sqlite3.connect(self.path, timeout=0.2, check_same_thread=False)
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA synchronous=NORMAL")
                self.fts_available = migrate(connection)
                self._connection = connection
                self.last_error = None
            except (OSError, sqlite3.Error) as exc:
                if connection is not None:
                    connection.close()
                self.last_error = "Local persistence temporarily unavailable"
                raise DatabaseUnavailable(self.last_error) from exc

    @contextmanager
    def session(self):
        with self._lock:
            self.initialize()
            self._depth += 1
            try:
                yield self._connection
                if self._depth == 1:
                    self._connection.commit()
                self.last_error = None
            except sqlite3.Error as exc:
                self._connection.rollback()
                self.last_error = "Local persistence temporarily unavailable"
                raise DatabaseUnavailable(self.last_error) from exc
            except BaseException:
                self._connection.rollback()
                raise
            finally:
                self._depth -= 1

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    def status(self) -> dict:
        return {"available": self._connection is not None and self.last_error is None,
                "fts5": self.fts_available, "schema_version": SCHEMA_VERSION, "warning": self.last_error}
