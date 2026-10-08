"""Answer-only language/voice selection and streamed speech regressions."""

import asyncio
import base64

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.voice.base import VoiceError
from app.voice.manager import SpeechQueue, VoiceManager
from app.voice.tts.cleaner import SpeechTextCleaner
from app.voice.tts.language import LanguageResolver
from app.voice.tts.macos import MacOSTTSProvider
from app.voice.tts.registry import VoiceRegistry
from tests.test_voice import MockSTT, MockTTS, voice_app, wav


INVENTORY = """Tingting zh_CN # 你好
Samantha en_US # Hello
Rishi en_IN # Hello
Lekha hi_IN # नमस्ते
Voice With Spaces (Enhanced) en_GB # Hello
"""

CASES = [
    ("Done bhai 😂🔥", "Done bhai"),
    ("Great job! 🎉", "Great job!"),
    ("😂🔥❤️", ""),
    ("**Done!** ✅", "Done!"),
    ("<think>internal reasoning</think>Done bhai.", "Done bhai."),
    ("<analysis>private\nreasoning.</analysis>Visible answer.", "Visible answer."),
    ("# Result\n**Done** [[volm 9]] bhai.", "Result Done bhai."),
    ("Done bhai 😂. I opened the page 🔥.", "Done bhai. I opened the page."),
    ("👩🏽‍💻 🇮🇳 👨‍👩‍👧‍👦 1️⃣", ""),
    ("Done &#x1F602; &amp; ready.", "Done & ready."),
    ("Use _natural_ punctuation: hello, bhai! Is 3 < 4?", "Use natural punctuation: hello, bhai! Is 3 < 4?"),
    ("Click [the page 😂](https://example.com/a).", "Click the page."),
    ("Visit https://example.com/a?x=1. Now continue.", "Visit the link. Now continue."),
    ("See www.example.com.", "See the link."),
    ('Here is code:\n```python\nprint("secret code")\n```\nDone.', "Here is code: I've displayed the code on screen. Done."),
    ('~~~json\n{"event":"tool.call","arguments":{"x":1}}\n~~~', "I've displayed the code on screen."),
    ('{"event":"tool.call","args":[1,{"value":"escaped \\\" }"}]} Done.', "Done."),
    ('[true,false,null] Done.', "Done."),
    ('[-1,2] Done.', "Done."),
    ("<tool_call>Chrome debug payload</tool_call><p>Done!</p>", "Done!"),
    ("<think/>Visible.", "Visible."),
    ("<think>unfinished reasoning", ""),
    ("request_id: abc-123\nDone.", "Done."),
    ("आप कैसे हैं? ❤️", "आप कैसे हैं?"),
]


@pytest.mark.parametrize("original,expected", CASES)
def test_speech_cleanup_and_every_stream_split(original, expected):
    assert SpeechTextCleaner.clean(original) == expected
    # Ollama may split syntax, Unicode sequences, or closing tags at any point.
    for split in range(len(original) + 1):
        parser = SpeechTextCleaner()
        streamed = parser.feed(original[:split]) + parser.feed(original[split:]) + parser.flush()
        assert SpeechTextCleaner.clean(streamed) == expected, (original, split, streamed)
    parser = SpeechTextCleaner()
    streamed = "".join(parser.feed(character) for character in original) + parser.flush()
    assert SpeechTextCleaner.clean(streamed) == expected


def test_raw_urls_only_when_explicitly_requested():
    assert SpeechTextCleaner.clean("Read https://example.com/a?x=1.", read_urls=True) == "Read https://example.com/a?x=1."
    assert SpeechTextCleaner.clean("[page](https://example.com)", read_urls=True) == "page https://example.com"


def test_long_private_and_code_blocks_do_not_accumulate_or_escape():
    parser = SpeechTextCleaner()
    assert "displayed the code" in parser.feed("```python\n")
    for _ in range(100):
        assert parser.feed("never speak this secret line.\n" * 100) == ""
        assert len(parser.pending) <= 2
    assert parser.feed("```\nDone.") + parser.flush() == "\nDone."
    parser = SpeechTextCleaner()
    assert parser.feed("<think>") == ""
    for _ in range(100):
        assert parser.feed("private reasoning. " * 100) == ""
        assert len(parser.pending) < len("</think>")
    assert parser.feed("</think>Done.") + parser.flush() == "Done."


@pytest.mark.parametrize("answer,language", [
    ("आप कैसे हैं?", "hi"), ("Chrome खोलो।", "hi"),
    ("Main bilkul theek hoon.", "hinglish"), ("Chrome kholo bhai.", "hinglish"),
    ("How are you today?", "en"), ("The main thing to do is open Chrome.", "en"),
    ("Aap kaise hain?", "hinglish"),
])
def test_language_is_resolved_from_answer(answer, language):
    assert LanguageResolver().resolve(answer).language == language


def test_overrides_preserve_detected_language_and_uncertainty():
    resolver = LanguageResolver()
    decision = resolver.resolve("आप कैसे हैं?", "english")
    assert decision.detected_language == "hi" and decision.language == "en"
    assert resolver.resolve("Chrome kholo bhai.").uncertain
    assert resolver.resolve("Bonjour!").uncertain
    assert resolver.resolve("Main bilkul theek hoon.", "hindi").language == "hi"
    assert resolver.resolve("Hello.", "hinglish").language == "hinglish"


@pytest.mark.asyncio
async def test_voice_inventory_cached_even_for_concurrent_requests(monkeypatch):
    calls = []

    async def process(*args, **kwargs):
        calls.append(args)
        await asyncio.sleep(0)
        return INVENTORY.encode()

    monkeypatch.setattr("app.voice.tts.registry.run_process", process)
    registry = VoiceRegistry()
    lists = await asyncio.gather(*(registry.list_voices() for _ in range(5)))
    assert len(calls) == 1 and calls[0] == ("/usr/bin/say", "-v", "?")
    assert lists[0][-1] == {"name": "Voice With Spaces (Enhanced)", "language": "en_GB"}
    assert registry.get_default_voice().locale == "en_US"
    assert registry.is_language_supported("hi")
    assert not registry.is_language_supported("fr")


@pytest.mark.parametrize("language,options,voice,fallback", [
    ("hi", {}, "Lekha", False),
    ("en", {}, "Samantha", False),
    ("hinglish", {}, "Rishi", False),
    ("hinglish", {"english": "Samantha"}, "Samantha", False),
    ("hi", {"hindi": "Not Installed"}, "Lekha", True),
    ("en", {"english": "Not Installed"}, "Samantha", True),
    ("hi", {"legacy": "Tingting"}, "Lekha", True),
    ("en", {"english": "Tingting", "fallback": "Tingting"}, "Samantha", True),
])
def test_language_specific_selection_never_uses_chinese(language, options, voice, fallback):
    selection = VoiceRegistry(INVENTORY).select(language, **options)
    assert selection.voice.name == voice
    assert selection.fallback_used == fallback


def test_missing_hindi_and_missing_safe_voices_fall_back_gracefully():
    registry = VoiceRegistry(INVENTORY.replace("Lekha hi_IN # नमस्ते\n", ""))
    selection = registry.select("hi", fallback="Rishi")
    assert selection.voice.name == "Rishi" and selection.fallback_used
    assert registry.select("hi", fallback="Tingting").voice.language == "en"
    with pytest.raises(VoiceError) as error:
        VoiceRegistry("Tingting zh_CN # 你好").select("hi", fallback="Tingting")
    assert error.value.code == "TTS_LANGUAGE_UNAVAILABLE"


@pytest.mark.asyncio
async def test_native_speech_uses_explicit_voice_clean_stdin_and_skips_emoji_only(monkeypatch):
    calls = []

    async def process(*args, **kwargs):
        calls.append((args, kwargs))
        return b""

    monkeypatch.setattr("app.voice.tts.macos.run_process", process)
    monkeypatch.setattr(MacOSTTSProvider, "is_available", lambda _: asyncio.sleep(0, result=True))
    provider = MacOSTTSProvider(voice="Tingting", registry=VoiceRegistry(INVENTORY))
    await provider.speak("😂🔥❤️")
    assert calls == []
    await provider.speak("आप कैसे हैं? ❤️")
    await provider.speak("Main bilkul theek hoon. 😂")
    assert calls[0][0][-2:] == ("-v", "Lekha")
    assert calls[0][1]["input_data"].decode() == "आप कैसे हैं?"
    assert calls[1][0][-2:] == ("-v", "Rishi")
    assert calls[1][1]["input_data"] == b"Main bilkul theek hoon."


@pytest.mark.asyncio
async def test_streaming_speaks_before_generation_finishes_without_code_or_metadata():
    class RecordingTTS(MockTTS):
        async def speak(self, text):
            await super().speak(text)
            first_spoken.set()

    first_spoken = asyncio.Event()
    events = []

    async def emit(kind, **payload):
        events.append((kind, payload))

    provider = RecordingTTS()
    queue = SpeechQueue(provider, emit)
    chunks = ["Done bhai 😂", ". I opened the page 🔥.", "\n```py", "thon\nsecret()\n"]
    for chunk in chunks:
        await queue.feed(chunk)
    await asyncio.wait_for(first_spoken.wait(), 1)
    assert provider.spoken[0] == "Done bhai."
    for chunk in ["more_secret()\n``", "`\n", '{"event":"tool.call"}', " All done."]:
        await queue.feed(chunk)
    await queue.finish()
    assert provider.spoken == ["Done bhai.", "I opened the page.", "I've displayed the code on screen.", "All done."]
    cleanup = next(payload for kind, payload in events if kind == "tts.cleanup")
    assert cleanup["original_answer_length"] > cleanup["cleaned_speech_length"]
    assert cleanup["speech_chars_removed"] > 0


def test_websocket_keeps_visible_answer_and_stt_language_separate(monkeypatch):
    calls = []

    async def process(*args, **kwargs):
        calls.append((args, kwargs))
        return b""

    monkeypatch.setattr("app.voice.tts.macos.run_process", process)
    monkeypatch.setattr(MacOSTTSProvider, "is_available", lambda _: asyncio.sleep(0, result=True))
    tts = MacOSTTSProvider(registry=VoiceRegistry(INVENTORY))
    app = voice_app(stt=MockSTT(), tts=tts)
    answer = "आप कैसे हैं? 😂🔥"

    async def stream(*args, **kwargs):
        for chunk in ["आप ", "कैसे हैं? ", "😂🔥"]:
            yield chunk
            await asyncio.sleep(0.01)

    app.state.provider_router.providers["ollama"].generate_stream = stream
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        ws.receive_json()
        request_id = "33333333-3333-4333-8333-333333333333"
        ws.send_json({"type": "voice.start", "request_id": request_id})
        ws.receive_json()
        ws.send_json({"type": "voice.audio", "request_id": request_id, "audio": base64.b64encode(wav()).decode()})
        events = []
        while not events or events[-1]["type"] != "agent.completed":
            events.append(ws.receive_json())
    transcript = next(event for event in events if event["type"] == "voice.transcript")
    speech = next(event for event in events if event["type"] == "tts.sentence")
    assert transcript["language"] == "en"
    assert speech["detected_answer_language"] == "hi"
    assert speech["selected_tts_voice"] == "Lekha" and speech["tts_voice_locale"] == "hi_IN"
    assert events[-1]["content"] == answer
    assert "".join(event.get("content", "") for event in events if event["type"] == "llm.token") == answer
    assert calls[0][1]["input_data"].decode() == "आप कैसे हैं?"
    cleanup = next(event for event in events if event["type"] == "tts.cleanup")
    assert cleanup["original_answer_length"] == len(answer)
    assert all(event["request_id"] == request_id for event in events)


def test_language_settings_round_trip_and_validation():
    with TestClient(voice_app()) as client:
        options = client.get("/api/voice").json()["settings"]
        options.update(tts_language_mode="hindi", tts_voice_hindi="Lekha", tts_voice_english="Rishi", tts_voice_fallback="Samantha")
        result = client.put("/api/voice", json=options)
        assert result.status_code == 200 and result.json()["settings"] == options
        assert client.put("/api/voice", json={"tts_language_mode": "chinese"}).status_code == 422
        assert client.put("/api/voice", json={"tts_voice_hindi": "Lekha\n-v Tingting"}).status_code == 422
    config = Settings(_env_file=None, tts_language_mode="hinglish", tts_voice_english="Rishi")
    assert VoiceManager(config).settings.tts_language_mode == "hinglish"
