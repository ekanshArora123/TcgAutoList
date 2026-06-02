#!/usr/bin/env bash
# ============================================================
#  TcgAutoList — run the dashboard (Flask backend + React UI)
#  First run sets up the venv, installs deps, and npm-installs
#  the frontend. Ctrl-C stops both.
#
#  Full agent (Telegram) instead of the dashboard:
#    "$PY" -m shared.main      (needs .env tokens)
# ============================================================
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

# --- Locate / create the venv python (Git-Bash on Windows vs Unix) ---
venv_py() {
    if [ -f "$ROOT/.venv/Scripts/python.exe" ]; then echo "$ROOT/.venv/Scripts/python.exe"
    elif [ -f "$ROOT/.venv/bin/python" ]; then echo "$ROOT/.venv/bin/python"
    else echo ""; fi
}

PY="$(venv_py)"
if [ -z "$PY" ]; then
    echo "Creating virtualenv..."
    python -m venv .venv 2>/dev/null || python3 -m venv .venv
    PY="$(venv_py)"
fi

# --- Install packages if card_server isn't importable yet ---
if ! "$PY" -c "import card_server" >/dev/null 2>&1; then
    echo "Installing Python dependencies (pip install -e .)..."
    "$PY" -m pip install -e . >/dev/null
fi

# --- Frontend deps ---
if [ ! -d "$ROOT/dashboard/frontend/node_modules" ]; then
    echo "Installing frontend dependencies (npm install)..."
    ( cd "$ROOT/dashboard/frontend" && npm install )
fi

echo
echo "Starting Flask backend on http://localhost:5000 ..."
( cd "$ROOT/dashboard/backend" && "$PY" app.py ) &
BACK_PID=$!

# Stop the backend when this script exits (Ctrl-C, etc.)
trap 'echo; echo "Stopping..."; kill "$BACK_PID" 2>/dev/null' EXIT INT TERM

echo "Starting Vite frontend on http://localhost:5173 ..."
echo
echo "============================================================"
echo "  Dashboard:  http://localhost:5173   (open in Chrome)"
echo "  API:        http://localhost:5000/api"
echo "  Ctrl-C to stop both."
echo "============================================================"
( cd "$ROOT/dashboard/frontend" && npm run dev )
