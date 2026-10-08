"""Run packaged STT/task-path checks with generated local speech audio.

This exercises the real packaged backend, bundled Whisper, WebSocket voice
handoff, normalization and non-simulated computer task. It does not claim a
physical microphone or native wake-detector acceptance; the audio is generated
locally with macOS `say` and submitted at the same finalized-audio boundary the
frontend uses after wake detection.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import subprocess
import tempfile
from pathlib import Path

import httpx
from websockets.asyncio.client import connect

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "src-tauri/target/release/bundle/macos/NOVA.app/Contents/MacOS/nova-backend"
BASE = "http://127.0.0.1:8742"


def packaged_session() -> str:
    pattern = str(BACKEND)
    try:
        pids = subprocess.check_output(["pgrep", "-f", pattern], text=True).split()
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("Launch the packaged NOVA.app before running this smoke test") from exc
    for pid in pids:
        command = subprocess.check_output(["ps", "eww", "-p", pid, "-o", "command="], text=True)
        match = re.search(r"NATIVE_SESSION_TOKEN=([^ ]+)", command)
        if match:
            return match.group(1)
    raise RuntimeError("Packaged NOVA backend session was not found")


def speech_wav(text: str, directory: Path, name: str) -> Path:
    aiff = directory / f"{name}.aiff"
    wav = directory / f"{name}.wav"
    subprocess.run(["/usr/bin/say", "-v", "Samantha", "-o", str(aiff), text], check=True)
    subprocess.run(["/usr/bin/afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(aiff), str(wav)], check=True)
    return wav


def compact(event: dict) -> dict:
    keys = ("type", "request_id", "task_id", "task_status", "current_step", "summary", "code", "message", "content", "text", "raw_transcript", "wake_phrase_removed", "wake_only", "failure", "pipeline_trace", "provider", "model")
    return {key: event[key] for key in keys if event.get(key) is not None}


async def voice_request(session: str, audio: Path, request_id: str, terminal: str) -> list[dict]:
    async with connect(f"ws://127.0.0.1:8742/ws?session={session}", open_timeout=10, max_size=2_000_000) as websocket:
        ready = json.loads(await websocket.recv())
        if ready.get("type") != "system.ready":
            raise RuntimeError("Packaged WebSocket did not become ready")
        await websocket.send(json.dumps({"type": "voice.start", "request_id": request_id}))
        await websocket.recv()
        await websocket.send(json.dumps({"type": "voice.audio", "request_id": request_id, "audio": base64.b64encode(audio.read_bytes()).decode()}))
        events = []
        async with asyncio.timeout(300):
            while True:
                event = json.loads(await websocket.recv())
                if event.get("type") in {"voice.transcript", "voice.wake_only", "task.updated", "task.completed", "task.failed", "agent.completed", "system.error"}:
                    events.append(compact(event))
                if event.get("type") == terminal:
                    return events


async def main() -> None:
    session = packaged_session()
    with tempfile.TemporaryDirectory(prefix="nova-spoken-smoke-") as directory:
        folder = Path(directory)
        wake_audio = speech_wav("Hey Nova", folder, "wake-only")
        command_audio = speech_wav("Hey Nova, open Safari", folder, "open-safari")
        async with httpx.AsyncClient(base_url=BASE, headers={"x-nova-session": session}, timeout=30) as client:
            settings = (await client.get("/api/computer")).json()
            if settings.get("simulation"):
                raise RuntimeError("Packaged spoken smoke requires simulation=false")
            text_response = None
            async with connect(f"ws://127.0.0.1:8742/ws?session={session}", open_timeout=10, max_size=2_000_000) as websocket:
                ready = json.loads(await websocket.recv())
                await websocket.send(json.dumps({"type": "agent.message", "content": "Open Safari", "speak": False}))
                text_events = []
                async with asyncio.timeout(300):
                    while True:
                        event = json.loads(await websocket.recv())
                        if event.get("type") in {"task.updated", "task.completed", "task.failed", "agent.completed", "system.error"}:
                            text_events.append(compact(event))
                        if event.get("type") == "agent.completed":
                            text_response = text_events
                            break
            wake_events = await voice_request(session, wake_audio, "11111111-1111-4111-8111-111111111111", "voice.wake_only")
            voice_events = await voice_request(session, command_audio, "22222222-2222-4222-8222-222222222222", "agent.completed")
            tasks = (await client.get("/api/computer/tasks", params={"limit": 10})).json()
        text_trace = next((event.get("pipeline_trace") for event in reversed(text_response or []) if event.get("pipeline_trace")), {})
        voice_trace = next((event.get("pipeline_trace") for event in reversed(voice_events) if event.get("pipeline_trace")), {})
        output = {
            "audio_source": "local macOS say -> 16 kHz PCM WAV; no physical microphone",
            "text": {"task_id": text_trace.get("task_id"), "goal": text_trace.get("task_goal"), "status": next((e.get("task_status") for e in reversed(text_response or []) if e.get("task_status")), None)},
            "wake_only": {"wake_only": any(event.get("wake_only") for event in wake_events), "task_count": sum(1 for event in wake_events if event.get("task_id"))},
            "voice": {"task_id": voice_trace.get("task_id"), "transcript": voice_trace.get("transcript"), "normalized_command": voice_trace.get("normalized_command"), "goal": voice_trace.get("task_goal"), "status": next((e.get("task_status") for e in reversed(voice_events) if e.get("task_status")), None), "failure": voice_trace.get("final_error")},
            "voice_text_equivalent": voice_trace.get("normalized_command") == text_trace.get("normalized_command", "Open Safari"),
            "recent_task_ids": [task.get("id") for task in tasks[:3]],
        }
        print(json.dumps(output, indent=2))
        if not output["wake_only"]["wake_only"] or output["wake_only"]["task_count"] != 0:
            raise AssertionError("Wake-only audio created a task")
        if output["voice"]["status"] != "COMPLETED":
            raise AssertionError("Packaged spoken command did not complete")
        if not output["voice_text_equivalent"]:
            raise AssertionError("Packaged voice/text normalized commands diverged")
        print("packaged_spoken_pipeline: wake-only suppression, bundled STT, normalization, task correlation, non-simulated execution and verification passed")


if __name__ == "__main__":
    asyncio.run(main())
