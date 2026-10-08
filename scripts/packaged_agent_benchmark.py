"""Run a bounded packaged-agent benchmark without physical desktop side effects."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "src-tauri/target/release/bundle/macos/NOVA.app"
BACKEND = APP / "Contents/MacOS/nova-backend"
PORT = 18743
SESSION = "packaged-agent-benchmark"

PACKAGED_CASES = [
    ("simple-1", "Open Safari"),
    ("simple-2", "Open Google Chrome"),
    ("simple-3", "Open Finder"),
    ("simple-4", "Open System Settings"),
    ("multi-1", "Open Safari and search AI tools"),
    ("multi-2", "Open Chrome and search YouTube for AI news"),
    ("multi-3", "Open Finder and open Downloads"),
    ("multi-4", "Open Safari, search AI tools, and verify the results"),
]
CASE_FILTER = {item.strip() for item in os.environ.get("NOVA_BENCHMARK_CASES", "").split(",") if item.strip()}


def compact(event: dict) -> dict:
    trace = event.get("pipeline_trace") or {}
    return {
        "task_id": trace.get("task_id") or event.get("task_id"),
        "status": event.get("task_status"),
        "provider": trace.get("provider"),
        "model": trace.get("model"),
        "selected_tool": trace.get("selected_tool"),
        "action_attempted": trace.get("action_attempted"),
        "planner_result": trace.get("planner_result"),
        "current_step": trace.get("current_step"),
        "final_error_code": trace.get("final_error_code"),
        "summary": str(event.get("summary") or event.get("content") or "")[:240],
    }


async def run_case(websocket, case_id: str, goal: str) -> dict:
    await websocket.send(json.dumps({"type": "agent.message", "content": goal, "speak": False}))
    events = []
    async with asyncio.timeout(180):
        while True:
            event = json.loads(await websocket.recv())
            if event.get("type") in {"task.updated", "task.completed", "task.failed", "agent.completed", "system.error"}:
                events.append(event)
            if event.get("type") == "agent.completed":
                final = next((item for item in reversed(events) if item.get("task_status") in {"COMPLETED", "FAILED", "CANCELLED"}), event)
                row = compact(final)
                row.update({"case": case_id, "goal": goal, "status": "PASS" if row.get("status") == "COMPLETED" else "FAIL"})
                return row


async def benchmark() -> dict:
    import httpx
    from websockets.asyncio.client import connect

    with tempfile.TemporaryDirectory(prefix="nova-packaged-agent-") as directory:
        data = Path(directory)
        environment = {
            **os.environ,
            "NATIVE_APP_ENABLED": "true",
            "NATIVE_SESSION_TOKEN": SESSION,
            "NOVA_PORT": str(PORT),
            "NATIVE_DATA_ROOT": str(data),
            "DATABASE_PATH": str(data / "nova.db"),
            "NOVA_NATIVE_HELPER": str(APP / "Contents/Resources/runtime/NOVA Computer Access.app/Contents/MacOS/NovaNative"),
            "WHISPER_BINARY": str(APP / "Contents/Resources/runtime/speech/whisper-cli"),
            "WHISPER_MODEL_PATH": str(APP / "Contents/Resources/runtime/models/ggml-small.bin"),
            "SPEECH_MODELS_DIRECTORY": str(APP / "Contents/Resources/runtime/models"),
        }
        child = subprocess.Popen([str(BACKEND)], env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{PORT}", headers={"x-nova-session": SESSION}, timeout=30) as client:
                for _ in range(300):
                    try:
                        if (await client.get("/api/health/live")).status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    await asyncio.sleep(.2)
                else:
                    raise RuntimeError("Packaged benchmark backend did not start")
                settings = (await client.get("/api/computer")).json()
                await client.put("/api/computer", json={**settings, "enabled": True, "simulation": True, "max_task_steps": 24})
                async with connect(f"ws://127.0.0.1:{PORT}/ws?session={SESSION}", open_timeout=10, max_size=2_000_000) as websocket:
                    ready = json.loads(await websocket.recv())
                    if ready.get("type") != "system.ready":
                        raise RuntimeError("Packaged benchmark WebSocket was not ready")
                    results = []
                    for case_id, goal in PACKAGED_CASES:
                        if CASE_FILTER and case_id not in CASE_FILTER:
                            continue
                        started = time.perf_counter()
                        try:
                            row = await run_case(websocket, case_id, goal)
                        except Exception as error:
                            row = {"case": case_id, "goal": goal, "status": "FAIL", "error": type(error).__name__, "summary": str(error)[:240]}
                        row["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
                        results.append(row)
                return {
                    "suite": "packaged_agent_benchmark",
                    "status": "PASS" if all(row["status"] == "PASS" for row in results) else "FAIL",
                    "execution": "bundled_backend_simulation",
                    "physical_desktop_actions": False,
                    "cases_run": len(results),
                    "results": results,
                    "deferred": {
                        "physical_permission_cases": "BLOCKED_ACCESSIBILITY_OR_SCREEN_RECORDING",
                        "real_account_connector_cases": "NOT_RUN_NO_ACCOUNT_ACCEPTANCE",
                        "risk_confirmation_cases": "AUTOMATED_REGRESSION_ONLY",
                    },
                }
        finally:
            os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT / "backend"))
    print(json.dumps(asyncio.run(benchmark()), indent=2, sort_keys=True))
