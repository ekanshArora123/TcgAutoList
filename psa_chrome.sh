#!/usr/bin/env bash
# ============================================================
#  psa_chrome.sh - launch a Chrome for the PSA graded-card cert scraper to
#  attach to over CDP (fast, ~1-2s/lookup, no Cloudflare wedging). Leave the
#  launched Chrome open while using the dashboard.
#
#  Then add ONE line to your .env:   PSA_CDP_URL=http://localhost:9222
#  (nothing needs to go in your OS/system environment variables.)
#
#  Usage:  ./psa_chrome.sh [port] [profile_dir]
#    port         default 9222 (must match PSA_CDP_URL)
#    profile_dir  default ~/.psa-chrome (a DEDICATED profile so it never clashes
#                 with your everyday Chrome)
# ============================================================
set -e
PORT="${1:-9222}"
PROFILE="${2:-$HOME/.psa-chrome}"

CHROME=""
for p in \
  "/c/Program Files/Google/Chrome/Application/chrome.exe" \
  "/c/Program Files (x86)/Google/Chrome/Application/chrome.exe" \
  "$LOCALAPPDATA/Google/Chrome/Application/chrome.exe" \
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  "$(command -v google-chrome 2>/dev/null || true)" \
  "$(command -v chromium 2>/dev/null || true)"; do
  if [ -n "$p" ] && [ -x "$p" ]; then CHROME="$p"; break; fi
done

if [ -z "$CHROME" ]; then
  echo "ERROR: Chrome/Chromium not found. Install it or edit this script's search list."
  exit 1
fi

echo "Chrome:  $CHROME"
echo "Port:    $PORT"
echo "Profile: $PROFILE"
echo "Launching Chrome with remote debugging on port $PORT ..."
"$CHROME" --remote-debugging-port="$PORT" --user-data-dir="$PROFILE" "https://www.psacard.com" >/dev/null 2>&1 &
echo "Chrome launched (PID $!). Leave it open."
echo
echo "Add this line to your .env (if not already):"
echo "    PSA_CDP_URL=http://localhost:$PORT"
