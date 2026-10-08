import time
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.database.models import new_id, now


class ComputerError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(message)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentMode(StrEnum):
    ASSISTED = "ASSISTED"
    SEMI_AUTONOMOUS = "SEMI_AUTONOMOUS"
    AUTONOMOUS = "AUTONOMOUS"


class TaskStatus(StrEnum):
    QUEUED = "QUEUED"
    PLANNING = "PLANNING"
    OBSERVING = "OBSERVING"
    ACTING = "ACTING"
    VERIFYING = "VERIFYING"
    WAITING_FOR_CONFIRMATION = "WAITING_FOR_CONFIRMATION"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    INTERRUPTED = "INTERRUPTED"


class ComputerSettings(StrictModel):
    enabled: bool = False
    mode: AgentMode = AgentMode.SEMI_AUTONOMOUS
    simulation: bool = True
    screen_access: bool = False
    accessibility_access: bool = False
    browser_access: bool = True
    filesystem_access: bool = True
    terminal_access: bool = False
    vision_enabled: bool = True
    require_confirmation: bool = False
    task_timeout_seconds: int = Field(300, ge=10, le=3600)
    max_task_steps: int = Field(24, ge=1, le=100)
    local_vision_model: str = Field("", max_length=100, pattern=r"^[\w:./-]*$")
    allow_cloud_planning: bool = False
    action_visibility_mode: Literal["FAST","NORMAL","HUMAN_VISIBLE"] = "NORMAL"
    watch_nova: bool = False


class ComputerIntent(StrictModel):
    """Bounded goal contract shared by planning, execution, and verification."""

    user_goal: str
    task_kind: Literal["CHAT", "RESEARCH", "COMPUTER_USE", "CONNECTOR", "FILE", "CODING", "HYBRID"] = "COMPUTER_USE"
    target_app: str = ""
    target_bundle_id: str = ""
    target_object: str = ""
    requested_content: str = ""
    target_site: str = "web"
    intended_action: str = ""
    ordered_steps: list[str] = Field(default_factory=list, max_length=30)
    success_condition: str = ""
    verification_strategy: str = ""
    risk_level: Literal["low", "medium", "high"] = "low"
    confirmation_requirement: str = "none"
    missing_information: str = ""
    entity_resolution: Literal["none", "required", "ambiguous", "resolved"] = "none"


class UIElement(StrictModel):
    id: str
    role: str
    label: str = ""
    value: str = ""
    x: float = 0
    y: float = 0
    width: float = 0
    height: float = 0
    confidence: float = Field(0.95, ge=0, le=1)
    source: str = "accessibility"
    editable: bool = False
    secure: bool = False


class ScreenObservation(StrictModel):
    timestamp: str = Field(default_factory=now)
    screen_width: float = 0
    screen_height: float = 0
    active_app: str = ""
    active_bundle_id: str = ""
    active_pid: int = 0
    window_title: str = ""
    visible_text: str = ""
    elements: list[UIElement] = Field(default_factory=list)
    screenshot_path: str | None = None
    confidence: float = 0
    current_url: str = ""
    source: str = "native"
    fingerprint: str = ""
    blocked_reason: str | None = None
    focused_element: dict[str, Any] | None = None


class Action(StrictModel):
    tool: str = Field(min_length=1, max_length=80)
    arguments: dict[str, Any]
    expected_result: str = Field(min_length=1, max_length=300)
    verification: dict[str, str | bool | float] = Field(default_factory=dict)


class TaskDecision(StrictModel):
    decision: Literal["act", "complete", "ask"]
    steps: list[str] = Field(default_factory=list, max_length=30)
    action: Action | None = None
    message: str = Field("", max_length=1000)


class ToolResult(StrictModel):
    success: bool
    timestamp: str = Field(default_factory=now)
    duration_ms: float = 0
    data: dict[str, Any] = Field(default_factory=dict)
    error: dict[str, str] | None = None
    simulated: bool = False


class Task(StrictModel):
    id: str = Field(default_factory=new_id)
    goal: str
    status: TaskStatus = TaskStatus.QUEUED
    current_step: str = "Queued"
    step_index: int = 0
    total_steps: int = 0
    created_at: str = Field(default_factory=now)
    updated_at: str = Field(default_factory=now)
    mode: AgentMode = AgentMode.SEMI_AUTONOMOUS
    allowed_scope: dict[str, Any] = Field(default_factory=dict)
    permissions: list[str] = Field(default_factory=list)
    last_observation: ScreenObservation | None = None
    last_action: Action | None = None
    retry_count: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)
    intent: ComputerIntent | None = None


class KillSwitch:
    def __init__(self):
        self.cancelled = False

    def stop(self):
        self.cancelled = True

    def check(self):
        if self.cancelled:
            import asyncio
            raise asyncio.CancelledError
