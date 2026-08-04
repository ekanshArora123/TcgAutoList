# TcgAutoList — TCG Automation Platform

## Project Overview

TcgAutoList is a growing platform of **interconnected capabilities** that assist with all sorts of trading-card-game (TCG) problems. The pieces are not isolated projects — they work together like organs. A card-data store, TCGplayer scrapers, a pricing engine, market/sales data collectors, an analytics dashboard, and a seller listing pipeline each build on and reuse the others.

There is no fixed end product. The platform accretes capabilities over time toward the open-ended goal of assisting with TCG problems. It is built primarily for one person's own needs today (managing and selling a ~10,000 card Pokemon collection), with a longer-term ambition to deploy it so other buyers and sellers can use it too.

**Core tenets:**
- **Reuse first.** Before writing new code, make use of existing functionality. This is the whole point — capabilities compound instead of duplicating each other.
- **Modular and generally usable.** Design components to be reused and composed, with clear boundaries and a shared substrate — not one-offs.

**Working model:** there's no fixed rule for what comes next — each session either **improves an existing capability** (e.g. make pricing more robust or agentic, harden a scraper) or **adds a new one** (e.g. vendor live-sales tracking), whichever is most valuable at the time. New work should lean on what already exists.

## Capabilities

The working capabilities in the repo today — built and in use. They reuse each other; the **Builds on** column shows the composition. New work should extend this web, not duplicate it. Active and planned projects are in **Status & Roadmap** below.

| Capability | Lives in | What it does | Builds on |
|------------|----------|--------------|-----------|
| **Card-data store + MCP server** | `services/card_server/` (`db.py`, `schema.sql`, `index.py`) | SQLite substrate (cards/skus/inventory/prices + market tables) exposed as CRUD services. | The shared foundation — everything else builds on it. |
| **TCGplayer scrapers/fetchers** | `helpers/tcgplayer/` | Active/sold listings, card metadata, condition/finish formatters, shared HTTP transport. See `docs/tcgplayer-api.md`. | TCGplayer APIs. |
| **Pricing engine** | `helpers/pricing/` via `pricing_service.py` | Lowest-listing anchor blended with an age-weighted sold mean, confidence scoring, cross-condition extrapolation, liquid value. Prices from *stored* inputs, so a config change reprices the collection offline. | Card-data store, scrapers, market-data. |
| **Market-data collector** | `helpers/market/` + `collect.py` | Fetches listings/solds per owned variant and records the aggregate stats the pricer reads → `market_snapshots`, then invokes the pricing engine to write `prices`. Idempotent per date. | Card-data store, scrapers. |
| **Sales-history + market-price collector & graph** | `sales_collector.py`, `sales_store.py`, `price_history_store.py`, `collect_sales.py` | ~1yr of raw sold listings + weekly TCGplayer market price for the per-card graph. Kept out of the pricing path. | Card-data store, scrapers/transport. |
| **Analytics dashboard** | `dashboard/frontend/` + `dashboard/backend/app.py` → `reporting_service.py` | Browse/filter/search the collection, charts, per-card detail + sales graph. | Card-data store (reporting), sales/market data. |
| **Seller listing pipeline** | `services/telegram/` + `dashboard/backend/orchestrator/` + `services/ebay/` | Turns owned inventory into live listings: `/next` → pick unlisted card → fetch price → route by tier → request photo → build listing → post → mark done. | Card-data store, pricing, Telegram, eBay. |
| **Graded cards (PSA)** | `helpers/grading/` + `helpers/crud/graded_*` + graded methods in `collection_service`/`reporting_service` + a "Graded" dashboard tab | Add a graded slab by cert #: scrape PSA (headed browser) for identity/population + front/back images, store as a parallel chain, browse them. See `helpers/grading/README.md`. | Card-data store, the browser scraper, TCGplayer (fallback image). |

## Status & Roadmap

What's in progress or planned and where each is heading — so new work coordinates with it instead of duplicating it. Stable intent, not a granular changelog.

| Project | Stage | Direction |
|---------|-------|-----------|
| **eBay posting** | Stub (`services/ebay/service.py` returns a fake ID) | Real eBay Sell API — listing CRUD, photo upload, category mapping. The seller pipeline's final step. |
| **Tier 2/3 LLM pricing** | Pseudocode (`orchestrator/llm.py`, `tools.py`, `system_prompt.py`) | Scoped-context (T2) / full-context (T3) LLM pricing for hard cards. See the tiered-escalation note under Architecture. |
| **Price override** | Stub (orchestrator detects a numeric reply but doesn't apply it) | Let a human override the computed price mid-flow. |
| **Sell/hold decision engine** | Design frontier; data plane already collecting | Decide which cards to sell vs hold from longitudinal price data (`sales` + `market_price_history`). Layered stats with LLM escalation on the tail. Coordinate with the pricing engine — don't re-derive prices. |
| **Dashboard as control plane** | Trigger workflows, override prices, manage listings from the UI, etc.|
| **DevOps / deployment** | Vercel (frontend) + a simple Azure pipeline exist | A sub-project like any other, following the tenets: harden CI/CD, secrets, environments. |
| **Vendor live-sales tracking** | Idea | Let vendors track sales in real time — an example of the platform reaching beyond the owner's own selling. |
| **MCP server(s)** | Deferred (not a current concern) | FastMCP server(s) that will be implemented later to wrap the existing services (card-server CRUD/pricing/reporting, etc.) as LLM-callable tools.|

## Architecture

See `docs/STRUCTURE.md` for the full folder layout and the **Capabilities** table for what lives where. Entry point: `dashboard/backend/orchestrator/main.py` wires the services + orchestrator and starts the app.

**Tiered escalation (pricing/seller).** LLM involvement scales with pricing difficulty: most cards run a coded, no-LLM path; harder cards escalate to scoped or full LLM context. Thresholds live in `dashboard/backend/orchestrator/types.py` (`DEFAULT_TIER_CONFIG`); the full model is in the seller pipeline's local doc, `dashboard/backend/orchestrator/PIPELINE.md`.

### Key Design Decisions (platform-wide)

- **All durable state in SQLite.** Every capability can stop/restart without losing progress; the DB is the single source of truth.
- **Idempotent by default.** Re-running skips work already done (already-listed cards, already-collected snapshots, …).
- **Services own their external system.** card-server owns the DB/TCGplayer/pricing; telegram owns Telegram; ebay owns eBay. Callers don't duplicate owned functionality or depend on a service's internal format.
- **New item "kinds" get a parallel chain.** A new kind (graded cards, sealed product) mirrors the raw chain with its own tables (`graded_skus`/`graded_inventory`/`graded_prices`) and unifies only at the read layer, leaving the raw path untouched. Details in `helpers/grading/README.md`, will likely vary for every kind.
- **Reduce redundancy with specific helpers; don't over-abstract.** Whenever two paths share real logic, extract a *specific* helper — one concrete, currently-shared piece of behavior — and have each caller pass its own parameters (e.g. `save_webp_from_url` shared by the raw and graded image fetches; the reporting `_paginate`/`_page_result` helpers shared by raw and graded browse). Do this consistently — cutting duplication is the goal. The only thing to avoid is *speculative* abstraction: don't build a generic framework for a case that doesn't exist yet; rule of thumb is if the type of problem is gonna be encountered with the addition of new functionality, then it makes sense to abstract. But if its restricted to one instance thats independent from the rest of the functionality then it doesnt need it. Generic algorithms should get extracted if they take up a lot of space or are complex to understand.
- **Migrations: `schema.sql` for fresh DBs, `db.py` for existing ones.** `schema.sql` (all `CREATE … IF NOT EXISTS`) builds a fresh DB; `db.py`'s init runs guarded, idempotent upgrades for existing DBs (relax a NOT NULL via table rebuild, add columns, dedup rows, add a partial unique index). Any index/constraint that references a not-yet-migrated column is created in `db.py` *after* the upgrade — never in `schema.sql` — so `executescript` can't trip on an un-upgraded table.
- **Card images fetched on demand.** `/api/images/<id>` downloads a missing image from TCGplayer on first request (no reload; nothing pre-fetched in the read-only reporting path), via one `save_webp_from_url` primitive that the bulk backfill (`scripts/fetch_images.py`) and the graded slab-image download also build on.

Capability-specific decisions live in that capability's local doc — e.g. the seller pipeline's `/next` trigger, one-photo-at-a-time, and per-card context reset are in `dashboard/backend/orchestrator/PIPELINE.md`.

## card-server Structure

Three service files are public: the orchestrator calls `collection_service` + `pricing_service`; the dashboard calls `reporting_service`. Everything else is internal.

```
services/card_server/         (imported as `services.card_server`)
├── data/cards.db             <- SQLite database (gitignored)
├── index.py                  <- MCP entry in future
├── db.py                     <- SQLite init/close
├── schema.sql                <- core chain (cards, skus, inventory, prices) + 3 market tables
├── types.py                  <- Pydantic models + type aliases
├── services/
│   ├── collection_service.py <- Cards + SKUs + Inventory management
│   ├── pricing_service.py    <- Pricing operations + TCGplayer fetch workflows
│   └── reporting_service.py  <- Read-only browse + analytics for the dashboard (no writes)
├── helpers/                  <- INTERNAL (services compose these)
│   ├── crud/                 <- Pure DB CRUD per table (cards, skus, inventory, prices)
│   ├── pricing/              <- algorithm + inputs + config + repricer (see docs/pricing-algorithm.md)
│   ├── tcgplayer/            <- API fetchers, formatters, shared rate-limited transport (see docs/tcgplayer-api.md)
│   └── market/               <- snapshot collection (pricing) + raw sales & price-history stores (graph)
├── collect.py                <- CLI: market snapshots (pricing)
├── collect_sales.py          <- CLI: sales + market-price history (graph; batch / `--loop` / `--crawl`)
└── migrate.py, dedup_inventory.py  <- historical migration + one-off utility
```

## Database Schema

Core chain: `cards` (TCGplayer product metadata) -> `skus` (condition+finish variants, composite UNIQUE) -> `inventory` (physical cards owned) -> `prices` (historical estimates per SKU per date, each carrying the algorithm's `reasoning`). Plus three market-data tables keyed by card+condition+finish+`specialty_one` (1st Edition and Unlimited are separate printings at very different prices, so none of them may pool the two): `market_snapshots` (per-variant aggregate stats written by `collect`; the pricing run's stored input as well as the historical/analytics record), `sales` (raw individual sold listings, ~1yr history), and `market_price_history` (TCGplayer weekly "market price" from the Infinite API). The latter two are written only by `collect_sales` and feed the per-card sales graph; the pricing path never reads either.

**Key decisions:**
- `inventory.pricing_sku_id` allows pricing a borderline card against a different condition (e.g., LP-NM priced as NM)
- `inventory.tags` is comma-separated (not normalized) for variable card-specific details (hidden creases, marks)
- `specialty_one` = TCGplayer-level variants (1st Edition) with their own product IDs. `specialty_two` = non-TCGplayer **error** attributes (miscuts, holo bleeds, …) that trigger manual review. (Graded cards used to ride on `specialty_two` but now live in their own parallel tables

## Pricing

**Goal:** maximum profit with variable aggressiveness. The primary anchor is the **lowest active TCGplayer listing** (best proxy for liquid modern cards); recent solds are a secondary signal that takes over for illiquid cards. TCGplayer's own "market price" is **not** trusted for pricing.

**Collection and pricing are separate steps.** `collect` writes each variant's market state to `market_snapshots`, then the pricer reads it back and writes `prices` — it never prices from the fetch directly. So `collect.bat --reprice-only` regenerates prices from stored data with zero requests, which is how a changed constant gets applied across the collection.

**Major caveats:**
- **Cheap cards (< $5)** use listing prices only — sold prices are shipping-noise.
- **When the listing disagrees with the sold signal by >30%**, the price is a weighted blend of the two rather than the listing alone; the sold side's share scales with the divergence (30–70%), and within it each sale decays by a half-life on its age. The listing always keeps at least 30%.
- **Conditions TCGplayer doesn't sell** (in-between grades like `MP-LP`, plus the `MINT`/`DM` aliases) are derived from the tiers it does. The collector fetches an in-between grade's *both* neighbors in the same visit and interpolates between them; falling back to one-sided **extrapolation** is always flagged for manual review. Error variants (`specialty_two`) have no tier either — they inherit the plain card's price and are always flagged.

All constants live in `services/card_server/helpers/pricing/config.py`; the full algorithm (decision tree, confidence, liquid value) is in `docs/pricing-algorithm.md`.

## Tech Stack

Python (3.11+). The majority of the codebase is in Python except for the frontend which is React.

Python | SQLite via stdlib `sqlite3` | `python-telegram-bot` | Pydantic | `httpx` | `python-dotenv`

**No packaging / no install.** Dependencies are in `requirements.txt` (`pip install -r requirements.txt`). Imports resolve via the repo root being on `sys.path` — always run modules from the repo root with `python -m <dotted.path>`. Import paths mirror the folder layout: `services.card_server.*`, `services.telegram.*`, `services.ebay.*`, `dashboard.backend.orchestrator.*`. (Folders use underscores, not hyphens, so they're valid module names.)

Dashboard: React + Vite + TypeScript (frontend) | Flask (backend)

## Environment Variables

All variables live in a **single `.env` at the repo root** (see `.env.example`). The backend reads it via `python-dotenv`; the Vite frontend reads the same file via `envDir` and only exposes `API_`/`VITE_`-prefixed vars to the client bundle, so backend secrets stay server-side.

- **Required:** `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
- **Optional:** `EBAY_*`, `TCGPLAYER_AUTH_COOKIE` (enables paginated sales history), `DB_PATH`, `PHOTOS_DIR`.
- **Frontend `API_BASE`:** backend origin the dashboard calls (no `/api` suffix, no trailing slash). Non-secret (baked into the bundle); defaults to `http://localhost:5000`. For deploys, set it in the host's build env (Vercel project settings) — the Azure pipeline builds/deploys the frontend via the Vercel CLI.

## Commands

```bash
# One-time setup
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # (Windows path)

# Easiest: run the whole dashboard
run.bat            # Windows
./run.sh           # bash / Git-Bash

# Or run pieces manually (always from the repo root so imports resolve):
python -m dashboard.backend.orchestrator.main   # the full app (entry point; needs .env)
python -m services.card_server.collect_sales     # comprehensive raw sales-history collection (graph data; separate from pricing)
python dashboard/backend/app.py                 # Flask API on :5000
cd dashboard/frontend && npm run dev            # Vite dev server on :5173

pytest -q                                       # run tests
```

## Git Workflow

**Never commit directly to `main`.** The Azure pipeline runs tests on PRs into `main` and tests + deploy (Vercel, via the CLI) on pushes to `main`. Work on a feature branch per task (GitHub Flow — no long-lived `develop`).

- Branch per unit of work off up-to-date `main`: `git switch -c <type>/<short-name>` (`feat/`, `fix/`, `chore/`, `docs/`, …). One branch per feature or side task.
- **Auto-commit and push to the feature branch after every significant change** — don't wait to be asked. Descriptive messages; don't batch unrelated changes into one commit.
- **Only the repo owner opens and merges PRs.** When work is complete, push the branch and stop — do **not** open the PR, and never merge to `main` (no `gh pr merge`, no fast-forward, no direct push to `main`). Resolve merge conflicts as they arise.

## Testing

Tests live in `tests/` (pytest). Run with `pytest -q` from the repo root. Live external-API contract tests are marked `external` and excluded by default; run them with `pytest -m external` (they need real creds, e.g. `TCGPLAYER_AUTH_COOKIE`).

**Maintain the tests as the code changes** — when you add or change behavior (pricing logic, tier routing, formatters, listing/render output, service methods), update or add the corresponding test in the same change so `pytest` stays green. Don't land logic changes without adjusting the tests they affect.
