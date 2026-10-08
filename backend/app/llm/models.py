"""Public model metadata and routing settings; never credentials or prompts."""

from __future__ import annotations

import re
import time
from collections import deque
from statistics import median
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ModelCapabilities(BaseModel):
    # None means unknown. It must never satisfy a required capability.
    text: bool | None = None
    streaming: bool | None = None
    reasoning: bool | None = None
    vision: bool | None = None
    audio_input: bool | None = None
    audio_output: bool | None = None
    tools: bool | None = None
    function_calling: bool | None = None
    structured_output: bool | None = None
    computer_use: bool | None = None
    search: bool | None = None
    context_length: int | None = Field(None, ge=1)


class ModelDescriptor(BaseModel):
    provider: str
    model: str
    name: str = ""
    capabilities: ModelCapabilities = Field(default_factory=ModelCapabilities)
    availability: Literal["AVAILABLE", "UNAVAILABLE", "UNKNOWN"] = "UNKNOWN"
    pricing_state: Literal["LOCAL_FREE", "FREE", "PAID", "UNKNOWN"] = "UNKNOWN"
    local: bool = False
    last_checked: float | None = None
    pricing_source: str = ""
    supported_methods: list[str] = Field(default_factory=list)
    input_modalities: list[str] = Field(default_factory=list)
    output_modalities: list[str] = Field(default_factory=list)
    output_limit: int | None = None
    quality: float | None = None  # Only provider/benchmark metadata, never a name-based guess.
    detail: str = ""

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.model}"


class ModelRegistry:
    """In-memory active registry; provider catalogs own their sanitized caches."""

    def __init__(self) -> None:
        self._models: dict[str, ModelDescriptor] = {}

    def upsert(self, descriptor: ModelDescriptor) -> None:
        self._models[descriptor.key] = descriptor

    def replace_provider(self, provider: str, descriptors: list[ModelDescriptor]) -> None:
        for key in [key for key, item in self._models.items() if item.provider == provider]:
            self._models.pop(key, None)
        for descriptor in descriptors:
            self.upsert(descriptor)

    def get(self, provider: str, model: str) -> ModelDescriptor | None:
        return self._models.get(f"{provider}:{model}")

    def all(self) -> list[ModelDescriptor]:
        return list(self._models.values())

    def snapshot(self) -> list[dict]:
        return [item.model_dump() for item in sorted(self._models.values(), key=lambda item: item.key)]


class RoutingSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["LOCAL_ONLY", "FREE_ONLY", "BALANCED", "BEST_AVAILABLE", "OFFLINE"] = "BALANCED"
    free_only: bool = True
    zero_budget: bool = True
    privacy_mode: Literal["LOCAL_FIRST", "STRICT_LOCAL", "CLOUD_ALLOWED"] = "LOCAL_FIRST"
    jury_mode: bool = False
    preferred_local_model: str = Field("", max_length=200)
    preferred_cloud_model: str = Field("", max_length=200)


TaskType = Literal["chat", "reasoning", "coding", "vision", "research", "summarization", "creative", "computer_use", "transcription", "translation"]


def detect_task(text: str) -> TaskType:
    """No classifier API call, name ranking or claim of live research evidence."""
    value = text.casefold()
    rules = (
        ("vision", r"screenshot|look at my screen|what.*(?:on|in).*screen|analy[sz]e.*image"),
        ("transcription", r"transcrib|speech.to.text"),
        ("computer_use", r"^(?:(?:hey|hello) nova[, ]+)?(?:open|launch|click|type|move|create a folder)\b|chrome kholo"),
        ("translation", r"\btranslat(?:e|ion)\b"),
        ("coding", r"\b(?:code|debug|program|refactor|function|pull request|code review)\b"),
        ("research", r"\bresearch\b|\blatest\b.*\b(?:tools|news|developments)\b"),
        ("summarization", r"\bsummari[sz]e\b|\bsummary\b"),
        ("reasoning", r"\b(?:architecture|complex|reason|trade.?offs|equation)\b"),
        ("creative", r"\b(?:joke|poem|story|creative|lyrics)\b"),
    )
    return next((kind for kind, pattern in rules if re.search(pattern, value)), "chat")


def task_requirements(kind: TaskType) -> set[str]:
    required = {"text", "streaming"}
    if kind == "vision": required.add("vision")
    if kind == "transcription": required.add("audio_input")
    if kind == "reasoning": required.add("reasoning")
    if kind == "computer_use": required.update({"structured_output", "tools"})
    if kind == "research": required.add("search")
    return required


class LatencyStats:
    """Bounded recent measurements, not prompts. Three samples before ranking."""
    def __init__(self):
        self.samples: dict[str, deque] = {}
        self.failed_until: dict[str, float] = {}

    def record(self, key: str, ttft: float | None, generation: float):
        self.samples.setdefault(key, deque(maxlen=20)).append((time.time(), ttft, generation))
        self.failed_until.pop(key, None)

    def fail(self, key: str):
        self.failed_until[key] = time.time() + 30

    def healthy(self, key: str) -> bool:
        return self.failed_until.get(key, 0) <= time.time()

    def summary(self, key: str) -> dict:
        samples = [s for s in self.samples.get(key, ()) if time.time() - s[0] < 3600]
        ttfts = [s[1] for s in samples if s[1] is not None]
        return {"samples": len(samples), "ttft_ms": median(ttfts) if ttfts else None,
                "generation_ms": median(s[2] for s in samples) if samples else None}

    def bucket(self, key: str) -> int:
        stats = self.summary(key)
        # 500ms buckets suppress tiny timing changes; sparse data is neutral.
        return int((stats["ttft_ms"] or 0) // 500) if stats["samples"] >= 3 else 0
