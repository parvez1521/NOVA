"""Text-to-speech with cancellable playback, not a cloud synthesis API."""

from abc import ABC, abstractmethod


class TextToSpeechProvider(ABC):
    provider_name: str

    @abstractmethod
    async def is_available(self) -> bool: ...

    @abstractmethod
    async def speak(self, text: str) -> None:
        """Synthesize and play locally. Cancellation must stop playback."""

    async def speech_info(self, text: str) -> dict[str, object]:
        """Optional playback metadata; no raw audio or internal reasoning."""
        return {}
