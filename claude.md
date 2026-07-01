# TcgAutoList — TCG Automation Platform

## Project Overview

TcgAutoList is a growing platform of **interconnected capabilities** that assist with all sorts of trading-card-game (TCG) problems. The pieces are not isolated projects — they work together like organs. A card-data store, TCGplayer scrapers, a pricing engine, market/sales data collectors, an analytics dashboard, and a seller listing pipeline each build on and reuse the others.

There is no fixed end product. The platform accretes capabilities over time toward the open-ended goal of assisting with TCG problems. It is built primarily for one person's own needs today (managing and selling a ~10,000 card Pokemon collection), with a longer-term ambition to deploy it so other buyers and sellers can use it too.

**Core tenets:**
- **Reuse first.** Before writing new code, make use of existing functionality. This is the whole point — capabilities compound instead of duplicating each other.
- **Modular and generally usable.** Design components to be reused and composed, with clear boundaries and a shared substrate — not one-offs.
- **Pokemon now, doors open.** Pokemon + TCGplayer is the current domain. Don't over-engineer for other games, but avoid hardcoding assumptions that would needlessly block other TCGs later.

**Working model:** there's no fixed rule for what comes next — each session either **improves an existing capability** (e.g. make pricing more robust or agentic, harden a scraper) or **adds a new one** (e.g. vendor live-sales tracking), whichever is most valuable at the time. New work should lean on what already exists.

## Capabilities

The interconnected capabilities in the repo today. They reuse each other — the **Builds on** column shows the composition. New capabilities should extend this web, not duplicate it.

| Capability | Lives in | What it does | Builds on |
|------------|----------|--------------|-----------|
| **Card-data store + MCP server** | `services/card_server/` (`db.py`, `schema.sql`, `index.py`) | SQLite substrate (cards/skus/inventory/prices + market tables) exposed as CRUD services and 30+ FastMCP tools. | The shared foundation — everything else builds on it. |
| **TCGplayer scrapers/fetchers** | `helpers/tcgplayer/` | Active/sold listings, card metadata, condition/finish formatters, shared HTTP transport. See `docs/tcgplayer-api.md`. | TCGplayer APIs. |
| **Pricing engine** | `helpers/pricing/` via `pricing_service.py` | Lowest-listing anchor + sold sanity checks, confidence scoring, cross-condition extrapolation, liquid value. | Card-data store, scrapers, market-data. |
| **Market-data collector** | `helpers/market/` + `collect.py` | Periodic aggregate snapshots → `market_snapshots` (feeds pricing). Idempotent per date. | Card-data store, scrapers. |
| **Sales-history + market-price collector & graph** | `sales_collector.py`, `sales_store.py`, `price_history_store.py`, `collect_sales.py` | ~1yr of raw sold listings + weekly TCGplayer market price for the per-card graph. Kept out of the pricing path. | Card-data store, scrapers/transport. |
| **Analytics dashboard** | `dashboard/frontend/` + `dashboard/backend/app.py` → `reporting_service.py` | Browse/filter/search the collection, charts, per-card detail + sales graph. | Card-data store (reporting), sales/market data. |
| **Seller listing pipeline** | `services/telegram/` + `dashboard/backend/orchestrator/` + `services/ebay/` | Turns owned inventory into live listings: `/next` → pick unlisted card → fetch price → route by tier → request photo → build listing → post → mark done. | Card-data store, pricing, Telegram, eBay. |

**Stubs / future.** eBay posting (`services/ebay/service.py`) and Tier 2/3 LLM pricing (`orchestrator/llm.py`, `tools.py`, `system_prompt.py`) are stubs. Future capabilities are open-ended and build on the same substrate — e.g. vendor live sales tracking, and a **sell/hold decision engine** over longitudinal price history (the raw `sales` + `market_price_history` tables are the data plane already being collected for it).

## Architecture

See `docs/STRUCTURE.md` for full folder layout.

| Component | Location | Purpose |
|-----------|----------|---------|
| `card-server` | `services/card_server/` | Data microservice. SQLite DB + CRUD + TCGplayer fetching + pricing algorithm. |
| `telegram` | `services/telegram/` | Transport layer. Routes commands/photos/text between user and orchestrator. |
| `ebay` | `services/ebay/` | eBay listing service [stub]. |
| `orchestrator` | `dashboard/backend/orchestrator/` | Coded event loop. Picks card, fetches price, routes by tier, drives workflow. |
| `dashboard` | `dashboard/` | Web UI (React frontend + Flask backend). |
| `entry point` | `dashboard/backend/orchestrator/main.py` | Wires services + orchestrator, starts app. |

### Tiered Escalation Model

This governs the pricing engine and seller listing pipeline. LLM involvement scales with pricing difficulty. Most cards go through a dumb coded pipeline.

| Tier | Criteria | LLM? | Tokens | % of Cards |
|------|----------|------|--------|------------|
| **1** | Confidence >= 80%, price < $50, no specialties | No | 0 | ~80% |
| **2** | Confidence 40-79%, OR $50-200, OR manual flag | Scoped context + pricing tools | ~5K | ~15% |
| **3** | Confidence < 40%, OR > $200, OR graded/errors, OR no data | Full context + all tools | ~12K | ~5% |

Estimated cost for 10,000 cards: **~9.75M tokens**

Tier thresholds are configurable in `dashboard/backend/orchestrator/types.py` (`DEFAULT_TIER_CONFIG`). Pricing magic numbers are in `services/card_server/helpers/pricing/config.py`.

### Key Design Decisions

- **Direct imports for Tier 1, MCP for Tier 2/3.** Orchestrator imports card-server services directly for the dumb pipeline. MCP STDIO only needed when LLM conversations need tool access.
- **Context resets per card.** Each card gets a fresh LLM conversation (Tier 2/3). No history accumulation. Token costs stay predictable.
- **Manual trigger only.** `/next` starts each card. No auto-advance.
- **One photo at a time.** Request photo, wait, continue.
- **All durable state in SQLite.** Program can stop/restart without losing progress. DB tracks card status (e.g., `photo_requested`).
- **Idempotent.** Re-running skips already-listed cards.
- **Services as microservices.** card-server, telegram, and ebay each own one external system. Don't duplicate owned functionality.

## Implementation Status

| Feature | Status | Notes |
|---------|--------|-------|
| card-server (DB, CRUD, services, MCP) | Done | 30+ MCP tools, all 4 table helpers |
| TCGplayer API (listings, solds, card info) | Done | See `docs/tcgplayer-api.md` for API reference |
| Pricing algorithm v1 | Done | See `docs/pricing-algorithm.md` for full details |
| Market-data collector (pricing snapshots) | Done | `helpers/market/` + `collect.py` → `market_snapshots` |
| Sales-history + market-price collection & per-card graph | Done | Robust: adaptive rate limiting + `--crawl` multi-day backfill. Graph data only, not pricing. |
| Analytics dashboard (browse + charts + card detail) | Done | Read-only via `reporting_service.py` |
| MySQL -> SQLite migration | Done | `services/card_server/migrate.py` |
| Telegram bot | Done | Commands, photos, inline keyboards |
| Tier routing | Done | Confidence-based, configurable thresholds |
| Tier 1 pipeline | Done | Template listing builder, photo request flow |
| Orchestrator (Tier 1) | Done | Full state machine for the dumb pipe |
| **Python conversion** | **Done** | Full TS→Python port. React frontend stays TS. |
| **eBay posting** | **Stub** | `services/ebay/service.py` — returns stub ID |
| **Price override** | **Stub** | Orchestrator detects numeric input but doesn't execute |
| **Tier 2/3 LLM** | **Stub** | `llm.py`, `tools.py`, `system_prompt.py` are pseudocode |

### Future Work

- **Pricing v2:** Sales velocity (sales per 48hr window) to price above lowest listing for fast-selling cards.
- **Pricing v3 (LLM sampling):** TCGplayer + eBay data as separate tools the LLM reasons over.
- **Tier 2.5:** Sell/hold sentiment analysis using price history and market trends.
- **In-between condition listing choice:** When a card is LP-NM, prompt user to choose LP or NM for the listing.
- **Bulk re-pricing:** Re-fetch prices for cards with stale data (>14 days old).
- **Dashboard as control plane:** Trigger workflows, override prices, manage listings from web UI.

## card-server Structure

Three service files are public: the orchestrator calls `collection_service` + `pricing_service`; the dashboard calls `reporting_service`. Everything else is internal.

```
services/card_server/         (imported as `services.card_server`)
├── data/cards.db             <- SQLite database (gitignored)
├── index.py                  <- MCP entry (FastMCP): 30+ tools, delegates to services
├── db.py                     <- SQLite init/close
├── schema.sql                <- 4-table schema (cards, skus, inventory, prices)
├── types.py                  <- Pydantic models + type aliases
├── services/
│   ├── collection_service.py <- Cards + SKUs + Inventory management
│   ├── pricing_service.py    <- Pricing operations + TCGplayer fetch workflows
│   └── reporting_service.py  <- Read-only browse + analytics for the dashboard (no writes)
├── helpers/                  <- INTERNAL (services compose these)
│   ├── crud/                 <- Pure DB CRUD (cards, skus, inventory, prices)
│   ├── pricing/
│   │   ├── algorithm.py      <- Pricing algorithm (see docs/pricing-algorithm.md)
│   │   └── config.py         <- All pricing magic numbers
│   ├── tcgplayer/
│   │   ├── fetch_prices.py   <- Active + sold listings (see docs/tcgplayer-api.md)
│   │   ├── fetch_card_info.py<- Card metadata fetching
│   │   ├── fetch_sales_history.py <- Raw sold-listings history (~1yr, paginated; graph data)
│   │   ├── fetch_price_history.py <- Weekly TCGplayer market-price history (infinite-api; graph data)
│   │   ├── transport.py      <- Shared async HTTP client + adaptive rate limiter (AIMD backoff)
│   │   └── formatters.py     <- Condition/finish format conversion
│   └── market/
│       ├── collector.py      <- Market data collection orchestration (pricing snapshots)
│       ├── fetchers.py       <- External API calls
│       ├── aggregators.py    <- Raw data → aggregate stats
│       ├── snapshots.py      <- DB read/write for market_snapshots
│       ├── sales_collector.py<- Graph-data gathering: raw sales + market-price history (NOT pricing)
│       ├── sales_store.py    <- DB read/write for the raw `sales` table
│       └── price_history_store.py <- DB read/write for `market_price_history`
├── collect.py                <- CLI runner for market data collection (pricing)
├── collect_sales.py          <- CLI runner for graph data: sales + market-price history (batch / `--loop` / `--crawl` multi-day backfill)
├── dedup_inventory.py        <- One-off utility script
└── migrate.py                <- MySQL -> SQLite migration (historical)
```

## Database Schema

Core chain: `cards` (TCGplayer product metadata) -> `skus` (condition+finish variants, composite UNIQUE) -> `inventory` (physical cards owned) -> `prices` (historical estimates per SKU per date). Plus three market-data tables keyed by card+condition+finish: `market_snapshots` (periodic aggregate stats, feeds pricing), `sales` (raw individual sold listings, ~1yr history), and `market_price_history` (TCGplayer weekly "market price" from the Infinite API). The latter two feed the per-card sales graph only — never the pricing algorithm; both are gathered by `collect_sales`.

**Key decisions:**
- `inventory.pricing_sku_id` allows pricing a borderline card against a different condition (e.g., LP-NM priced as NM)
- `inventory.tags` is comma-separated (not normalized) for variable card-specific details (hidden creases, marks)
- `specialty_one` = TCGplayer-level variants (1st Edition) with their own product IDs. `specialty_two` = non-TCGplayer variants (graded, errors) that trigger manual review.

## Pricing Philosophy

**Goal:** Maximum profit with variable aggressiveness.

- **Primary anchor:** Lowest active TCGplayer listing. Best proxy for liquid NM modern cards.
- **Secondary:** Average of recent solds. Takes precedence for illiquid cards.
- **TCGplayer "market price" is NOT used.** It is unreliable.
- **Cheap cards (< $5):** Listing prices only. Sold prices are noisy due to inconsistent shipping ($0 bundled vs $1 single-card). See `docs/pricing-algorithm.md` for details.
- **Cross-condition extrapolation:** 30% discount per tier, compounding. Always flagged for manual review.
- **Liquid value:** `(sell_price * 0.85) - shipping_cost`. Shipping: <$5 = $0, $5-25 = $1 (PWE), >$25 = $5 (tracked).

All pricing constants live in `services/card_server/helpers/pricing/config.py`.

## Tech Stack

Python (3.11+). The TypeScript codebase was fully converted to Python. The React frontend remains TypeScript.

Python | SQLite via stdlib `sqlite3` | `mcp` (FastMCP) | `python-telegram-bot` | Pydantic | `httpx` | `python-dotenv`

**No packaging / no install.** Dependencies are in `requirements.txt` (`pip install -r requirements.txt`). Imports resolve via the repo root being on `sys.path` — always run modules from the repo root with `python -m <dotted.path>`. Import paths mirror the folder layout: `services.card_server.*`, `services.telegram.*`, `services.ebay.*`, `dashboard.backend.orchestrator.*`. (Folders use underscores, not hyphens, so they're valid module names.)

Dashboard: React + Vite + TypeScript (frontend) | Flask (backend)

## Environment Variables

**Backend** (`.env` at repo root, see `.env.example`): Required `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. Optional `EBAY_*`, `TCGPLAYER_AUTH_COOKIE`, `DB_PATH`, `PHOTOS_DIR`.

**Frontend** (`dashboard/frontend/.env`, see `dashboard/frontend/.env.example`): `API_BASE` — backend origin the dashboard calls (no `/api` suffix, no trailing slash), e.g. `http://localhost:5000` locally or the deployed backend URL in prod. It's a Vite build-time var (baked into the bundle), exposed via the `API_` env prefix in `vite.config.ts`; set it in the host's build env (e.g. Vercel) for deploys. Defaults to `http://localhost:5000` if unset.

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
python -m services.card_server.index            # card-server MCP server (stdio)
python -m services.card_server.collect          # market data collection runner (pricing snapshots)
python -m services.card_server.collect_sales     # comprehensive raw sales-history collection (graph data; separate from pricing)
python dashboard/backend/app.py                 # Flask API on :5000
cd dashboard/frontend && npm run dev            # Vite dev server on :5173

pytest -q                                       # run tests
```

## Git Workflow

**Never commit directly to `main`.** Azure DevOps CI triggers on `main` (tests + Deploy) and on PRs into `main` (tests only), so direct commits to main fire the pipeline and can deploy. Work on a feature branch per feature/task instead.

- Start each unit of work on a new branch off up-to-date `main`: `git switch -c <type>/<short-name>` (`feat/`, `fix/`, `chore/`, …). One branch per unique feature or side task.
- **Auto-commit and push to the feature branch after every significant change** — don't wait to be asked. Write descriptive commit messages; don't batch unrelated changes into one commit.
- When the work is complete, open a PR into `main` (the PR run executes the test suite) and **stop there — do NOT merge**. The user reviews and merges the PR once tests pass. Never merge to main (no `gh pr merge`, no fast-forward, no direct push). Resolve merge conflicts as they arise.
- Branch model is GitHub Flow — no long-lived `develop` branch. Deploy runs only on pushes/merges to `main`; the live TCGplayer canary runs on schedule/manual.

## Common Tasks for AI Assistants

- **Adding an MCP tool:** Pydantic/type in `services/card_server/types.py` -> handler method in `collection_service.py` or `pricing_service.py` -> register as an `@mcp.tool()` in `services/card_server/index.py`.
- **Changing the pricing algorithm:** Edit `services/card_server/helpers/pricing/algorithm.py`. Constants are in `config.py`.
- **Adding a TCGplayer data source:** Fetch function in `services/card_server/helpers/tcgplayer/` -> format conversions in `formatters.py` -> wire into service.
- **Changing condition/finish mappings:** Edit `formatters.py`.

## Testing

Tests live in `tests/` (pytest). Run with `pytest -q` from the repo root.

**Maintain the tests as the code changes** — when you add or change behavior (pricing logic, tier routing, formatters, listing/render output, service methods), update or add the corresponding test in the same change so `pytest` stays green. Don't land logic changes without adjusting the tests they affect.
