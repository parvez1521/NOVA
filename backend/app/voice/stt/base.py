"""Local speech-to-text interface."""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from pydantic import BaseModel


class TranscriptionResult(BaseModel):
    text: str
    raw_transcript: str
    normalized_transcript: str
    language: str | None = None
    duration_ms: int
    provider: str
    latency_ms: float
    confidence: float | None = None


class SpeechToTextProvider(ABC):
    provider_name: str

    @abstractmethod
    async def is_available(self) -> bool: ...

    @abstractmethod
    async def transcribe(self, audio: bytes) -> TranscriptionResult: ...

    async def transcribe_stream(self, audio_stream: AsyncIterator[bytes]) -> AsyncIterator[TranscriptionResult]:
        """Buffered utterance adapter; Whisper does not claim token-level live STT."""
        audio = bytearray()
        async for chunk in audio_stream:
            audio.extend(chunk)
            if len(audio) > 2_000_000:
                from app.voice.base import VoiceError
                raise VoiceError("AUDIO_TOO_LARGE", "Voice recordings are limited to 60 seconds.")
        yield await self.transcribe(bytes(audio))
