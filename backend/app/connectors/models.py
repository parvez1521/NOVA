from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ConnectorStatus(StrEnum):
    CONNECTED = "CONNECTED"
    AVAILABLE = "AVAILABLE"
    NEEDS_SETUP = "NEEDS_SETUP"
    EXPIRED = "EXPIRED"
    UNAVAILABLE = "UNAVAILABLE"
    REQUIRES_REAUTH = "REQUIRES_REAUTH"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    PARTIAL = "PARTIAL"
    ERROR = "ERROR"


class AuthKind(StrEnum):
    NONE = "none"
    OAUTH2 = "oauth2"
    API_KEY = "api_key"
    BOT_TOKEN = "bot_token"
    MCP = "mcp"


class ConnectorDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=40, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    icon: str = Field(min_length=1, max_length=8)
    description: str = Field(min_length=1, max_length=240)
    auth: AuthKind
    permissions: list[str] = Field(default_factory=list, max_length=20)
    capabilities: list[str] = Field(default_factory=list, max_length=30)
    implemented: bool = False


class ConnectorHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool = False
    detail: str = Field(default="", max_length=240)


class ConnectorSnapshot(ConnectorDescriptor):
    status: ConnectorStatus
    connected: bool = False
    detail: str = Field(default="", max_length=240)
    health: ConnectorHealth


class ConnectorActionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    status: ConnectorStatus
    connected: bool = False
    detail: str = Field(default="", max_length=240)
    authorization_url: str | None = None


class ConnectorError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 409):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


ConnectorCapability = Literal["read", "write", "publish", "send", "delete", "admin"]
