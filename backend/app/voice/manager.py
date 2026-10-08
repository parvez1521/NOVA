"""Voice provider factory, runtime settings, and speech sentence buffering."""

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from enum import StrEnum
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.voice.base import STT_PROFILE_MODELS, VoiceError, VoiceSettings
from app.voice.stt.base import SpeechToTextProvider
from app.voice.stt.whisper import LocalWhisperProvider
from app.voice.tts.base import TextToSpeechProvider
from app.voice.tts.macos import MacOSTTSProvider
from app.voice.tts.cleaner import SpeechTextCleaner
from app.voice.tts.registry import VoiceRegistry


class SentenceBuffer:
    def __init__(self) -> None:
        self.pending = ""

    def feed(self, token: str) -> list[str]:
        self.pending += token
        sentences: list[str] = []
        # Wait for following whitespace, so decimals/abbreviations don't split by token.
        while match := re.search(r"[.!?।](?:[\"'”’)]*)\s+|\n", self.pending):
            end = match.end()
            part = self.pending[:end].strip()
            self.pending = self.pending[end:]
            if part:
                sentences.append(part)
        if len(self.pending) > 240:
            end = self.pending.rfind(" ", 0, 240)
            if end > 0:
                sentences.append(self.pending[:end].strip())
                self.pending = self.pending[end:].lstrip()
        return sentences

    def flush(self) -> list[str]:
        result = [self.pending.strip()] if self.pending.strip() else []
        self.pending = ""
        return result


class SpeechState(StrEnum):
    IDLE = "TTS_IDLE"
    QUEUED = "TTS_QUEUED"
    PLAYING = "TTS_PLAYING"
    STOPPED = "TTS_STOPPED"
    ERROR = "TTS_ERROR"


SpeechEmitter = Callable[..., Awaitable[None]]


class SpeechQueue:
    """One request's sequential audio queue, concurrent with LLM generation."""

    def __init__(self, provider: TextToSpeechProvider, emit: SpeechEmitter, *, read_urls: bool = False) -> None:
        self.provider = provider
        self.emit = emit
        self.queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=32)
        self.state = SpeechState.IDLE
        self.failed = False
        self.started = False
        self.read_urls = read_urls
        self.cleaner = SpeechTextCleaner(read_urls=read_urls)
        self.sentences = SentenceBuffer()
        self.original_answer_length = 0
        self.cleaned_speech_length = 0
        self.task = asyncio.create_task(self._worker())

    async def feed(self, token: str) -> None:
        self.original_answer_length += len(token)
        for sentence in self.sentences.feed(self.cleaner.feed(token)):
            await self.enqueue(sentence, from_stream=True)

    def cleanup_info(self) -> dict[str, int]:
        return {"original_answer_length": self.original_answer_length,
                "cleaned_speech_length": self.cleaned_speech_length,
                "speech_chars_removed": max(0, self.original_answer_length - self.cleaned_speech_length)}

    async def enqueue(self, text: str, *, from_stream: bool = False) -> None:
        if not from_stream:
            self.original_answer_length += len(text)
        spoken = SpeechTextCleaner.clean(text, read_urls=self.read_urls)
        if spoken and not self.failed and self.state != SpeechState.STOPPED:
            self.state = SpeechState.QUEUED
            self.cleaned_speech_length += len(spoken)
            await self.queue.put(spoken)

    async def _worker(self) -> None:
        try:
            while True:
                sentence = await self.queue.get()
                if sentence is None:
                    break
                metadata = await self.provider.speech_info(sentence)
                if not self.started:
                    await self.emit("tts.started", provider=self.provider.provider_name, **metadata)
                    self.started = True
                self.state = SpeechState.PLAYING
                await self.emit("tts.sentence", state=self.state, queue_length=self.queue.qsize(), **metadata, **self.cleanup_info())
                started_at = time.perf_counter()
                await self.provider.speak(sentence)
                await self.emit("tts.sentence_completed", queue_length=self.queue.qsize(), latency_ms=round((time.perf_counter() - started_at) * 1000, 2))
            self.state = SpeechState.IDLE
            await self.emit("tts.cleanup", **self.cleanup_info())
            if self.started:
                await self.emit("tts.completed")
        except VoiceError as exc:
            self.failed = True
            self.state = SpeechState.ERROR
            await self.emit("system.error", code=exc.code, message=exc.message, recoverable=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.failed = True
            self.state = SpeechState.ERROR
            await self.emit("system.error", code="TTS_PROCESS_FAILED", message="Speech playback failed; the text response remains available.", recoverable=True)
        finally:
            self._clear()

    def _clear(self) -> None:
        while not self.queue.empty():
            self.queue.get_nowait()

    async def finish(self) -> None:
        for sentence in self.sentences.feed(self.cleaner.flush()) + self.sentences.flush():
            await self.enqueue(sentence, from_stream=True)
        if not self.task.done():
            await self.queue.put(None)
        await self.task

    async def stop(self) -> None:
        self.state = SpeechState.STOPPED
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
        self._clear()
        if self.started:
            await self.emit("tts.cancelled")


class VoiceManager:
    """Share provider resource locks, never audio or conversation content."""

    def __init__(self, config: Settings, *, stt: SpeechToTextProvider | None = None, tts: TextToSpeechProvider | None = None) -> None:
        self.config = config
        self.stt_override = stt
        self.tts_override = tts
        self.stt_lock = asyncio.Lock()
        self.tts_lock = asyncio.Lock()
        self.voice_registry = VoiceRegistry()
        profile = next((name for name, model in STT_PROFILE_MODELS.items() if model == config.whisper_model), "BALANCED")
        self.settings = VoiceSettings(
            voice_enabled=config.voice_enabled, stt_enabled=config.stt_enabled,
            tts_enabled=config.tts_enabled, stt_profile=profile, whisper_model=config.whisper_model,
            tts_voice=config.tts_voice, tts_rate=config.tts_rate,
            tts_language_mode=config.tts_language_mode,
            tts_voice_english=config.tts_voice_english, tts_voice_hindi=config.tts_voice_hindi,
            tts_voice_fallback=config.tts_voice_fallback,
            language=config.whisper_language, shortcut=config.voice_shortcut,
            auto_stop=config.voice_auto_stop, silence_timeout_ms=config.voice_silence_timeout_ms,
            max_recording_seconds=config.voice_max_recording_seconds,
            wake_word_enabled=config.wake_word_enabled,followup_window_seconds=config.conversation_followup_window_seconds,
            microphone_id=config.microphone_device,auto_gain=config.auto_gain,pre_roll_ms=config.pre_roll_ms,post_roll_ms=config.post_roll_ms,
        )

    def stt(self, settings: VoiceSettings | None = None) -> SpeechToTextProvider:
        options = settings or self.settings
        if self.stt_override:
            return self.stt_override
        directory=Path(self.config.speech_models_directory) if self.config.speech_models_directory else self.config.project_root/"backend/data/models"
        local=self.config.project_root/"backend/data/models"/f"ggml-{options.whisper_model}.bin"
        path = Path(self.config.whisper_model_path).expanduser() if self.config.whisper_model_path else local if local.is_file() else directory / f"ggml-{options.whisper_model}.bin"
        if not path.is_absolute():
            path = self.config.project_root / path
        return LocalWhisperProvider(path, binary=self.config.whisper_binary, language=options.language, timeout=self.config.stt_timeout, lock=self.stt_lock,context=options.stt_context,preprocess=options.auto_gain)

    def tts(self, settings: VoiceSettings | None = None, *, read_urls: bool = False) -> TextToSpeechProvider:
        options = settings or self.settings
        return self.tts_override or MacOSTTSProvider(voice=options.tts_voice, rate=options.tts_rate, lock=self.tts_lock,
            language_mode=options.tts_language_mode, voice_english=options.tts_voice_english,
            voice_hindi=options.tts_voice_hindi, voice_fallback=options.tts_voice_fallback,
            registry=self.voice_registry, read_urls=read_urls)

    async def status(self) -> dict[str, Any]:
        stt = self.stt()
        detail = await stt.availability() if isinstance(stt, LocalWhisperProvider) else {"available": await stt.is_available()}
        tts = self.tts()
        tts_available = await tts.is_available()
        languages: dict[str, bool] = {}
        if tts_available and isinstance(tts, MacOSTTSProvider):
            try:
                await self.voice_registry.load()
                languages = {lang: self.voice_registry.is_language_supported(lang) for lang in ("en", "hi", "hinglish")}
            except VoiceError:
                tts_available = False
        return {
            "settings": self.settings.model_dump(),
            "stt": {**detail, "enabled": self.settings.stt_enabled, "provider": stt.provider_name},
            "tts": {"available": tts_available, "enabled": self.settings.tts_enabled, "provider": tts.provider_name,
                    "playback": "Mac speakers", "language_support": languages},
        }

    def models(self):
        directory=Path(self.config.speech_models_directory) if self.config.speech_models_directory else self.config.project_root/"backend/data/models"
        return {"profiles":{"FAST":"base","BALANCED":"small","ACCURATE":"medium"},"models":[{"name":name,"installed":(directory/f"ggml-{name}.bin").is_file() or (self.config.project_root/"backend/data/models"/f"ggml-{name}.bin").is_file()} for name in ("tiny","base","small","medium")],"automatic_downloads":False}
