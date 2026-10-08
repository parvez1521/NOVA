"""Persistence boundary: visible prose only, with credentials omitted entirely."""

import re
from uuid import UUID

_PRIVATE = re.compile(r"<(think|thinking|analysis|reasoning|tool_call|tool_result|debug|metadata)\b[^>]*>.*?(?:</\1\s*>|$)", re.I | re.S)
_CREDENTIAL = re.compile(
    r"\b(?:sk-[\w.\-]+|gh[pousr]_[\w]+|github_pat_[\w]+|xox[baprs]-[\w-]+)"
    r"|-----BEGIN (?:\w+ )?PRIVATE KEY-----"
    r"|\beyJ[\w-]+\.[\w-]+\.[\w-]+"
    r"|\b(?:[a-z][a-z0-9]*_)*(?:api[ _-]?key|password|passwd|auth(?:entication)?[ _-]?token|access[ _-]?token|refresh[ _-]?token|token|secret[ _-]?key|private[ _-]?(?:encryption[ _-]?)?key|session[ _-]?(?:id|token)|browser[ _-]?cookies?|credit[ _-]?card(?:[ _-]?number)?)\b[\"']?\s*(?:is|=|:|are)\s*[\"']?\S+"
    r"|\b(?:my|our)\s+(?:api[ _-]?key|password|private key|authentication token|token|credit card|browser cookie)\b"
    r"|\bauthorization[\"']?\s*:\s*[\"']?(?:bearer|basic)\s+\S+"
    r"|(?:^|\n)\s*(?:cookie|set-cookie)\s*:\s*\S+"
    r"|\b(?:otp|2fa[ -]?code|one[ -]?time[ -]?(?:code|password)|verification[ -]?code|security[ -]?code|passcode|pin|cvv|cvc)\b\s*(?:(?:is|=|:)\s*)?\d{3,8}\b",
    re.I,
)
_SENSITIVE_TOPIC = re.compile(r"(?<![a-zA-Z0-9])(?:api[ _-]?keys?|passwords?|passwd|credentials?|auth(?:entication)?|tokens?|private key|secret|credit card|cookies?)\b", re.I)


def has_secret(text: str) -> bool:
    if _CREDENTIAL.search(text):
        return True
    for candidate in re.findall(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)", text):
        digits = [int(character) for character in candidate if character.isdigit()]
        if 13 <= len(digits) <= 19 and len(set(digits)) > 1:
            total = sum((value * 2 - 9 if value * 2 > 9 else value * 2) if index % 2 == len(digits) % 2 else value for index, value in enumerate(digits))
            if total % 10 == 0:
                return True
    return False


def unsafe_memory(text: str) -> bool:
    return has_secret(text) or bool(_SENSITIVE_TOPIC.search(text))


def visible_text(text: str) -> str:
    text = _PRIVATE.sub("", text)
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text).strip()


def safe_text(text: str) -> str:
    # Omit the complete secret-bearing message rather than guessing secret bounds.
    return "[Sensitive input omitted]" if has_secret(text) else visible_text(text)


def safe_metadata(metadata: dict | None) -> dict:
    allowed = {"request_id", "status", "redacted", "title_source", "memory_command", "memory_id", "fact_key", "fact_value"}
    result = {key: value for key, value in (metadata or {}).items() if key in allowed and isinstance(value, (str, bool, int, float)) and not has_secret(str(value))}
    identifiers = (metadata or {}).get("memory_ids", [])
    if isinstance(identifiers, list):
        safe = []
        for identifier in identifiers[:20]:
            try:
                safe.append(str(UUID(str(identifier))))
            except ValueError:
                continue
        if safe:
            result["memory_ids"] = safe
    return result
