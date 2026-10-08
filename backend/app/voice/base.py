"""Voice contracts; errors never include raw audio or subprocess output."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


STT_PROFILE_MODELS = {"FAST": "base", "BALANCED": "small", "ACCURATE": "medium"}


class VoiceError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class VoiceState(StrEnum):
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING_AUDIO = "processing_audio"
    THINKING = "thinking"
    SPEAKING = "speaking"
    ERROR = "error"


class VoiceSettings(BaseModel):
    """Safe voice settings, independent of backend paths and credentials."""

    voice_enabled: bool = True
    stt_enabled: bool = True
    tts_enabled: bool = True
    microphone_id: str = Field(default="", max_length=256)
    stt_provider: Literal["local_whisper"] = "local_whisper"
    stt_profile: Literal["FAST", "BALANCED", "ACCURATE"] = "BALANCED"
    whisper_model: Literal["tiny", "base", "small", "medium"] = "small"
    language: str = Field(default="auto", pattern=r"^(auto|[a-z]{2,3})$")
    tts_provider: Literal["macos"] = "macos"
    tts_voice: str = Field(default="", max_length=100, pattern=r"^[^\x00-\x1f]*$")
    tts_language_mode: Literal["auto", "english", "hindi", "hinglish"] = "auto"
    tts_voice_english: str = Field(default="", max_length=100, pattern=r"^[^\x00-\x1f]*$")
    tts_voice_hindi: str = Field(default="", max_length=100, pattern=r"^[^\x00-\x1f]*$")
    tts_voice_fallback: str = Field(default="", max_length=100, pattern=r"^[^\x00-\x1f]*$")
    tts_rate: int = Field(default=180, ge=100, le=350)
    shortcut: Literal["Alt+Space", "Alt+V"] = "Alt+Space"
    auto_stop: bool = False
    silence_timeout_ms: int = Field(default=1200, ge=500, le=5000)
    max_recording_seconds: int = Field(default=60, ge=1, le=60)
    wake_word_enabled: bool = False
    wake_provider: Literal["auto","apple_speech","whisper_vad"] = "auto"
    wake_sensitivity: Literal["LOW","MEDIUM","HIGH"] = "MEDIUM"
    followup_window_seconds: int = Field(10,ge=0,le=60)
    auto_gain: bool = True
    pre_roll_ms: int = Field(350,ge=0,le=1500)
    post_roll_ms: int = Field(250,ge=0,le=1000)
    stt_context: str = Field("NOVA, Chrome, YouTube, Premiere Pro, After Effects, Telegram, Instagram, LinkedIn, GitHub, Notion.",max_length=500)
