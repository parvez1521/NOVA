"""Deterministic thinking policy for fast local conversations."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

ThinkingMode = Literal["off", "on", "smart"]

_COMPLEX_PATTERNS = (
    r"\banaly[sz]e\b",
    r"\bdebug(?:ging)?\b",
    r"\bfind\s+(?:the\s+)?bug\b",
    r"\bcompare\b.*\b(?:architecture|approach|option|design)\b",
    r"\barchitectures?\b",
    r"\bplan\b.*\b(?:project|system|app|software|roadmap)\b",
    r"\bcomplex\b",
    r"\bcomplicated\b",
    r"\breason\s+(?:through|about)\b",
    r"\bthink\s+(?:through|deeply)\b",
    r"\bsolve\b.*\b(?:difficult|hard|challenging|equation|math|problem)\b",
    r"\b(?:algorithm|code)\b.*\b(?:complexity|bug|trade.?off|performance)\b",
    r"\bstep[- ]by[- ]step\b",
    r"\btrade.?offs?\b",
)

_SIMPLE_PATTERNS = (
    r"^(?:hey\s+)?nova[,! ]+(?:who are you|how are you|good morning|good night)\b",
    r"\b(?:tell me a joke|make me laugh)\b",
    r"^(?:hey|hello|hi|good morning|good night|thanks|thank you)\b",
    r"\bexplain\s+what\s+\w+\s+is\b",
    r"\bwhat\s+is\s+\d+\s*[+\-*/x×]\s*\d+\??$",
    r"\b(?:what time is it|how are you|who are you)\??$",
    r"^(?:open|launch|start)\s+\w+(?:\s+\w+)?[.!?]?$",
)


@dataclass(frozen=True, slots=True)
class ThinkingDecision:
    enabled: bool
    max_tokens: int
    reason: str


def classify_thinking(
    text: str,
    *,
    mode: ThinkingMode = "smart",
    default: bool = False,
    simple_max_tokens: int = 256,
    normal_max_tokens: int = 512,
    complex_max_tokens: int = 1024,
    requested_mode: str = "normal",
) -> ThinkingDecision:
    """Choose thinking locally; never spend an LLM call classifying a request."""

    normalized = re.sub(r"\s+", " ", text.casefold()).strip()
    if mode == "off":
        return ThinkingDecision(False, simple_max_tokens, "configured_off")
    if mode == "on":
        return ThinkingDecision(True, complex_max_tokens, "configured_on")
    if requested_mode.casefold() == "complex":
        return ThinkingDecision(True, complex_max_tokens, "explicit_complex_mode")
    if any(re.search(pattern, normalized) for pattern in _COMPLEX_PATTERNS):
        return ThinkingDecision(True, complex_max_tokens, "complex_language")
    if any(re.search(pattern, normalized) for pattern in _SIMPLE_PATTERNS):
        return ThinkingDecision(False, simple_max_tokens, "simple_language")
    return ThinkingDecision(default, complex_max_tokens if default else normal_max_tokens, "smart_default")
