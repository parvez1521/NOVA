"""Standard-library SQLite persistence for local chat history and user memory."""

from app.database.db import Database, DatabaseUnavailable

__all__ = ["Database", "DatabaseUnavailable"]
