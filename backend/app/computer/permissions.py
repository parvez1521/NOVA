from dataclasses import dataclass

from app.computer.models import AgentMode, ComputerError, ComputerSettings, Task
from app.tools.base import PermissionLevel


@dataclass(frozen=True)
class PermissionDecision:
    allowed: bool
    requires_confirmation: bool = False
    reason: str = ""


class PermissionManager:
    def __init__(self, settings: ComputerSettings):
        self.settings = settings

    def capability_allowed(self, capability: str) -> bool:
        return {
            "screen": self.settings.screen_access,
            "accessibility": self.settings.accessibility_access,
            "browser": self.settings.browser_access,
            "filesystem": self.settings.filesystem_access,
            "terminal": self.settings.terminal_access,
            "clipboard": self.settings.enabled,
            "application": self.settings.enabled,
            "connector": self.settings.enabled,
        }.get(capability, False)

    def decide(self, task: Task, permission: PermissionLevel, capability: str) -> PermissionDecision:
        if not self.settings.enabled:
            return PermissionDecision(False, reason="Computer use is disabled in settings.")
        if not self.capability_allowed(capability):
            return PermissionDecision(False, reason=f"{capability.title()} access is disabled in settings.")
        if permission == PermissionLevel.SAFE and task.mode != AgentMode.ASSISTED and not self.settings.require_confirmation:
            return PermissionDecision(True)
        return PermissionDecision(False, requires_confirmation=True, reason="This action requires your confirmation.")

    def require(self, task: Task, permission: PermissionLevel, capability: str) -> None:
        decision = self.decide(task, permission, capability)
        if not decision.allowed:
            if decision.requires_confirmation:
                raise ConfirmationRequired(decision.reason)
            raise ComputerError("PERMISSION_REQUIRED", decision.reason)


class ConfirmationRequired(ComputerError):
    def __init__(self, message: str):
        super().__init__("CONFIRMATION_REQUIRED", message)
