@echo off
REM ============================================================
REM  psa_chrome.bat - launch a Chrome instance for the PSA graded-card
REM  cert scraper to attach to over CDP (fast, ~1-2s/lookup, no Cloudflare
REM  wedging). Leave the launched Chrome window OPEN while using the dashboard.
REM
REM  Then add ONE line to your .env:   PSA_CDP_URL=http://localhost:9222
REM  (nothing needs to go in your OS/system environment variables.)
REM
REM  Usage:  psa_chrome.bat [port] [profile_dir]
REM    port         default 9222 (must match PSA_CDP_URL)
REM    profile_dir  default %LOCALAPPDATA%\psa-chrome  (a DEDICATED profile so it
REM                 never clashes with your everyday Chrome; sign into PSA here
REM                 once if you want the future population feature)
REM ============================================================
setlocal

set "PORT=%~1"
if "%PORT%"=="" set "PORT=9222"

set "PROFILE=%~2"
if "%PROFILE%"=="" set "PROFILE=%LOCALAPPDATA%\psa-chrome"

REM --- locate chrome.exe ---
set "CHROME="
for %%P in (
    "%ProgramFiles%\Google\Chrome\Application\chrome.exe"
    "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
    "%LocalAppData%\Google\Chrome\Application\chrome.exe"
) do if exist "%%~P" if not defined CHROME set "CHROME=%%~P"

if not defined CHROME (
    echo ERROR: chrome.exe not found in the usual locations.
    echo Install Google Chrome, or pass its full path is not supported here -
    echo edit this script's search list.
    exit /b 1
)

echo Chrome:  "%CHROME%"
echo Port:    %PORT%
echo Profile: "%PROFILE%"
echo.
echo Launching Chrome with remote debugging on port %PORT% ...
start "PSA Chrome (CDP :%PORT%)" "%CHROME%" --remote-debugging-port=%PORT% --user-data-dir="%PROFILE%" "https://www.psacard.com"

echo.
echo ============================================================
echo  Chrome is up for scraping. Leave that window open while you
echo  add graded cards in the dashboard.
echo.
echo  If you haven't already, add this line to your .env:
echo      PSA_CDP_URL=http://localhost:%PORT%
echo ============================================================
endlocal
