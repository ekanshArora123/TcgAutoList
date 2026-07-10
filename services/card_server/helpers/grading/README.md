# Grading providers (graded cards)

Fetchers that pull graded-card data from grading companies' public APIs by cert
number, plus notes on how graded cards are modeled. Sibling to `tcgplayer/`; one
module per company (`psa.py` today), behind a small provider registry so more
companies drop in later. The graded DB chain (`graded_skus` / `graded_inventory`
/ `graded_prices`) lives in `helpers/crud/graded_*.py`; the write workflow is
`CollectionService.add_graded_by_cert(...)`.

## Design: identity is decoupled from the TCGplayer link

A graded card's **identity and population come from the grading company** (e.g.
PSA `GetByCertNumber`: year, set, number, subject, variety, grade, population).
This works for 100% of graded cards, in any language, including cards TCGplayer
doesn't carry.

The link to a TCGplayer `cards` row (`graded_skus.card_id`) is **optional and
nullable** — it exists only to enable raw-vs-graded price comparison later, and
is intentionally left `NULL` at add time for now.

Grader-identity columns on `graded_skus` are named **generically** (not
PSA-specific) so any grading company maps onto them — assume every company
exposes equivalents (`grader_spec_id`, `card_year`, `card_set`, `card_number`,
`card_subject`, `card_variety`, `card_language`, `grade_label`, `population`,
`population_higher`, `pop_fetched_at`).

## PSA fetching = website scraping (headed browser)

`psa.py` gathers a cert's data by **scraping the public PSA website with a headed
Playwright/Chromium browser**, not the official API. Why: PSA's JSON API works
but its quota is ~1 call/day on this account (HTTP 429 "maximum admitted 1 per
Day"), so it's unusable. The website isn't quota-limited — only Cloudflare-
protected + JS-rendered — so we drive a real browser through it. See the module
docstring in `psa.py` for the full rationale and the exact DOM selectors.

### Recommended: CDP attach (`PSA_CDP_URL`) — fast & reliable

Set `PSA_CDP_URL` and the code **attaches to an already-running Chrome** over the
DevTools protocol instead of launching its own. Measured **~0.5–2s per warm
lookup** (~4s cold), no Cloudflare wedging — because a normally-launched Chrome
looks legitimate to Cloudflare (Playwright *launching* Chromium trips its
automation detection, which is what makes the fallback path slow/wedgy). We only
open our own tab and, on teardown, only **disconnect** — your Chrome and your
tabs are never touched. Setup:

1. Start Chrome once with a debug port + a dedicated profile (keep it open):
   `chrome.exe --remote-debugging-port=9222 --user-data-dir="C:\psa-chrome"`
   (dedicated profile so it doesn't clash with your everyday Chrome; or fully
   quit Chrome and relaunch your normal profile with the flag).
2. `PSA_CDP_URL=http://localhost:9222` (or `PSA_CDP_PORT=9222`) in `.env`.

The warm browser (below) reuses this one connection across adds.

### Fallback (no CDP): launch our own browser

Without `PSA_CDP_URL`, `psa.py` launches its own **headed** browser (Cloudflare
blocks headless). One browser is kept **warm** across calls (a persistent
background event-loop thread — Playwright objects are loop-bound but Flask hands a
fresh `asyncio.run` loop per request), so the launch + Cloudflare solve is paid
once. Caveats of this path:
- **The window must hold OS foreground** or Cloudflare re-challenges and a lookup
  fails fast (~15s). So repeat lookups are unreliable unless you're at the desktop
  — which is exactly why CDP is recommended.
- **Not deployable headless.**
- **DOM scraping is fragile** — PSA can change markup and the `_CERT_JS` /
  `_POP_JS` selectors in `psa.py` would need updating (applies to both paths).

**Requires:** `playwright` (in `requirements.txt`) **and** its Chromium binary
(`python -m playwright install chromium` — run.bat / run.sh do this on venv
setup; a manual clone must run it once).

**Environment variables (all optional):**
- `PSA_USER_DATA_DIR` — path to a persistent Chrome profile folder. Set this to
  enable **population** scraping (see login below). Unset → a throwaway profile →
  identity only, population is `None`.
- `PSA_HEADLESS=1` — force headless (normally leave off; Cloudflare blocks it).
  Only useful on an IP that isn't challenged.
- `PSA_BROWSER_CHANNEL` — use an installed Chrome channel (e.g. `chrome`) instead
  of Playwright's bundled Chromium; handy for reusing an existing signed-in Chrome
  profile.

**A persistent, signed-in profile helps Cloudflare reliability.** `fetch_cert`
keeps ONE warm browser alive across calls (see the module docstring); pointing it
at a persistent, PSA-logged-in Chrome profile via `PSA_USER_DATA_DIR` (ideally
with `PSA_BROWSER_CHANNEL=chrome` so it's real Google Chrome) gives Cloudflare an
established, trusted session and its `cf_clearance` cookie survives restarts — the
most reliable + fastest config. Set it up once (needs a PSA/Collectors account):
1. Point `PSA_USER_DATA_DIR` at a **dedicated** folder (Playwright's own profile;
   NOT your everyday Chrome profile — a running Chrome locks its profile).
2. `python -m services.card_server.helpers.grading.psa --login` — a real window
   opens; **you** sign in by hand (the code never handles your credentials), then
   press Enter. The session persists in that folder and later runs reuse it (log
   in once, not every time).

**Population is currently NOT scraped** (deferred — see future work below):
`fetch_cert` reads only the public cert *identity* page for speed/consistency, so
`population` / `population_higher` always come back `None` even with a signed-in
profile. PSA's population report is behind the same sign-in wall; the scrape
plumbing exists (`_scrape_population`) but is not wired into `fetch_cert` yet.

## Slab images (PSA front/back)

At add time the scraper extracts the two cert-page image URLs — PSA shows a
**front and a back, or neither** — from PSA's public CloudFront CDN (which is
*not* Cloudflare-gated). `store_graded_images` (in `scripts/fetch_images.py`)
downloads both straight with httpx and converts to webp.

**Storage format — one folder per cert, sides suffixed `f`/`b`:**

    data/graded-card-images/{cert}/{cert}f.webp    # front
    data/graded-card-images/{cert}/{cert}b.webp    # back

Served at `GET /api/graded-images/{cert}` (front) and `/api/graded-images/{cert}/back`.
The graded tile displays the **front**, falling back to the linked raw card image,
then a placeholder. The **back is stored but not shown yet.** Download is
best-effort at add time (a failure never blocks the add) and happens only then —
the opaque CDN URLs are available only during the scrape, so there's no on-demand
re-fetch; re-adding a cert re-downloads.

## ⚠️ Future work — these features are DEFERRED and MUST be built

1. **Graded → raw mapper (the "comprehensive converter").** Resolve a TCGplayer
   `card_id` for a graded card so raw-vs-graded pricing (e.g. NM price vs PSA 8
   price) can work. Today `card_id` is left `NULL`; there is **no** auto-matcher
   and **no** manual card-picker yet. When built, this converter is the single
   place that populates `graded_skus.card_id` — any interim linking hook must be
   isolated and removable so this replaces it cleanly. Must handle: English cards
   with a TCGplayer equivalent, same-card different-language, and cards that exist
   in neither (leave unlinked).

2. **Slab images — largely DONE** (see "Slab images" above): front + back are
   downloaded and stored at add time, and the front is displayed. Remaining:
   display the back in the UI; a backfill for graded cards added before this
   existed (re-adding the cert re-fetches); and refresh if PSA's images change.

3. **Population report.** The scrape supports it (login-gated spec page), but it's
   **not wired on by default** — `population` / `population_higher` come back
   `None` until someone sets up the signed-in `PSA_USER_DATA_DIR` profile (see the
   login steps above) AND we surface pop in the UI. Deferred by owner for now; the
   plumbing exists (`_scrape_population` / `_summarize_population` in `psa.py`),
   just needs the auth profile + frontend display, and ideally a periodic pop
   refresh collector (mirroring `collect_sales.py`).

Also deferred (owner will handle): the raw-card price estimate shown next to a
graded card.
