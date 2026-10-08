import io
import math
import struct
import wave
from app.voice.stt.audio import condition_audio
from app.voice.stt.whisper import validate_audio
from tests.test_voice import wav


def test_conditioning_preserves_bounded_pcm_duration_and_reports_clipping():
    result,quality=condition_audio(wav())
    assert validate_audio(result)==500 and 1<quality["gain"]<=4
    output=io.BytesIO()
    with wave.open(output,"wb") as target:
        target.setparams((1,2,16000,0,"NONE","not compressed"));target.writeframes(struct.pack("<h",32767)*8000)
    result,quality=condition_audio(output.getvalue())
    assert quality["clipping_fraction"]==1.0 and len(result)==16044


def test_silence_is_not_amplified_into_speech():
    result,quality=condition_audio(wav(True))
    assert quality["gain"]==1 and quality["rms"]==0
