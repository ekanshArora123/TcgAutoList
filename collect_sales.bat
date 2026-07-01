@echo off
REM ============================================================
REM  TcgAutoList - gather per-card graph data (sales + market price)
REM  Runs services.card_server.collect_sales. Populates the `sales` and
REM  `market_price_history` tables that power the per-card graph. Separate
REM  from the pricing collector (run collect.py) - never touches pricing.
REM
REM  First run sets up the venv + installs deps (same as run.bat).
REM  Loads repo .env so TCGPLAYER_AUTH_COOKIE / DB_PATH are picked up.
REM
REM  Usage (all args are forwarded to the Python runner):
REM    collect_sales.bat --crawl         ONE-TIME full raw backfill (bucket sweep + multi-day drip)
REM    collect_sales.bat --loop          paced batches until drained (ongoing refresh)
REM    collect_sales.bat --batch         one batch (150 cards, oldest-fetched first)
REM    collect_sales.bat --batch 50      one batch of 50
REM    collect_sales.bat                 collect all owned cards in one run
REM    collect_sales.bat --stale 14      owned cards not fetched in 14+ days
REM    collect_sales.bat --cards 123,456 specific TCGplayer card IDs
REM    collect_sales.bat --days 90       history window (default 365)
REM    collect_sales.bat --rate 1.0      avg requests/sec (default 1.5)
REM
REM  NOTE: full pagination needs TCGPLAYER_AUTH_COOKIE (browser
REM        TCGAuthTicket_Production cookie). Without it ~5 sales/card.
REM        Tip: --crawl is the one-time transaction-level backfill. The raw-sales
REM        endpoint is a global per-IP bucket (~0.15 req/s), so it self-paces and
REM        drips for hours/days, highest-value cards first. Fully resumable: stop
REM        with Ctrl-C and re-run any time. Afterwards use --loop to stay fresh.
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

if not defined TCGPLAYER_AUTH_COOKIE (
    echo WARNING: TCGPLAYER_AUTH_COOKIE is not set ^(no .env entry^).
    echo          The sales API will return only ~5 rows per card with no
    echo          pagination, so history will be incomplete.
    echo.
)

REM Run the collector, forwarding all args; propagate its exit code.
.venv\Scripts\python.exe -m services.card_server.collect_sales %*
set "RC=%ERRORLEVEL%"

endlocal & exit /b %RC%

:usage
echo Usage: collect_sales.bat [--crawl ^| --loop ^| --batch [N] ^| --stale [days] ^| --cards id1,id2] [--days N] [--rate R]
echo   --crawl        ONE-TIME full raw backfill: bucket sweep + multi-day adaptive drip (resumable)
echo   --loop         paced, resumable batches until backlog drained
echo   --batch [N]    one batch of N cards, oldest-fetched first (default 150)
echo   no args        collect all owned cards in one run
echo   --stale [N]    owned cards not fetched in N+ days (default 7)
echo   --cards LIST   comma-separated TCGplayer card IDs
echo   --days N       history window in days (default 365)
echo   --rate R       average requests/sec across endpoints (default 1.5)
echo   --max-age N    skip cards refreshed within N days (default 7)
endlocal
