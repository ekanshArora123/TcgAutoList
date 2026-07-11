#!/usr/bin/env bash
# ============================================================
#  TcgAutoList - run the dashboard (Flask backend + React UI)
#  First run sets up the venv, installs deps, and npm-installs
#  the frontend. Ctrl-C stops both.
#
#  Options:
#    --reset   Delete all caches/temp state first (every __pycache__,
#              *.pyc, *.egg-info, .pytest_cache, the frontend node_modules
#              + package-lock.json, and the .venv), then rebuild everything.
#
#  Full agent (Telegram) instead of the dashboard (run from repo root):
#    "$PY" -m dashboard.backend.orchestrator.main      (needs .env tokens)
# ============================================================
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

RESET=0
for arg in "$@"; do
    case "$arg" in
        --reset) RESET=1 ;;
        -h|--help) echo "Usage: ./run.sh [--reset]"; exit 0 ;;
        *) echo "Unknown option: $arg"; echo "Usage: ./run.sh [--reset]"; exit 1 ;;
    esac
done

BACK_PID=""
cleanup() { [ -n "$BACK_PID" ] && kill "$BACK_PID" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

venv_py() {
    if [ -f "$ROOT/.venv/Scripts/python.exe" ]; then echo "$ROOT/.venv/Scripts/python.exe"
    elif [ -f "$ROOT/.venv/bin/python" ]; then echo "$ROOT/.venv/bin/python"
    else echo ""; fi
}

# --- --reset: delete every temp/cache artifact that could hold stale data ---
if [ "$RESET" = "1" ]; then
    echo "=== --reset: deleting caches, node_modules, lock files, and venv ==="
    rm -rf "$ROOT/.venv"
    rm -rf "$ROOT/dashboard/frontend/node_modules"
    rm -f  "$ROOT/dashboard/frontend/package-lock.json"
    rm -rf "$ROOT/.pytest_cache"
    # __pycache__, *.pyc, *.egg-info anywhere under the repo (skip .git)
    find "$ROOT" -path "$ROOT/.git" -prune -o -type d -name "__pycache__" -print0 2>/dev/null | xargs -0 rm -rf 2>/dev/null || true
    find "$ROOT" -path "$ROOT/.git" -prune -o -type d -name "*.egg-info" -print0 2>/dev/null | xargs -0 rm -rf 2>/dev/null || true
    find "$ROOT" -path "$ROOT/.git" -prune -o -type f -name "*.pyc" -print0 2>/dev/null | xargs -0 rm -f 2>/dev/null || true
    echo "Reset done. Rebuilding..."
fi

# --- venv (corruption-guarded via .venv/.ready marker) ---
# The marker is written ONLY after a fully successful install. A .venv without
# it means a previous setup was interrupted, so we rebuild from scratch.
PY="$(venv_py)"
if [ -n "$PY" ] && [ ! -f "$ROOT/.venv/.ready" ]; then
    echo "Existing .venv is incomplete (interrupted setup?) - rebuilding..."
    rm -rf "$ROOT/.venv"
    PY=""
fi
if [ -z "$PY" ]; then
    echo "Creating virtualenv and installing dependencies..."
    python -m venv "$ROOT/.venv" 2>/dev/null || python3 -m venv "$ROOT/.venv"
    PY="$(venv_py)"
    "$PY" -m pip install --upgrade pip >/dev/null
    "$PY" -m pip install -r "$ROOT/requirements.txt"
    # Playwright browser binary (PSA graded-card cert scraping). Not covered by
    # requirements.txt — the pip package still needs its Chromium downloaded.
    echo "Installing Playwright Chromium (PSA scraping)..."
    "$PY" -m playwright install chromium
    : > "$ROOT/.venv/.ready"   # mark ready only after a clean, complete install
fi

# --- frontend deps ---
if [ ! -d "$ROOT/dashboard/frontend/node_modules" ]; then
    echo "Installing frontend dependencies (npm install)..."
    ( cd "$ROOT/dashboard/frontend" && npm install )
fi

echo
echo "Starting Flask backend on http://localhost:5000 ..."
( cd "$ROOT/dashboard/backend" && "$PY" app.py ) &
BACK_PID=$!

echo "Starting Vite frontend on http://localhost:5173 ..."
echo
echo "============================================================"
echo "  Dashboard:  http://localhost:5173   (open in Chrome)"
echo "  API:        http://localhost:5000/api"
echo "  Ctrl-C to stop both."
echo "============================================================"
( cd "$ROOT/dashboard/frontend" && npm run dev )
