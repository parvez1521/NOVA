"""Exercise real packaged Tauri subscriptions without using the microphone."""
import json
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "src-tauri/target/release/bundle/macos/NOVA.app"
RESULT = Path.home() / "Library/Application Support/local.nova.desktop/native-event-smoke.json"


def main():
    executable = APP / "Contents/MacOS/nova-desktop"
    # A running single instance would receive the open request instead of the flag.
    running = subprocess.run(["pgrep", "-f", f"^{executable}$"], capture_output=True)
    if running.returncode == 0:
        raise SystemExit("Quit the packaged NOVA app before running the native event smoke.")
    previous = RESULT.stat().st_mtime_ns if RESULT.exists() else 0
    child = subprocess.Popen([str(executable), "--event-smoke"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(300):
            if RESULT.exists() and RESULT.stat().st_mtime_ns != previous:
                result = json.loads(RESULT.read_text())
                child.wait(timeout=20)
                assert result["passed"], "Packaged native event subscription failed"
                print(json.dumps(result, indent=2))
                print("packaged_native_events: subscription, native voice-channel delivery, cleanup and resubscription passed")
                return
            if child.poll() is not None:
                raise RuntimeError("Packaged app exited without a native event smoke result")
            time.sleep(.2)
        raise RuntimeError("Packaged native event subscription timed out")
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=20)


if __name__ == "__main__":
    main()
