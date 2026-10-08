#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="$ROOT/src-tauri/target/release/bundle/macos/NOVA.app"
if ! /usr/bin/env python3 "$ROOT/scripts/production_diagnostic.py" --require-canonical; then
  printf '%s\n' 'A different NOVA.app instance is active. Quit it before launching the project artifact.' >&2
  exit 2
fi
STATUS_PATH="$HOME/Library/Application Support/local.nova.desktop/native-status.json"
/bin/rm -f "$STATUS_PATH"
/usr/bin/open "$APP"
ROOT="$ROOT" /usr/bin/python3 - <<'PY'
import json
import os
import sys
import time
from pathlib import Path

root = Path(os.environ["ROOT"])
sys.path.insert(0, str(root / "scripts"))
from production_diagnostic import active_apps

status_path = Path.home() / "Library/Application Support/local.nova.desktop/native-status.json"
deadline = time.monotonic() + 60
while time.monotonic() < deadline:
    if active_apps():
        try:
            status = json.loads(status_path.read_text())
        except (OSError, ValueError):
            status = {}
        if status.get("status") == "ready" and status.get("base_url") == "http://127.0.0.1:8742":
            print(json.dumps({"status": "ready", "base_url": status["base_url"], "restarts": status.get("restarts", 0)}))
            raise SystemExit(0)
    time.sleep(0.2)
raise SystemExit("Canonical NOVA.app did not reach ready on 127.0.0.1:8742 within 60 seconds.")
PY
