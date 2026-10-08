#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="$HOME/.cargo/bin:$PATH"
cd "$ROOT"
if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  printf '%s\n' 'NOVA desktop packaging currently targets Apple Silicon macOS.' >&2
  exit 1
fi
if [[ ! -x "$ROOT/backend/.venv/bin/python" ]]; then
  printf '%s\n' 'Backend environment missing. Run ./setup.sh first.' >&2
  exit 1
fi
if ! cargo --version >/dev/null 2>&1 || ! rustc --version >/dev/null 2>&1; then
  printf '%s\n' 'Rust and Cargo are required for desktop packaging. Install Rust with https://rustup.rs/.' >&2
  exit 1
fi
if [[ ! -x "$ROOT/frontend/node_modules/.bin/tauri" ]]; then
  printf '%s\n' 'Tauri CLI missing. Run ./setup.sh first.' >&2
  exit 1
fi
"$ROOT/backend/.venv/bin/python" "$ROOT/scripts/package_backend.py"
"$ROOT/frontend/node_modules/.bin/tauri" icon "$ROOT/scripts/icon.svg" --output "$ROOT/src-tauri/icons"
"$ROOT/frontend/node_modules/.bin/tauri" build --config "$ROOT/src-tauri/tauri.conf.json" --bundles app
"$ROOT/backend/.venv/bin/python" "$ROOT/scripts/sign_release.py"
"$ROOT/backend/.venv/bin/python" "$ROOT/scripts/create_dmg.py"
