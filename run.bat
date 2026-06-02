@echo off
REM ============================================================
REM  TcgAutoList - run the dashboard (Flask backend + React UI)
REM  First run sets up the venv, installs deps, and npm-installs
REM  the frontend. Opens two windows; close them to stop.
REM
REM  Full agent (Telegram) instead of the dashboard (run from repo root):
REM    .venv\Scripts\python.exe -m dashboard.backend.orchestrator.main  (needs .env)
REM ============================================================
setlocal
cd /d "%~dp0"

REM --- Python venv ---
if not exist ".venv\Scripts\python.exe" (
    echo Creating virtualenv...
    py -3 -m venv .venv
    if errorlevel 1 python -m venv .venv
)

REM --- Install Python dependencies if not present yet ---
.venv\Scripts\python.exe -c "import flask, mcp, telegram" 1>nul 2>nul
if errorlevel 1 (
    echo Installing Python dependencies [pip install -r requirements.txt] ...
    .venv\Scripts\python.exe -m pip install -r requirements.txt
)

REM --- Frontend deps ---
if not exist "dashboard\frontend\node_modules" (
    echo Installing frontend dependencies [npm install] ...
    pushd dashboard\frontend
    call npm install
    popd
)

echo.
REM New windows inherit this working directory, so relative paths are space-safe.
echo Starting Flask backend on http://localhost:5000 ...
start "TcgAutoList Backend" cmd /k ".venv\Scripts\python.exe dashboard\backend\app.py"

echo Starting Vite frontend on http://localhost:5173 ...
start "TcgAutoList Frontend" cmd /k "cd dashboard\frontend && npm run dev"

echo.
echo ============================================================
echo  Dashboard:  http://localhost:5173   [open in Chrome]
echo  API:        http://localhost:5000/api
echo  Two windows opened (backend + frontend). Close them to stop.
echo ============================================================
endlocal
