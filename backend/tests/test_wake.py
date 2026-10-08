from unittest.mock import AsyncMock
import pytest
from app.voice.wake import WakeProviderRegistry,WhisperKeywordProvider,wake_phrase_matches
from app.voice.base import VoiceError
from app.voice.stt.base import TranscriptionResult
from tests.test_voice import wav


@pytest.mark.parametrize("text,expected",[("Nova",False),("supernova",False),("hey Novation",False),("Hey, Nova!",True),("Hello Nova",True),("hello nova open Chrome",True),("hey nova open Chrome",True),("I know Nova",False)])
def test_wake_requires_complete_phrase(text,expected):assert wake_phrase_matches(text)==expected


@pytest.mark.asyncio
async def test_large_whisper_models_are_never_wake_providers(tmp_path):
    provider=WhisperKeywordProvider(tmp_path/"model",model="small");provider.recognizer.is_available=AsyncMock(return_value=True)
    assert not await provider.is_available()
    with pytest.raises(VoiceError):await provider.start()


@pytest.mark.asyncio
async def test_passive_transcript_is_not_returned_and_disabled_listener_does_not_transcribe(tmp_path):
    provider=WhisperKeywordProvider(tmp_path/"model")
    provider.recognizer.is_available=AsyncMock(return_value=True)
    provider.recognizer.transcribe=AsyncMock(return_value=TranscriptionResult(text="Hey Nova",raw_transcript="Hey Nova",normalized_transcript="Hey Nova",duration_ms=500,provider="local_whisper",latency_ms=10))
    with pytest.raises(VoiceError):await provider.inspect(wav())
    provider.recognizer.transcribe.assert_not_awaited()
    await provider.start();callback=AsyncMock();provider.on_wake(callback)
    result=await provider.inspect(wav())
    assert result["matched"] and "text" not in result and "transcript" not in str(result)
    callback.assert_awaited_once()
    await provider.stop();assert not provider.enabled


@pytest.mark.asyncio
async def test_stop_cancels_pending_keyword_stt(tmp_path):
    import asyncio
    provider=WhisperKeywordProvider(tmp_path/"model");provider.recognizer.is_available=AsyncMock(return_value=True)
    entered=asyncio.Event()
    async def transcribe(audio):entered.set();await asyncio.Event().wait()
    provider.recognizer.transcribe=transcribe;await provider.start()
    task=asyncio.create_task(provider.inspect(wav()));await entered.wait();await provider.stop()
    with pytest.raises(asyncio.CancelledError):await task
    assert provider.running is None and not provider.enabled
