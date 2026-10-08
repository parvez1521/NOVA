"""Tool metadata contract used by the registry and permission layer."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol


class PermissionLevel(StrEnum):
    SAFE = "safe"
    CONFIRM = "confirm"
    HIGH_RISK_CONFIRM = "high_risk_confirm"


class ToolExecutor(Protocol):
    async def __call__(self, arguments: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(slots=True)
class ToolDefinition:
    name: str
    description: str
    schema: dict[str, Any]
    permission_level: PermissionLevel
    execute: ToolExecutor
