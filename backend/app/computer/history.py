import json

from app.computer.models import Task, TaskStatus
from app.database.db import Database
from app.database.models import now
from app.database.privacy import safe_text


class TaskHistoryRepository:
    def __init__(self, db: Database): self.db=db

    def save(self, task: Task, summary: str = "") -> None:
        # A screenshot answer may include OCR text for the visible response;
        # task history retains only its short action summary.
        summary=summary.split("\nVisible in ",1)[0][:1200]
        summary=summary.split("\nRead content: ",1)[0].split("\nClipboard: ",1)[0]
        with self.db.session() as connection:
            connection.execute("""INSERT INTO tasks(id,goal,status,summary,mode,created_at,updated_at,metadata_json)
                VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,summary=excluded.summary,updated_at=excluded.updated_at,metadata_json=excluded.metadata_json""",
                (task.id,safe_text(task.goal),task.status,safe_text(summary),task.mode,task.created_at,now(),json.dumps({
                    key:task.metadata[key] for key in ("simulation","verified_steps","error_code","conversation_id","intent","failure","task_id","pipeline_trace") if key in task.metadata
                })))

    def get(self, identifier: str) -> dict:
        with self.db.session() as connection:
            row=connection.execute("SELECT * FROM tasks WHERE id=?",(identifier,)).fetchone()
            if row is None: raise LookupError("Task not found")
            result=dict(row);result["metadata"]=json.loads(result.pop("metadata_json","{}"));return result

    def list(self, limit: int = 50) -> list[dict]:
        with self.db.session() as connection:
            rows=connection.execute("SELECT * FROM tasks ORDER BY updated_at DESC LIMIT ?",(limit,)).fetchall()
            return [self._row(row) for row in rows]

    @staticmethod
    def _row(row):
        result=dict(row);result["metadata"]=json.loads(result.pop("metadata_json","{}"));return result
