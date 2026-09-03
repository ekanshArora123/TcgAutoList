@echo off
REM ============================================================
REM  TcgAutoList - collect pricing data for owned cards
REM  Runs services.card_server.collect. Fetches active + sold TCGplayer
REM  listings, writes aggregate `market_snapshots`, runs the pricing
REM  algorithm and stores estimates in `prices` - the price the dashboard
REM  shows. This IS the pricing path (contrast collect_sales.bat, which
REM  only gathers per-card graph data and never touches pricing).
REM
REM  First run sets up the venv + installs deps (same as run.bat).
REM  Loads repo .env so TCGPLAYER_AUTH_COOKIE / DB_PATH are picked up.
REM
REM  Idempotent per day: cards already collected today are skipped, and
REM  re-runs upsert (never duplicate) that day's snapshot + price rows.
REM
REM  Order: most expensive card first, by each card's latest stored estimate
REM  (never-priced cards last), so a run that dies on a rate-limit wall or gets
REM  cancelled has already refreshed the cards worth the most. Applies to the
REM  selection modes; an explicit --cards list keeps the order given.
REM
REM  Usage (all args are forwarded to the Python runner):
REM    collect.bat                    collect all owned cards
REM    collect.bat --stale            owned cards not collected in 7+ days
REM    collect.bat --stale 14         owned cards not collected in 14+ days
REM    collect.bat --cohort           same-set neighbors (not owned)
REM    collect.bat --cards 123,456    specific TCGplayer card IDs
REM    collect.bat --delay 1000       ms between cards (default 500)
REM    collect.bat --variant-delay 0  ms between a card's variant calls (default 250)
REM    collect.bat --force            re-collect cards already collected today
REM    collect.bat --reprice-only     recompute prices from stored data, no fetching
REM
REM  A collect run stores each card's market state, then prices it FROM that
REM  store. --reprice-only redoes only the second half, so a changed pricing
REM  constant can be applied across the collection in seconds with no requests.
REM  It accepts the same selection flags (--stale / --cohort / --cards).
REM
REM  NOTE: TCGPLAYER_AUTH_COOKIE is optional here - it only widens sold-
REM        listing pagination for the sold sanity check. The primary anchor
REM        (lowest active listing) works without it.
REM ============================================================
setlocal
cd /d "%~dp0"

for %%a in (%*) do (
    if /I "%%a"=="-h" goto :usage
    if /I "%%a"=="--help" goto :usage
)

REM --- venv (corruption-guarded via .venv\.ready marker, same as run.bat) ---
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
    echo ready> ".venv\.ready"
)

REM --- Load repo .env (KEY=VALUE lines; '#' comments and blanks skipped) ---
if exist ".env" (
    for /f "usebackq eol=# tokens=1* delims==" %%a in (".env") do (
        if not "%%~a"=="" set "%%a=%%b"
    )
)

REM Run the collector, forwarding all args; propagate its exit code.
.venv\Scripts\python.exe -m services.card_server.collect %*
set "RC=%ERRORLEVEL%"

endlocal & exit /b %RC%

:usage
echo Usage: collect.bat [--stale [days] ^| --cohort ^| --cards id1,id2] [--delay ms]
echo                    [--variant-delay ms] [--force] [--reprice-only]
echo   no args           collect all owned cards
echo   --stale [N]       owned cards not collected in N+ days (default 7)
echo   --cohort          same-set neighbors of owned cards (not owned)
echo   --cards LIST      comma-separated TCGplayer card IDs
echo   --delay MS        milliseconds between cards (default 500)
echo   --variant-delay MS  milliseconds between a card's variant calls (default 250)
echo   --force           re-collect cards already collected today
echo   --reprice-only    recompute prices from stored data, no fetching
echo.
echo   Cards are collected most-expensive-first [latest stored estimate].
endlocal
