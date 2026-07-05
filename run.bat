@echo off
REM ============================================================
REM  TcgAutoList - run the dashboard (Flask backend + React UI)
REM  First run sets up the venv, installs deps, and npm-installs
REM  the frontend. Opens two windows; close them to stop.
REM
REM  Options:
REM    --reset   Delete all caches/temp state first (every __pycache__,
REM              *.pyc, *.egg-info, .pytest_cache, the frontend node_modules
REM              + package-lock.json, and the .venv), then rebuild everything.
REM
REM  Full agent (Telegram) instead of the dashboard (run from repo root):
REM    .venv\Scripts\python.exe -m dashboard.backend.orchestrator.main  (needs .env)
REM ============================================================
setlocal
cd /d "%~dp0"

set "RESET=0"
for %%a in (%*) do (
    if /I "%%a"=="--reset" set "RESET=1"
    if /I "%%a"=="-h" goto :usage
    if /I "%%a"=="--help" goto :usage
)

if "%RESET%"=="1" (
    echo === --reset: deleting caches, node_modules, lock files, and venv ===
    if exist ".venv" rd /s /q ".venv"
    if exist "dashboard\frontend\node_modules" rd /s /q "dashboard\frontend\node_modules"
    if exist "dashboard\frontend\package-lock.json" del /q "dashboard\frontend\package-lock.json"
    if exist ".pytest_cache" rd /s /q ".pytest_cache"
    REM __pycache__, *.egg-info dirs, and *.pyc anywhere under the repo
    for /d /r %%d in (__pycache__) do if exist "%%d" rd /s /q "%%d"
    for /d /r %%d in (*.egg-info) do if exist "%%d" rd /s /q "%%d"
    del /s /q *.pyc 1>nul 2>nul
    echo Reset done. Rebuilding...
)

REM --- venv (corruption-guarded via .venv\.ready marker) ---
REM The marker is written ONLY after a fully successful install. A .venv without
REM it means a previous setup was interrupted, so we rebuild from scratch.
if exist ".venv\Scripts\python.exe" if not exist ".venv\.ready" (
    echo Existing .venv is incomplete [interrupted setup?] - rebuilding...
    rd /s /q ".venv"
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtualenv and installing dependencies...
    py -3 -m venv .venv
    if errorlevel 1 python -m venv .venv
    .venv\Scripts\python.exe -m pip install --upgrade pip
    .venv\Scripts\python.exe -m pip install -r requirements.txt
    if errorlevel 1 (
        echo ERROR: dependency install failed. .venv left unmarked; rerun to rebuild.
        exit /b 1
    )
    REM Playwright browser binary (PSA graded-card cert scraping). Not covered by
    REM requirements.txt - the pip package still needs its Chromium downloaded.
    echo Installing Playwright Chromium [PSA scraping] ...
    .venv\Scripts\python.exe -m playwright install chromium
    REM mark ready only after a clean, complete install
    echo ready> ".venv\.ready"
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
goto :eof

:usage
echo Usage: run.bat [--reset]
endlocal
