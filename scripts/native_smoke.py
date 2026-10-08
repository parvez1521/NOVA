"""Safe packaged-runtime smoke: no real desktop tasks or external side effects."""
import asyncio
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def backend_executable(bundle: Path) -> Path:
    candidates=(bundle/"Contents/MacOS/nova-backend", bundle/"Contents/MacOS/nova-backend-aarch64-apple-darwin")
    for candidate in candidates:
        if candidate.is_file():return candidate
    raise AssertionError("Packaged backend executable is missing")


async def main():
    import httpx
    from websockets.asyncio.client import connect
    import json
    bundle=ROOT/"src-tauri/target/release/bundle/macos/NOVA.app"
    backend=backend_executable(bundle)
    with tempfile.TemporaryDirectory(prefix="nova-native-smoke-") as directory:
        data=Path(directory)
        environment={**os.environ,"NATIVE_APP_ENABLED":"true","NATIVE_SESSION_TOKEN":"isolated-native-test","NOVA_PORT":"18742","NATIVE_DATA_ROOT":str(data),"DATABASE_PATH":str(data/"nova.db"),
            "NOVA_NATIVE_HELPER":str(bundle/"Contents/Resources/runtime/NOVA Computer Access.app/Contents/MacOS/NovaNative"),
            "WHISPER_BINARY":str(bundle/"Contents/Resources/runtime/speech/whisper-cli"),"WHISPER_MODEL_PATH":str(bundle/"Contents/Resources/runtime/models/ggml-small.bin"),"SPEECH_MODELS_DIRECTORY":str(bundle/"Contents/Resources/runtime/models")}
        child=subprocess.Popen([str(backend)],env=environment,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
        async with httpx.AsyncClient(base_url="http://127.0.0.1:18742",headers={"x-nova-session":"isolated-native-test"},timeout=30) as client:
            try:
                for attempt in range(450):
                    try:
                        if (await client.get("/api/health/live")).status_code==200:break
                    except httpx.HTTPError:pass
                    await asyncio.sleep(.2)
                else:raise AssertionError("Packaged backend failed to start")
                assert (await client.get("/api/health/live",headers={"x-nova-session":"invalid"})).status_code==401
                health=(await client.get("/api/health")).json()
                assert health["capabilities"]["persistence"]["available"]
                assert health["capabilities"]["voice"]["stt"]["available"]
                connectors=(await client.get("/api/connectors")).json()
                assert connectors["enabled"] is False and len(connectors["connectors"]) >= 10
                assert (await client.get("/api/mcp")).json()["enabled"] is False
                settings=(await client.get("/api/computer")).json()
                await client.put("/api/computer",json={**settings,"enabled":True,"simulation":True})
                async with connect("ws://127.0.0.1:18742/ws?session=isolated-native-test") as ws:
                    ready=json.loads(await ws.recv());conversation=ready["conversation_id"]
                    await ws.send(json.dumps({"type":"agent.message","content":"Open Chrome","speak":False}))
                    async with asyncio.timeout(90):
                        while True:
                            event=json.loads(await ws.recv())
                            assert event["type"]!="system.error",event.get("code")
                            if event["type"]=="agent.completed":assert event["content"].startswith("[SIMULATION]");break
                assert (await client.get(f"/api/conversations/{conversation}/messages")).json()[-1]["model"]=="computer-task"
                assert (await client.get("/api/computer/tasks")).json()[0]["status"]=="COMPLETED"
                print("packaged_smoke: authenticated HTTP/WebSocket, local Ollama simulation, bundled STT discovery, persistence, and global-stop endpoint passed")
                assert (await client.post("/api/runtime/stop")).json()["stopped"]
            finally:
                import signal
                os.killpg(child.pid,signal.SIGTERM)
                try:child.wait(timeout=10)
                except subprocess.TimeoutExpired:os.killpg(child.pid,signal.SIGKILL);child.wait()


if __name__=="__main__":
    sys.path.insert(0,str(ROOT/"backend"));asyncio.run(main())
