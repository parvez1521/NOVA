"""Setup diagnostics. Enumerates devices without opening or recording a microphone."""

import asyncio
import json
import platform
import shutil

from app.core.config import get_settings
from app.voice.manager import VoiceManager
from app.voice.process import run_process


async def main() -> None:
    print(f"{'✓' if shutil.which('ffmpeg') else '⚠'} FFmpeg")
    manager = VoiceManager(get_settings())
    status = await manager.status()
    print(f"{'✓' if status['stt']['available'] else '⚠'} Local Whisper ({status['settings']['whisper_model']}): {status['stt'].get('code') or 'ready'}")
    print(f"{'✓' if status['tts']['available'] else '⚠'} macOS TTS")
    if platform.system() == "Darwin":
        try:
            output = await run_process("/usr/sbin/system_profiler", "SPAudioDataType", "-json", timeout=20)
            data = json.loads(output)
            def has_input(value):
                if isinstance(value, dict):
                    return any((key == "coreaudio_device_input" and isinstance(child, int) and child > 0) or has_input(child) for key, child in value.items())
                return isinstance(value, list) and any(has_input(child) for child in value)
            print(f"{'✓' if has_input(data) else '⚠'} Microphone device inventory; browser permission checked only when you hold to talk")
        except Exception:
            print("⚠ Microphone inventory unavailable; the browser checks it on push-to-talk")


if __name__ == "__main__":
    asyncio.run(main())
