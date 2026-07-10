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

**Operational realities (by design — accept or optimize later):**
- **A real Chrome window opens on every scrape and must stay visible on-screen.**
  Cloudflare fingerprints headless Chrome and re-blocks a minimized/off-screen
  window, so headless is disabled by default. This needs a desktop/display
  session → it **cannot run on a headless deployed server** as-is.
- **~30–45s per add.** Almost all of it is Cloudflare's "Just a moment…"
  challenge clearing on each page navigation, plus a fresh browser launch per
  call. With population enabled a *second* gated page loads (~doubles it). The
  `POST /api/graded` request is synchronous, so it holds that request/worker open
  for the duration (fine for a single-user local dashboard). Optimizable later by
  keeping a warm browser/session instead of launching one per add.
- **DOM scraping is fragile** — PSA can change class names / markup at any time
  and the `_CERT_JS` / `_POP_JS` selectors in `psa.py` would then need updating.

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

## ⚠️ Future work — these features are DEFERRED and MUST be built

1. **Graded → raw mapper (the "comprehensive converter").** Resolve a TCGplayer
   `card_id` for a graded card so raw-vs-graded pricing (e.g. NM price vs PSA 8
   price) can work. Today `card_id` is left `NULL`; there is **no** auto-matcher
   and **no** manual card-picker yet. When built, this converter is the single
   place that populates `graded_skus.card_id` — any interim linking hook must be
   isolated and removable so this replaces it cleanly. Must handle: English cards
   with a TCGplayer equivalent, same-card different-language, and cards that exist
   in neither (leave unlinked).

2. **Comprehensive slab-image getter.** Fetch + store graded slab images by cert
   number. Blocked today: PSA's free API image endpoint is quota-gated (~1/day)
   and the website is Cloudflare-protected (plain GETs 403). Until this exists,
   graded cards have **no** slab image (the frontend falls back to the raw card
   image where a `card_id` link exists, else a placeholder). Possible approach:
   spend the 1/day image call to learn PSA's image-CDN URL pattern, then fetch
   images off the CDN directly; or a headless-browser scrape; or a paid tier.

3. **Population report.** The scrape supports it (login-gated spec page), but it's
   **not wired on by default** — `population` / `population_higher` come back
   `None` until someone sets up the signed-in `PSA_USER_DATA_DIR` profile (see the
   login steps above) AND we surface pop in the UI. Deferred by owner for now; the
   plumbing exists (`_scrape_population` / `_summarize_population` in `psa.py`),
   just needs the auth profile + frontend display, and ideally a periodic pop
   refresh collector (mirroring `collect_sales.py`).

Also deferred (owner will handle): the raw-card price estimate shown next to a
graded card.
