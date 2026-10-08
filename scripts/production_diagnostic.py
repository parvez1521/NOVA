"""Print sanitized NOVA production identity and permission-owner diagnostics."""

from __future__ import annotations

import argparse
import json
import plistlib
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "src-tauri/target/release/bundle/macos/NOVA.app"
HELPER = APP / "Contents/Resources/runtime/NOVA Computer Access.app"
VOICE = APP / "Contents/Resources/runtime/NovaVoice"


def plist_value(path: Path, key: str) -> str:
    try:
        return str(plistlib.loads((path / "Contents/Info.plist").read_bytes()).get(key, ""))
    except (OSError, ValueError, plistlib.InvalidFileException):
        return ""


def signing_identity(path: Path) -> str:
    result = subprocess.run(["/usr/bin/codesign", "-dvvv", str(path)], capture_output=True, text=True, check=False)
    for line in (result.stdout + result.stderr).splitlines():
        if line.startswith("Signature="):
            return line.partition("=")[2]
        if line.startswith("Authority="):
            return line.partition("=")[2]
    return "unknown"


def active_apps() -> list[dict[str, object]]:
    result = subprocess.run(["/bin/ps", "-axo", "pid=,command="], capture_output=True, text=True, check=False)
    executable = str(APP / "Contents/MacOS/nova-desktop")
    rows = []
    for line in result.stdout.splitlines():
        value = line.strip()
        pid, _, command = value.partition(" ")
        path = command.split(" ", 1)[0]
        if path != executable:
            continue
        rows.append({"pid": int(pid) if pid.isdigit() else None, "path": path})
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-canonical", action="store_true")
    options = parser.parse_args()
    rows = active_apps()
    payload = {
        "active_app_path": rows[0]["path"] if rows else None,
        "active_processes": rows,
        "canonical_app_path": str(APP),
        "bundle_id": plist_value(APP, "CFBundleIdentifier"),
        "signing_identity": signing_identity(APP),
        "designated_requirement": 'identifier "local.nova.desktop"',
        "helper_path": str(HELPER),
        "helper_bundle_id": plist_value(HELPER, "CFBundleIdentifier"),
        "helper_signing_identity": signing_identity(HELPER),
        "voice_path": str(VOICE),
        "voice_identity": "local.nova.voice",
        "runtime_identity": "PACKAGED" if str(APP / "Contents/MacOS/nova-desktop") in {str(row["path"]) for row in rows} else "PROJECT_ARTIFACT_NOT_ACTIVE",
        "permission_owners": {
            "microphone": "local.nova.voice",
            "speech_recognition": "local.nova.voice",
            "accessibility": "local.nova.computer-access",
            "screen_recording": "local.nova.computer-access",
        },
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    if options.require_canonical and rows and any(row["path"] != str(APP / "Contents/MacOS/nova-desktop") for row in rows):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
