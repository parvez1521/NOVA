"""Local wake-provider discovery. Passive transcripts never leave this boundary."""
import asyncio
import re
import time
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from pathlib import Path

from app.voice.base import VoiceError
from app.voice.stt.whisper import LocalWhisperProvider, validate_audio


def wake_phrase_matches(text: str, phrase: str = "Hey Nova") -> bool:
    if phrase == "Hey Nova":
        return _phrase_matches(text, "Hey Nova") or _phrase_matches(text, "Hello Nova")
    return _phrase_matches(text, phrase)


def _phrase_matches(text: str, phrase: str) -> bool:
    words=re.findall(r"[\w]+",text.casefold())
    expected=re.findall(r"[\w]+",phrase.casefold())
    return any(words[index:index+len(expected)]==expected for index in range(len(words)-len(expected)+1))


class WakeWordProvider(ABC):
    name: str
    def __init__(self):self.enabled=False;self.callback=None
    async def start(self):
        if not await self.is_available():raise VoiceError("WAKE_UNAVAILABLE","Install an eligible local wake engine/model first.")
        self.enabled=True
    async def stop(self):self.enabled=False
    def on_wake(self,callback: Callable[[],Awaitable[None]]):self.callback=callback
    @abstractmethod
    async def is_available(self) -> bool:...


class WhisperKeywordProvider(WakeWordProvider):
    name="whisper_vad"
    def __init__(self,path: Path,*,binary="whisper-cli",model="tiny",lock=None):
        super().__init__();self.model=model
        self.recognizer=LocalWhisperProvider(path,binary=binary,language="en",timeout=15,lock=lock)
        self.lock=asyncio.Lock();self.last_window=0.;self.last_wake=0.
        self.running=None
    async def is_available(self):return self.model in {"tiny","base"} and await self.recognizer.is_available()
    async def stop(self):
        self.enabled=False
        if self.running and self.running is not asyncio.current_task():
            self.running.cancel();await asyncio.gather(self.running,return_exceptions=True)
    async def inspect(self,audio: bytes):
        validate_audio(audio,maximum_seconds=4)
        if not self.enabled:raise VoiceError("WAKE_DISABLED","Enable wake listening before keyword recognition.")
        if self.lock.locked() or time.monotonic()-self.last_window<.5:return {"matched":False,"provider":self.name}
        async with self.lock:
            self.last_window=time.monotonic()
            self.running=asyncio.current_task()
            try:result=await self.recognizer.transcribe(audio)
            except VoiceError as error:
                if error.code=="EMPTY_TRANSCRIPT":return {"matched":False,"provider":self.name}
                raise
            finally:self.running=None
            if not self.enabled:return {"matched":False,"provider":self.name}
            matched=wake_phrase_matches(result.text) and time.monotonic()-self.last_wake>2
            if matched:
                self.last_wake=time.monotonic()
                if self.callback:await self.callback()
            # Never return, log, or persist the passive transcript.
            return {"matched":matched,"provider":self.name,"latency_ms":result.latency_ms}


class WakeProviderRegistry:
    def __init__(self):self.providers={}
    def register(self,provider: WakeWordProvider):self.providers[provider.name]=provider
    async def discover(self):return [{"name":name,"available":await provider.is_available(),"local":True,"vad_gated":True,"enabled":provider.enabled} for name,provider in self.providers.items()]
