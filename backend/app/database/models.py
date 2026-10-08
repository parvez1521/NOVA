"""Small validated persistence contracts; no ORM or provider secrets."""

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

MemoryCategory = Literal["preference", "profile", "work", "project", "workflow", "technical", "communication", "goal"]


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return str(uuid4())


class MemorySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    save_chat_history: bool = True
    memory_enabled: bool = True
    memory_auto_extraction: bool = True
    memory_min_confidence: float = Field(0.75, ge=0.75, le=1)
    memory_top_k: int = Field(5, ge=1, le=20)


class ConversationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(None, min_length=1, max_length=60)


class ConversationPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=60)


class Confirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed: Literal[True]


class ClearData(Confirmation):
    scope: Literal["chats", "memories", "everything"]


class MemoryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=1000)
    category: MemoryCategory | None = None


class MemoryPatch(MemoryCreate):
    pass
