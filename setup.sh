#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"

if ! command -v python3 >/dev/null 2>&1; then
  printf '%s\n' 'Python 3 is required. Install it with Homebrew, then rerun setup.' >&2
  exit 1
fi

if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
  printf '%s\n' 'Node.js and npm are required. Install them with Homebrew, then rerun setup.' >&2
  exit 1
fi

printf '%s\n' 'NOVA environment'
printf '%s\n' "✓ Python $(python3 --version 2>&1)"
printf '%s\n' "✓ Node $(node --version)"

mkdir -p "$BACKEND_DIR/data"

if [[ ! -d "$BACKEND_DIR/.venv" ]]; then
  printf '%s\n' 'Creating backend virtual environment...'
  python3 -m venv "$BACKEND_DIR/.venv"
fi

printf '%s\n' 'Installing backend dependencies...'
"$BACKEND_DIR/.venv/bin/python" -m pip install --upgrade pip
"$BACKEND_DIR/.venv/bin/pip" install -r "$BACKEND_DIR/requirements.txt"
printf '%s\n' '✓ Backend dependencies'

printf '%s\n' 'Installing frontend dependencies...'
npm install --prefix "$FRONTEND_DIR"
printf '%s\n' '✓ Frontend dependencies'

if [[ ! -f "$ROOT_DIR/.env" ]]; then
  cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
  printf '%s\n' 'Created .env from .env.example.'
else
  printf '%s\n' 'Keeping existing .env.'
fi

OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://127.0.0.1:11434}"
OLLAMA_MODEL="${OLLAMA_MODEL:-qwen3:4b-q4_K_M}"
if command -v ollama >/dev/null 2>&1; then
  printf '%s\n' "✓ Ollama installed ($(ollama --version 2>/dev/null || printf '%s' 'version unavailable'))"
  if curl -fsS --max-time 2 "$OLLAMA_BASE_URL/api/tags" >/dev/null 2>&1; then
    printf '%s\n' "✓ Ollama server reachable at $OLLAMA_BASE_URL"
    ollama_models="$(ollama list 2>/dev/null || true)"
    if [[ "$ollama_models" == *"$OLLAMA_MODEL"* ]]; then
      printf '%s\n' "✓ Model available: $OLLAMA_MODEL"
    else
      printf '%s\n' "⚠ Model not installed: $OLLAMA_MODEL"
    fi
  else
    printf '%s\n' '⚠ Ollama is installed but the server is not running; NOVA will still boot.'
  fi
else
  printf '%s\n' '⚠ Ollama not installed; NOVA will still boot.'
fi

PYTHONPATH="$BACKEND_DIR" "$BACKEND_DIR/.venv/bin/python" -m app.voice.diagnostics || printf '%s\n' '⚠ Voice diagnostics unavailable; text mode will still start.'

if [[ "${1:-}" == "--start" ]]; then
  backend_pid=''
  frontend_pid=''
  cleanup() {
    if [[ -n "$backend_pid" ]]; then
      kill "$backend_pid" 2>/dev/null || true
    fi
    if [[ -n "$frontend_pid" ]]; then
      kill "$frontend_pid" 2>/dev/null || true
    fi
  }
  trap cleanup EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  "$BACKEND_DIR/.venv/bin/uvicorn" app.main:app --app-dir "$BACKEND_DIR" --host 127.0.0.1 --port 8742 &
  backend_pid=$!
  "$FRONTEND_DIR/node_modules/.bin/vite" "$FRONTEND_DIR" --host 127.0.0.1 --port 5173 --strictPort &
  frontend_pid=$!
  wait "$frontend_pid"
else
  printf '%s\n' ''
  printf '%s\n' 'Setup complete. Start the services with:'
  printf '%s\n' "  $BACKEND_DIR/.venv/bin/uvicorn app.main:app --app-dir $BACKEND_DIR --reload --host 127.0.0.1 --port 8742"
  printf '%s\n' "  npm --prefix $FRONTEND_DIR run dev"
  printf '%s\n' 'Use ./setup.sh --start to launch both development servers.'
fi
