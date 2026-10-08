"""On-demand whisper.cpp recognition using its Apple Silicon Metal backend."""

import asyncio
import io
import json
import math
import re
import shutil
import struct
import time
import wave
from pathlib import Path
from tempfile import TemporaryDirectory

from app.voice.base import VoiceError
from app.voice.process import run_process
from app.voice.stt.base import SpeechToTextProvider, TranscriptionResult


def validate_audio(audio: bytes, maximum_seconds: int = 60) -> int:
    """Only accept bounded mono 16 kHz PCM16 WAV; reject silence before Whisper."""
    if len(audio) > maximum_seconds * 32000 + 4096:
        raise VoiceError("AUDIO_TOO_LARGE", "Voice recordings are limited to 60 seconds.")
    try:
        with wave.open(io.BytesIO(audio), "rb") as source:
            if (source.getnchannels(), source.getframerate(), source.getsampwidth(), source.getcomptype()) != (1, 16000, 2, "NONE"):
                raise VoiceError("INVALID_AUDIO", "Expected mono 16 kHz PCM16 WAV audio.")
            frames = source.getnframes()
            pcm = source.readframes(frames)
            if not 1600 <= frames <= maximum_seconds * 16000 or len(pcm) != frames * 2:
                raise VoiceError("INVALID_AUDIO", "The recording is too short, truncated, or too long.")
    except (wave.Error, EOFError) as exc:
        raise VoiceError("INVALID_AUDIO", "The recording is not a valid PCM WAV file.") from exc
    rms = math.sqrt(sum(sample[0] ** 2 for sample in struct.iter_unpack("<h", pcm)) / frames)
    if rms < 30:
        raise VoiceError("EMPTY_TRANSCRIPT", "No speech detected. Hold the microphone button and try again.")
    return round(frames / 16)


def normalize_transcript(raw: str) -> str:
    """Whitespace only: no unreliable word substitution or forced translation."""
    return re.sub(r"\s+", " ", raw).strip()


_WAKE_PREFIX = re.compile(r"^\s*(?:(?:hey|hello)\s+)?nova\b[\s,:.!?\-]*", re.I)


def normalize_spoken_command(raw: str) -> str:
    """Remove only a leading wake phrase from a finalized transcript.

    Partial transcripts never enter this function: the WebSocket bridge calls it
    only after the audio recording has been finalized by STT. The original
    transcript remains available in the sanitized trace for diagnostics.
    """
    normalized = normalize_transcript(raw)
    command = normalize_transcript(_WAKE_PREFIX.sub("", normalized, count=1)).rstrip(" .!?।")
    # Match the text-command boundary's readable sentence form without
    # title-casing the user's words or changing multilingual content.
    return command[:1].upper() + command[1:] if command else ""


def is_wake_only_transcript(raw: str) -> bool:
    normalized = normalize_transcript(raw)
    return bool(normalized and _WAKE_PREFIX.fullmatch(normalized))


class LocalWhisperProvider(SpeechToTextProvider):
    provider_name = "local_whisper"

    def __init__(self, model_path: Path, *, binary: str = "whisper-cli", language: str = "auto", timeout: float = 120, lock: asyncio.Lock | None = None, context: str = "", preprocess: bool = False) -> None:
        self.model_path = model_path
        self.binary = binary
        self.language = language
        self.timeout = timeout
        self.lock = lock or asyncio.Lock()
        self.context=context
        self.preprocess=preprocess

    async def availability(self) -> dict[str, object]:
        binary = shutil.which(self.binary)
        exists = self.model_path.is_file()
        code = None if binary and exists else "WHISPER_UNAVAILABLE" if not binary else "WHISPER_MODEL_MISSING"
        return {"available": bool(binary and exists), "provider": self.provider_name, "engine": "whisper.cpp", "model_available": exists, "code": code}

    async def is_available(self) -> bool:
        return bool((await self.availability())["available"])

    async def transcribe(self, audio: bytes) -> TranscriptionResult:
        duration_ms = await asyncio.to_thread(validate_audio, audio)
        if self.preprocess:
            from app.voice.stt.audio import condition_audio
            audio,_=await asyncio.to_thread(condition_audio,audio)
        status = await self.availability()
        if not status["available"]:
            raise VoiceError(str(status["code"]), "Local Whisper is unavailable. Install whisper.cpp and configure a local GGML model.")
        start = time.perf_counter()
        async with self.lock:
            with TemporaryDirectory(prefix="nova-stt-") as directory:
                source = Path(directory) / "input.wav"
                destination = Path(directory) / "transcript"
                await asyncio.to_thread(source.write_bytes, audio)
                try:
                    await run_process(
                        self.binary, "-m", str(self.model_path), "-f", str(source),
                        "-l", self.language, "-oj", "-of", str(destination), "-np", "-nt",
                        "-t", "4", "-bs", "5", "-bo", "5", "-sns",
                        *(["--prompt",self.context] if self.context else []),
                        timeout=self.timeout,
                    )
                    data = json.loads(await asyncio.to_thread(destination.with_suffix(".json").read_text))
                    raw = "".join(segment["text"] for segment in data["transcription"])
                    language = data.get("result", {}).get("language")
                except asyncio.TimeoutError as exc:
                    raise VoiceError("STT_TIMEOUT", "Local transcription timed out. Try a shorter recording or smaller model.") from exc
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    raise VoiceError("STT_RESPONSE_ERROR", "Whisper returned an invalid transcription result.") from exc
        normalized = normalize_transcript(raw)
        if not normalized or re.fullmatch(r"\[[^]]*\]", normalized):
            raise VoiceError("EMPTY_TRANSCRIPT", "No speech was recognized. Try again or use text input.")
        return TranscriptionResult(
            text=normalized, raw_transcript=raw, normalized_transcript=normalized,
            language=language, duration_ms=duration_ms, provider=self.provider_name,
            latency_ms=round((time.perf_counter() - start) * 1000, 2),
        )
