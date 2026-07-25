# Grading providers (graded cards)

Fetchers that pull graded-card data from grading companies by cert number —
scraping the site or hitting an API as the source demands (PSA is **scraped**; see
below) — plus notes on how graded cards are modeled. Sibling to `tcgplayer/`; one
module per company (`psa.py` today), behind a small provider registry so more
companies drop in later. The graded DB chain (`graded_skus` / `graded_inventory`
/ `graded_prices`) lives in `helpers/crud/graded_*.py`; the write workflow is
`CollectionService.add_graded_by_cert(...)`.

## Data model (parallel to the raw chain)

Graded cards are a **parallel chain** mirroring the raw one — `graded_skus` ‖
`skus`, `graded_inventory` ‖ `inventory`, `graded_prices` ‖ `prices` — so the raw
path stays untouched and the two meet only at the read layer (the dashboard
browse). A future kind (e.g. sealed) should follow the same pattern. Graded used
to ride on the raw `skus.specialty_two` convention; that column now holds only
error attributes (miscuts, holo bleeds), which is why graded got its own tables.

Identity keys:
- **`graded_skus`** dedups on `(grading_company, grader_spec_id, grade)` via a
  partial unique index (a legacy card-based key is kept for manual adds that have
  no grader spec).
- **A cert number is one physical slab**, so `graded_inventory.cert_id` has a
  partial unique index (non-null) and `add_graded_by_cert` is **idempotent** —
  re-adding a cert refreshes it (pop/images) rather than creating a duplicate.

Frontend: graded cards get their own **"Graded" tab**, not mixed into the raw
collection grid (keeps the raw browse untouched).

### Detail page — one slab, with its grade class as context

The page's **subject is one physical slab** (the cert in the URL); the spec-level
grade class sits beside it as read-only context. Route: `/graded/slab/:certId` (a
tile links here); backend `reporting.graded_slab_detail` returns `slab` + `grades`.
The cert determines its spec (one lookup), so the spec is *not* a URL param — the
response just carries both tiers.

- **Slab** (`graded_inventory`, the left panel): the focused cert — large front +
  back image, status, and the **`to_crack`** action. The only editable tier.
- **Grade class** (`graded_skus`, the right panel): every grade of this card —
  qty owned, population, price — shown **read-only** (you change graded qty by
  adding/removing slabs, never a qty box). The page groups **grades of one card
  within a company by `grader_spec_id`** (PSA's spec is per card-variety, shared
  across grades), so PSA 9 and PSA 10 of the same card appear as sibling rows.
  **Cross-company grouping (PSA vs CGC of the same card) is deferred** — it needs
  the graded→raw mapper (future work #1); until then each company groups on its own
  spec. Price-over-time is a reserved area, filled once graded pricing exists.
  Each owned cert is its own page (same spec context); you reach each from its tile.

**"To crack"** (a slab marked for cracking out + regrading) is a plain **reused
tag** on `graded_inventory.tags` (`to_crack`), not a status or queue — toggled via
`PATCH /api/graded/slab/<id>/tag`. Tag add/remove for both the raw and graded
chains shares one helper, `helpers/crud/tags.py`.

## Design: identity is decoupled from the TCGplayer link

A graded card's **identity comes from the grading company** (PSA's public cert
page: year, set, number, subject, variety, grade — and population once that's
wired up, see future work), not from TCGplayer. This works for 100% of graded
cards, in any language, including cards TCGplayer doesn't carry.

The link to a TCGplayer `cards` row (`graded_skus.card_id`) is **optional and
nullable** — it drives the (future) raw-vs-graded price comparison and the raw
fallback image. It's set **manually** two ways — at add time (a TCGplayer id field
on the add form) or later from the **slab detail page's TCGplayer-ID editor**
(`set_graded_link_by_cert`, which applies the link to the whole spec group since
the raw card is the same across grades); otherwise it stays `NULL`. Both go
through the same `_resolve_card_link` validation. Linking is robust:
a provided id is pegged — with a minimal stub `cards` row built from the grader's
own fields — whenever it has a real image on TCGplayer's CDN, *even if the
search-API metadata lookup returns nothing*, so the image still displays; a
typo'd id with no image is left unlinked (no junk `cards` row). The future
graded→raw converter (future work) will populate `card_id` comprehensively and
can replace this interim `_resolve_card_link` hook.

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

**Environment variables (all optional; only relevant to the fallback launch path
— CDP above needs none of them):**
- `PSA_USER_DATA_DIR` — path to a persistent Chrome profile folder. Improves
  Cloudflare reliability on the launch path (an established, signed-in session
  whose `cf_clearance` survives restarts) and is the prerequisite for the
  (deferred) population scrape. Unset → a throwaway profile.
- `PSA_HEADLESS=1` — force headless (normally leave off; Cloudflare blocks it).
  Only useful on an IP that isn't challenged.
- `PSA_BROWSER_CHANNEL` — use an installed Chrome channel (e.g. `chrome`) instead
  of Playwright's bundled Chromium.

**Best config for the fallback launch path** (CDP above is still preferred): point
`PSA_USER_DATA_DIR` at a persistent, PSA-logged-in Chrome profile (ideally with
`PSA_BROWSER_CHANNEL=chrome`). `fetch_cert` keeps ONE warm browser alive across
calls, and an established, trusted session makes Cloudflare far less likely to
re-challenge. Set it up once (needs a PSA/Collectors account):
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

1. **Graded → raw mapper (the "comprehensive converter").** Auto-resolve a
   TCGplayer `card_id` for a graded card so raw-vs-graded pricing (e.g. NM price
   vs PSA 8 price) can work. Today `card_id` is set only **manually** — the id
   field on the add form or the slab detail page's TCGplayer-ID editor (both via
   the isolated, removable `_resolve_card_link` hook) — or left `NULL`; there is
   **no auto-matcher** yet. When built, the converter becomes the single place that
   populates `card_id` and replaces the interim hook + manual editors. Must handle:
   English cards with a TCGplayer equivalent, same-card different-language, and
   cards that exist in neither (leave unlinked).

2. **Slab images — largely DONE** (see "Slab images" above): front + back are
   downloaded and stored at add time; both are now displayed (the tile shows the
   front, the slab detail page shows front + back). Remaining: a backfill for
   graded cards added before this existed (re-adding the cert re-fetches); and
   refresh if PSA's images change.

3. **Population report.** The scrape supports it (login-gated spec page), but it's
   **not wired on by default** — `population` / `population_higher` come back
   `None` until someone sets up the signed-in `PSA_USER_DATA_DIR` profile (see the
   login steps above) AND we surface pop in the UI. Deferred by owner for now; the
   plumbing exists (`_scrape_population` / `_summarize_population` in `psa.py`),
   just needs the auth profile + frontend display, and ideally a periodic pop
   refresh collector (mirroring `collect_sales.py`).

4. **Awkward / non-standard grades.** Grades usually run 1-10 (+ half), but PSA
   has values like `Authentic` and `PSA Unavailable` (no number), and error
   qualifiers concatenated to a number (`OC 8` = off-center 8, `MK 9` = mark 9).
   **Today (interim):** anything without a clean number is condensed to a `grade`
   of **-1** (displayed as **ERR**); the exact text is kept in `grade_label`, and
   qualifier+number values still parse to just the number. A full model would
   store the numeric grade + qualifier separately and display them properly.
   (`parse_grade` in `formatters.py`, the `-1` sentinel in `psa.py` / `add_graded_by_cert`,
   and the `ERR` rendering in `GradedCollectionPage`.)

5. **Graded price-history graph.** The data plane is **already recording it** —
   manual prices are stored in `graded_prices` keyed by `(graded_sku_id,
   calculation_date)`, so each day's saved price is a dated point (same-day
   re-edits overwrite that day; new days append), and `GradedPricesHelper.
   get_history()` already returns the series. What's missing is **display**: a
   read-only price-over-time chart on the slab detail page, filling the reserved
   "Price history across grades will graph here" placeholder — one line per grade,
   modeled on the raw per-card `SalesChart`. Moderate effort: a reporting method +
   endpoint + chart; no schema change (daily granularity; sub-day would need a
   timestamp key). Deferred by owner.

Also deferred (owner will handle): the raw-card price estimate shown next to a
graded card.
