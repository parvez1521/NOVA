"""Cached native voice inventory. Selection never delegates to an unknown OS default."""

import asyncio
import re
from dataclasses import dataclass

from app.voice.process import run_process
from app.voice.base import VoiceError


@dataclass(frozen=True, slots=True)
class Voice:
    name: str
    locale: str

    @property
    def language(self) -> str:
        return re.split(r"[_-]", self.locale.lower())[0]


@dataclass(frozen=True, slots=True)
class VoiceSelection:
    voice: Voice
    fallback_used: bool
    reason: str

    def as_dict(self) -> dict[str, object]:
        return {
            "selected_tts_voice": self.voice.name,
            "tts_language": self.voice.language,
            "tts_voice_locale": self.voice.locale,
            "fallback_used": self.fallback_used,
            "voice_selection_reason": self.reason,
        }


class VoiceRegistry:
    def __init__(self, inventory: str | None = None) -> None:
        self._voices = self.parse(inventory) if inventory is not None else []
        self._loaded = inventory is not None
        self._lock = asyncio.Lock()

    @staticmethod
    def parse(output: str) -> list[Voice]:
        voices: list[Voice] = []
        for line in output.splitlines():
            match = re.match(r"^(.+?)\s+([a-z]{2,3}[_-][A-Za-z0-9_-]+)\s+#", line)
            if match:
                voices.append(Voice(match[1].strip(), match[2]))
        return voices

    async def load(self, *, refresh: bool = False) -> None:
        async with self._lock:
            if self._loaded and not refresh:
                return
            output = await run_process("/usr/bin/say", "-v", "?", timeout=10)
            self._voices = self.parse(output.decode("utf-8", errors="replace"))
            self._loaded = True

    async def list_voices(self) -> list[dict[str, str]]:
        await self.load()
        return [{"name": voice.name, "language": voice.locale} for voice in self._voices]

    def find_voice_for_language(self, language: str, preferred: str = "", *, indian_english: bool = False) -> Voice | None:
        language = "en" if language == "hinglish" else language
        matches = [voice for voice in self._voices if voice.language == language]
        if preferred:
            selected = next((voice for voice in matches if voice.name.casefold() == preferred.casefold()), None)
            if selected:
                return selected
        # Names are only preferences among discovered installed voices, never assumptions.
        preferences = ("Rishi", "Aman", "Tara", "Samantha", "Alex", "Daniel") if indian_english else ("Samantha", "Alex", "Daniel", "Rishi")
        if language == "en":
            for name in preferences:
                selected = next((voice for voice in matches if voice.name == name), None)
                if selected:
                    return selected
        return matches[0] if matches else None

    def get_default_voice(self) -> Voice | None:
        """Safe app default, not the possibly Chinese system default."""
        return self.find_voice_for_language("en")

    def is_language_supported(self, language: str) -> bool:
        return self.find_voice_for_language(language) is not None

    def select(self, language: str, *, english: str = "", hindi: str = "", fallback: str = "", legacy: str = "") -> VoiceSelection:
        target = "hi" if language == "hi" else "en"
        configured = hindi if target == "hi" else english
        # Legacy selection is honored only if its discovered locale fits the answer.
        preferred = configured or legacy
        exact = next((voice for voice in self._voices if voice.name.casefold() == preferred.casefold() and voice.language == target), None) if preferred else None
        if exact:
            return VoiceSelection(exact, False, "configured_language_voice")
        installed = self.find_voice_for_language(target, indian_english=language == "hinglish")
        if installed:
            return VoiceSelection(installed, bool(preferred), "configured_voice_unavailable_or_wrong_language" if preferred else "discovered_language_voice")
        # Hindi is missing: configured English fallback, then a discovered English voice.
        safe_fallback = next((voice for voice in self._voices if voice.name.casefold() == fallback.casefold() and voice.language == "en"), None) if fallback else None
        english_voice = safe_fallback or self.find_voice_for_language("en", english, indian_english=language == "hinglish")
        if english_voice:
            return VoiceSelection(english_voice, True, "hindi_voice_unavailable" if target == "hi" else "english_fallback")
        raise VoiceError("TTS_LANGUAGE_UNAVAILABLE", "No installed English/Hindi voice is available. The response remains visible as text.")
