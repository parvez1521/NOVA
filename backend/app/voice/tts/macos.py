"""Native macOS speech synthesis and audio playback via say's stdin."""

import asyncio
import platform
import shutil

from app.voice.base import VoiceError
from app.voice.process import run_process
from app.voice.tts.base import TextToSpeechProvider
from app.voice.tts.cleaner import SpeechTextCleaner
from app.voice.tts.language import LanguageMode, LanguageResolver
from app.voice.tts.registry import VoiceRegistry, VoiceSelection


class MacOSTTSProvider(TextToSpeechProvider):
    provider_name = "macos"

    def __init__(self, *, voice: str = "", rate: int = 180, lock: asyncio.Lock | None = None,
                 language_mode: LanguageMode = "auto", voice_english: str = "", voice_hindi: str = "",
                 voice_fallback: str = "", registry: VoiceRegistry | None = None, read_urls: bool = False) -> None:
        self.voice = voice
        self.rate = rate
        self.lock = lock or asyncio.Lock()
        self.language_mode = language_mode
        self.voice_english = voice_english
        self.voice_hindi = voice_hindi
        self.voice_fallback = voice_fallback
        self.registry = registry or VoiceRegistry()
        self.resolver = LanguageResolver()
        self.read_urls = read_urls

    async def is_available(self) -> bool:
        return platform.system() == "Darwin" and shutil.which("say") is not None

    async def voices(self) -> list[dict[str, str]]:
        if not await self.is_available():
            return []
        return await self.registry.list_voices()

    async def _selection(self, text: str) -> tuple[VoiceSelection, dict[str, object]]:
        await self.registry.load()
        language = self.resolver.resolve(text, self.language_mode)
        selection = self.registry.select(language.language, english=self.voice_english,
            hindi=self.voice_hindi, fallback=self.voice_fallback, legacy=self.voice)
        return selection, {**language.as_dict(), **selection.as_dict()}

    async def speech_info(self, text: str) -> dict[str, object]:
        spoken = SpeechTextCleaner.clean(text, read_urls=self.read_urls)
        if not spoken:
            return {}
        _, metadata = await self._selection(spoken)
        return metadata

    async def speak(self, text: str) -> None:
        spoken = SpeechTextCleaner.clean(text, read_urls=self.read_urls)
        if not spoken:
            return
        if not await self.is_available():
            raise VoiceError("TTS_UNAVAILABLE", "macOS speech is unavailable; the text response is still available.")
        selection, _ = await self._selection(spoken)
        arguments = ["/usr/bin/say", "-r", str(self.rate), "-v", selection.voice.name]
        async with self.lock:
            try:
                await run_process(*arguments, timeout=max(30, len(spoken) / 5), input_data=spoken.encode("utf-8"))
            except (VoiceError, asyncio.TimeoutError) as exc:
                raise VoiceError("TTS_PROCESS_FAILED", "Native speech playback failed; the text response is still available.") from exc
