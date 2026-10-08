import asyncio
import base64
import io
import json
import math
import struct
import sys
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.llm.router import ProviderRouter
from app.main import create_app
from app.voice.base import VoiceError, VoiceSettings
from app.voice.manager import SentenceBuffer, SpeechQueue, SpeechState, VoiceManager
from app.voice.process import run_process
from app.voice.stt.base import SpeechToTextProvider, TranscriptionResult
from app.voice.stt.whisper import LocalWhisperProvider, is_wake_only_transcript, normalize_spoken_command, normalize_transcript, validate_audio
from app.voice.tts.base import TextToSpeechProvider
from app.voice.tts.macos import MacOSTTSProvider
from app.voice.tts.registry import VoiceRegistry
from tests.test_websocket_streaming import WebSocketFakeProvider


def wav(silent: bool = False) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        stream.writeframes(b"".join(struct.pack("<h", 0 if silent else round(3000 * math.sin(i * 0.1))) for i in range(8000)))
    return output.getvalue()


class MockSTT(SpeechToTextProvider):
    provider_name = "local_whisper"

    async def is_available(self):
        return True

    async def transcribe(self, audio):
        return TranscriptionResult(text="Tell me a joke", raw_transcript=" Tell me a joke ", normalized_transcript="Tell me a joke", language="en", duration_ms=500, provider=self.provider_name, latency_ms=1)


class MockTTS(TextToSpeechProvider):
    provider_name = "macos"

    def __init__(self):
        self.spoken = []

    async def is_available(self):
        return True

    async def speak(self, text):
        self.spoken.append(text)
        await asyncio.sleep(0)


def test_sentence_buffer_handles_token_boundaries():
    buffer = SentenceBuffer()
    assert buffer.feed("Done bhai.") == []
    assert buffer.feed(" I opened ") == ["Done bhai."]
    assert buffer.feed("Chrome. Next sentence!") == ["I opened Chrome."]
    assert buffer.flush() == ["Next sentence!"]
    assert buffer.flush() == []


def test_audio_validation_and_normalization():
    assert validate_audio(wav()) == 500
    assert normalize_transcript("  Chrome\n kholo. ") == "Chrome kholo."
    with pytest.raises(VoiceError, match="No speech"):
        validate_audio(wav(True))
    with pytest.raises(VoiceError):
        validate_audio(b"not audio")


def test_spoken_command_normalization_removes_only_leading_wake_phrase():
    assert normalize_spoken_command("Hey Nova, open Safari") == "Open Safari"
    assert normalize_spoken_command("hello nova open Safari") == "Open Safari"
    assert normalize_spoken_command("Nova: open Safari") == "Open Safari"
    assert normalize_spoken_command("Hey Nova, open Safari!") == "Open Safari"
    assert normalize_spoken_command("open Safari") == "Open Safari"
    assert normalize_spoken_command("Hey Nova") == ""
    assert is_wake_only_transcript("Hey Nova")
    assert is_wake_only_transcript("hello nova!")
    assert not is_wake_only_transcript("Hey Nova, open Safari")


def test_spoken_command_normalization_does_not_strip_wake_words_inside_goal():
    assert normalize_spoken_command("Open Nova Safari") == "Open Nova Safari"
    assert normalize_spoken_command("please open Safari") == "Please open Safari"


@pytest.mark.asyncio
async def test_whisper_result_and_temporary_audio_cleanup(tmp_path, monkeypatch):
    model = tmp_path / "model.bin"
    model.touch()
    monkeypatch.setattr("app.voice.stt.whisper.shutil.which", lambda _: "whisper-cli")
    paths = []

    async def process(*args, **kwargs):
        source = Path(args[args.index("-f") + 1])
        destination = Path(args[args.index("-of") + 1]).with_suffix(".json")
        paths.append(source)
        assert source.read_bytes() == wav()
        assert "-tr" not in args  # preserve multilingual speech; never force translation
        destination.write_text(json.dumps({"transcription": [{"text": " Nova Chrome kholo. "}], "result": {"language": "hi"}}))
        return b""

    monkeypatch.setattr("app.voice.stt.whisper.run_process", process)
    result = await LocalWhisperProvider(model).transcribe(wav())
    assert result.text == "Nova Chrome kholo."
    assert result.raw_transcript == " Nova Chrome kholo. "
    assert result.language == "hi"
    assert result.confidence is None
    assert not paths[0].exists()


@pytest.mark.asyncio
async def test_whisper_missing_model(tmp_path, monkeypatch):
    monkeypatch.setattr("app.voice.stt.whisper.shutil.which", lambda _: "whisper-cli")
    provider = LocalWhisperProvider(tmp_path / "absent.bin")
    assert not await provider.is_available()
    with pytest.raises(VoiceError) as error:
        await provider.transcribe(wav())
    assert error.value.code == "WHISPER_MODEL_MISSING"


@pytest.mark.asyncio
async def test_macos_tts_sanitizes_and_uses_stdin(monkeypatch):
    monkeypatch.setattr(MacOSTTSProvider, "is_available", lambda _: asyncio.sleep(0, result=True))
    calls = []

    async def process(*args, **kwargs):
        calls.append((args, kwargs))
        return b""

    monkeypatch.setattr("app.voice.tts.macos.run_process", process)
    await MacOSTTSProvider(registry=VoiceRegistry("Samantha en_US # Hello")).speak("**Hello** [[volm 9]] bhai.")
    assert calls[0][1]["input_data"] == b"Hello bhai."
    assert "Hello" not in calls[0][0]


@pytest.mark.asyncio
async def test_speech_queue_sequential_and_cancellable():
    events = []

    async def emit(kind, **payload):
        events.append(kind)

    provider = MockTTS()
    queue = SpeechQueue(provider, emit)
    await queue.enqueue("First.")
    await queue.enqueue("Second.")
    await queue.finish()
    assert provider.spoken == ["First.", "Second."]
    assert events[0] == "tts.started"
    assert events[-1] == "tts.completed"

    stopped = asyncio.Event()
    playing = asyncio.Event()

    async def slow_speak(text):
        playing.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    provider.speak = slow_speak
    queue = SpeechQueue(provider, emit)
    await queue.enqueue("Slow.")
    await queue.enqueue("Never spoken.")
    await playing.wait()
    await queue.stop()
    assert stopped.is_set()
    assert queue.queue.empty()
    assert queue.state == SpeechState.STOPPED
    assert events[-1] == "tts.cancelled"


@pytest.mark.asyncio
async def test_local_subprocess_cancellation_reaps_child():
    task = asyncio.create_task(run_process(sys.executable, "-c", "import time; time.sleep(60)", timeout=120))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)


def voice_app(stt=None, tts=None):
    settings = Settings(_env_file=None, ollama_enabled=True, openrouter_enabled=False)
    provider = WebSocketFakeProvider()

    async def stream(messages, **kwargs):
        yield "First sentence. "
        await asyncio.sleep(0.05)
        yield "Second sentence."

    provider.generate_stream = stream
    router = ProviderRouter(settings, providers={"ollama": provider})
    manager = VoiceManager(settings, stt=stt or MockSTT(), tts=tts or MockTTS())
    return create_app(settings, router=router, voice=manager)


def test_voice_websocket_to_shared_agent_and_streaming_speech():
    request_id = "33333333-3333-4333-8333-333333333333"
    app = voice_app()
    with TestClient(app) as client, client.websocket_connect("/ws") as websocket:
        websocket.receive_json()
        websocket.send_json({"type": "voice.start", "request_id": request_id})
        assert websocket.receive_json()["type"] == "voice.listening"
        websocket.send_json({"type": "voice.recording", "request_id": request_id})
        assert websocket.receive_json()["type"] == "voice.recording"
        websocket.send_json({"type": "voice.audio", "request_id": request_id, "audio": base64.b64encode(wav()).decode()})
        events = []
        while not events or events[-1]["type"] != "agent.completed":
            events.append(websocket.receive_json())
        types = [event["type"] for event in events]
        assert types[:5] == ["voice.processing", "voice.transcript", "agent.started", "agent.thinking", "llm.started"]
        assert types.index("tts.started") < types.index("llm.completed")
        assert types.index("tts.completed") < types.index("agent.completed")
        assert all(event["request_id"] == request_id for event in events)
        assert app.state.voice_manager.tts_override.spoken == ["First sentence.", "Second sentence."]


def test_empty_voice_transcript_and_text_fallback():
    class EmptySTT(MockSTT):
        async def transcribe(self, audio):
            raise VoiceError("EMPTY_TRANSCRIPT", "No speech recognized.")

    with TestClient(voice_app(stt=EmptySTT())) as client, client.websocket_connect("/ws") as websocket:
        websocket.receive_json()
        request_id = "33333333-3333-4333-8333-333333333333"
        websocket.send_json({"type": "voice.start", "request_id": request_id})
        websocket.receive_json()
        websocket.send_json({"type": "voice.audio", "request_id": request_id, "audio": base64.b64encode(wav()).decode()})
        assert websocket.receive_json()["type"] == "voice.processing"
        assert websocket.receive_json()["code"] == "EMPTY_TRANSCRIPT"
        websocket.send_json({"type": "agent.message", "content": "Text still works"})
        while True:
            event = websocket.receive_json()
            if event["type"] == "agent.completed":
                assert event["content"]
                break


def test_voice_settings_disable_stt_preserve_text():
    with TestClient(voice_app()) as client:
        options = VoiceSettings(stt_enabled=False, tts_enabled=False).model_dump()
        assert client.put("/api/voice", json=options).status_code == 200
        with client.websocket_connect("/ws") as websocket:
            websocket.receive_json()
            websocket.send_json({"type": "voice.start"})
            assert websocket.receive_json()["code"] == "STT_DISABLED"
            websocket.send_json({"type": "agent.message", "content": "Hello", "speak": True})
            types = []
            while not types or types[-1] != "agent.completed":
                types.append(websocket.receive_json()["type"])
            assert "tts.started" not in types


def test_voice_recording_cancellation_and_invalid_settings():
    with TestClient(voice_app()) as client, client.websocket_connect("/ws") as websocket:
        websocket.receive_json()
        websocket.send_json({"type": "voice.start", "request_id": "33333333-3333-4333-8333-333333333333"})
        websocket.receive_json()
        websocket.send_json({"type": "agent.stop"})
        assert websocket.receive_json()["type"] == "agent.cancelled"
        assert client.put("/api/voice", json={"tts_rate": 9999}).status_code == 422


def test_tts_unavailable_does_not_break_response():
    class UnavailableTTS(MockTTS):
        async def is_available(self):
            return False

    with TestClient(voice_app(tts=UnavailableTTS())) as client, client.websocket_connect("/ws") as websocket:
        websocket.receive_json()
        websocket.send_json({"type": "agent.message", "content": "Hello", "speak": True})
        error = websocket.receive_json()
        assert error["code"] == "TTS_UNAVAILABLE" and error["recoverable"]
        while websocket.receive_json()["type"] != "agent.completed":
            pass


def test_new_voice_request_interrupts_speech_and_preserves_context():
    stopped = []

    class SlowTTS(MockTTS):
        async def speak(self, text):
            try:
                await asyncio.Event().wait()
            finally:
                stopped.append(True)

    app = voice_app(tts=SlowTTS())
    with TestClient(app) as client, client.websocket_connect("/ws") as websocket:
        websocket.receive_json()
        first = "33333333-3333-4333-8333-333333333333"
        second = "44444444-4444-4444-8444-444444444444"
        websocket.send_json({"type": "agent.message", "request_id": first, "content": "Tell me a joke", "speak": True})
        while websocket.receive_json()["type"] != "tts.sentence":
            pass
        websocket.send_json({"type": "voice.start", "request_id": second})
        events = []
        while not events or events[-1]["type"] != "voice.listening":
            events.append(websocket.receive_json())
        assert stopped
        assert "tts.cancelled" in [event["type"] for event in events]
        assert "agent.cancelled" in [event["type"] for event in events]
        assert events[-1]["request_id"] == second
        websocket.send_json({"type": "agent.stop", "request_id": second})
        assert websocket.receive_json()["type"] == "agent.cancelled"


@pytest.mark.asyncio
async def test_tts_failure_queue_degrades_to_text():
    class BrokenTTS(MockTTS):
        async def speak(self, text):
            raise VoiceError("TTS_PROCESS_FAILED", "Speech failed")

    errors = []

    async def emit(kind, **payload):
        if kind == "system.error":
            errors.append(payload)

    queue = SpeechQueue(BrokenTTS(), emit)
    await queue.enqueue("Hi.")
    await queue.finish()
    assert errors[0]["recoverable"]
    assert queue.failed


def test_stop_during_transcription_and_second_request():
    stopped = []

    class SlowSTT(MockSTT):
        async def transcribe(self, audio):
            try:
                await asyncio.Event().wait()
            finally:
                stopped.append(True)

    with TestClient(voice_app(stt=SlowSTT())) as client, client.websocket_connect("/ws") as websocket:
        websocket.receive_json()
        request_id = "33333333-3333-4333-8333-333333333333"
        websocket.send_json({"type": "voice.start", "request_id": request_id})
        websocket.receive_json()
        websocket.send_json({"type": "voice.audio", "request_id": request_id, "audio": base64.b64encode(wav()).decode()})
        assert websocket.receive_json()["type"] == "voice.processing"
        websocket.send_json({"type": "agent.stop", "request_id": request_id})
        assert websocket.receive_json()["type"] == "agent.cancelled"
        websocket.send_json({"type": "agent.message", "content": "Text after STOP"})
        while websocket.receive_json()["type"] != "agent.completed":
            pass


def test_voice_stop_command_is_not_sent_to_llm():
    class StopSTT(MockSTT):
        async def transcribe(self, audio):
            result = await super().transcribe(audio)
            result.text = "Nova stop."
            return result

    with TestClient(voice_app(stt=StopSTT())) as client, client.websocket_connect("/ws") as websocket:
        websocket.receive_json()
        request_id = "33333333-3333-4333-8333-333333333333"
        websocket.send_json({"type": "voice.start", "request_id": request_id})
        websocket.receive_json()
        websocket.send_json({"type": "voice.audio", "request_id": request_id, "audio": base64.b64encode(wav()).decode()})
        assert websocket.receive_json()["type"] == "voice.processing"
        assert websocket.receive_json()["type"] == "voice.transcript"
        assert websocket.receive_json()["type"] == "agent.cancelled"


def test_invalid_voice_message_keeps_websocket_usable():
    with TestClient(voice_app()) as client, client.websocket_connect("/ws") as websocket:
        websocket.receive_json()
        websocket.send_json({"type": []})
        assert websocket.receive_json()["code"] == "INVALID_MESSAGE"
        websocket.send_json({"type": "ping"})
        assert websocket.receive_json()["type"] == "system.pong"


def test_named_stt_profiles_select_local_models():
    with TestClient(voice_app()) as client:
        response = client.put("/api/voice", json=VoiceSettings(stt_profile="FAST").model_dump())
        assert response.status_code == 200
        assert response.json()["settings"]["stt_profile"] == "FAST"
        assert response.json()["settings"]["whisper_model"] == "base"

        response = client.put("/api/voice", json=VoiceSettings(stt_profile="ACCURATE").model_dump())
        assert response.status_code == 200
        assert response.json()["settings"]["whisper_model"] == "medium"
