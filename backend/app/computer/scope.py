from pathlib import Path

from app.computer.models import ComputerError, Task


class ScopeManager:
    """Filesystem scope defaults to the task's named Desktop/Downloads area."""

    def roots_for(self, goal: str) -> list[Path]:
        home = Path.home()
        lowered = goal.casefold()
        roots: list[Path] = []
        if "download" in lowered:
            roots.append(home / "Downloads")
        if "desktop" in lowered:
            roots.append(home / "Desktop")
        return roots

    def scope_for(self, task: Task) -> dict[str, object]:
        roots = self.roots_for(task.goal)
        task.allowed_scope = {"filesystem_roots": [str(root.resolve()) for root in roots], "network": "public_http_https"}
        return task.allowed_scope

    def validate(self, task: Task, value: str, *, allow_missing: bool = True) -> Path:
        path = Path(value).expanduser().resolve()
        if any(part.casefold() in {"username","yourusername","your_username","<username>","user"} for part in path.parts):
            raise ComputerError("PLACEHOLDER_PATH","Use the actual home path from the observation or ~/Desktop; placeholder usernames are invalid.")
        forbidden={".ssh",".aws",".gnupg",".config","keychains","cookies","login data"}
        if any(part.casefold() in forbidden or part.casefold()==".env" or part.casefold().startswith(".env.") for part in path.parts):
            raise ComputerError("SENSITIVE_PATH_BLOCKED","Credential stores and private configuration are outside computer-task access.")
        roots = [Path(root).resolve() for root in task.allowed_scope.get("filesystem_roots", [])]
        if not any(path == root or root in path.parents for root in roots):
            raise ComputerError("SCOPE_CONFIRMATION_REQUIRED", "That path is outside this task's allowed folder scope.")
        if not allow_missing and not path.exists():
            raise ComputerError("PATH_NOT_FOUND", "The requested path does not exist.")
        return path
