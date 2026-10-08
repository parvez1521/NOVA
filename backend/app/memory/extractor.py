"""Conservative explicit/automatic factual extraction. No model or network call."""

import hashlib
import re
import unicodedata
from dataclasses import dataclass

from app.database.privacy import unsafe_memory, visible_text
from app.voice.tts.cleaner import SpeechTextCleaner

_TEMPORARY = re.compile(r"\b(?:tomorrow|today|yesterday|tonight|next week|this week|remind|reminder|deadline|temporarily|for now|this (?:message|reply|response|chat)|maybe|might|sometimes)\b", re.I)
_TOOLS = r"Premiere(?: Pro)?|After Effects|DaVinci Resolve|Final Cut Pro|Python|VS Code|Photoshop|Blender|Ollama|macOS|Windows|Linux"


def normalize_fact(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    return " ".join(re.findall(r"[^\W_]+", text, re.UNICODE))


@dataclass(frozen=True)
class MemoryCandidate:
    category: str
    content: str
    confidence: float
    fact_key: str
    fact_value: str
    source: str


class MemoryExtractor:
    def extract(self, text: str, *, explicit: bool = False, source: str | None = None) -> MemoryCandidate | None:
        if unsafe_memory(text) or len(text) > 1000:
            return None
        if not explicit and ("?" in text or "\n" in text):
            return None
        if not explicit and re.search(r"\b(?:if|unless|but|except|depending|not|don't|no longer)\b", text, re.I):
            return None
        text = SpeechTextCleaner.clean(visible_text(text)).strip(" .!?।")
        if not text or (not explicit and (_TEMPORARY.search(text) or "?" in text or "\n" in text)):
            return None
        # Canonical strings from the memory editor can go through the same rules.
        text = re.sub(r"^User's\s+", "My ", text, flags=re.I)
        for verb, first_person in [("prefers", "prefer"), ("likes", "like"), ("uses", "use"), ("is building", "am building"), ("wants", "want")]:
            text = re.sub(rf"^User\s+{verb}\b", f"I {first_person}", text, flags=re.I)
        text = re.sub(r"^User usually works\b", "I usually work", text, flags=re.I)
        text = re.sub(r"^User works on\b", "I work on", text, flags=re.I)
        text = re.sub(r"^actually[\s,]+", "", text, flags=re.I)
        confidence = 1.0 if explicit else 0.9
        source = source or ("explicit" if explicit else "automatic")

        def fact(category: str, content: str, key: str, value: str, strength: float = confidence):
            return MemoryCandidate(category, content, 1.0 if explicit else strength, key, normalize_fact(value), source)

        if re.fullmatch(r"I (?:prefer|like|want) (?:a )?(?:mix|mixture) of (?:Hindi and English|English and Hindi)(?: (?:in|for) (?:your |my |the )?(?:replies|answers|responses))?", text, re.I):
            return fact("communication", "User prefers Hinglish replies.", "communication:response_language", "Hinglish")
        if match := re.match(r"^(?:I (?:prefer|like|want)|my (?:preferred )?(?:response |reply )?language is)\s+(Hinglish|Hindi|English)\b(?:\s+(?:replies|responses|answers|replying))?(?:\s+.*)?$", text, re.I):
            language = match[1].capitalize()
            return fact("communication", f"User prefers {language} replies.", "communication:response_language", language)
        if re.fullmatch(r"I (?:prefer|like|want) (?:short|concise|brief)(?:er)? (?:answers|replies|responses)(?:\s+.*)?", text, re.I):
            return fact("communication", "User prefers concise answers.", "communication:response_length", "concise")
        if re.fullmatch(r"I (?:prefer|like|want) (?:long|detailed|thorough) (?:answers|replies|responses)(?:\s+.*)?", text, re.I):
            return fact("communication", "User prefers detailed answers.", "communication:response_length", "detailed")
        if re.fullmatch(r"I (?:prefer|like|want) local[- ]first (?:tools|software|apps)(?:\s+.*)?", text, re.I):
            return fact("preference", "User prefers local-first tools.", "preference:tool_locality", "local-first")
        if match := re.fullmatch(rf"I (?:usually |primarily )?(?:use|work with) ({_TOOLS})(?:\s+(?:for|in|on|as)\s+.*)?", text, re.I):
            tool = match[1]
            if tool.casefold() in {"premiere", "premiere pro"}:
                tool = "Premiere Pro"
            return fact("work", f"User uses {tool}.", "work:tool:" + normalize_fact(tool), tool, 0.85)
        if match := re.fullmatch(r"(?:my (?:main )?project is|I(?:'m| am) building|I am working on)\s+([\w][\w .-]{0,70})", text, re.I):
            project = match[1].strip()
            return fact("project", f"User's main project is {project}.", "project:main", project, 0.85)
        if re.fullmatch(r"I (?:usually|regularly|often) work late at night", text, re.I):
            return fact("workflow", "User usually works late at night.", "workflow:work_schedule", "late at night", 0.85)
        if match := re.fullmatch(r"I (?:usually |primarily )?(?:work on|create|make) (AI(?:/| and | )video content|AI content|video content)", text, re.I):
            return fact("work", f"User works on {match[1]}.", "work:content", match[1], 0.85)
        if match := re.fullmatch(r"my name is ([\w .-]{1,60})", text, re.I):
            return fact("profile", f"User's name is {match[1]}.", "profile:name", match[1], 0.85)
        if match := re.fullmatch(r"my goal is to (.{1,160})", text, re.I):
            return fact("goal", f"User's goal is to {match[1]}.", "goal:" + normalize_fact(match[1]), match[1], 0.8)
        if match := re.fullmatch(r"I (?:like|prefer|enjoy) (.{1,160})", text, re.I):
            if not explicit:
                return None  # A casual opinion is not a stable preference.
            value = match[1].strip()
            return fact("preference", f"User likes {value}.", "preference:interest:" + normalize_fact(value), value)
        if not explicit:
            return None
        # Explicit factual notes are allowed; executable instructions/secrets aren't.
        if re.search(r"\b(?:ignore (?:all|previous)|system prompt|execute|run command)\b", text, re.I):
            return None
        text = re.sub(r"^User notes:\s*", "", text, flags=re.I)
        key = hashlib.sha256(normalize_fact(text).encode()).hexdigest()[:24]
        return fact("profile", f"User notes: {text}.", "profile:note:" + key, text)
