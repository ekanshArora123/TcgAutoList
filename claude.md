# TcgAutoList — Agentic Pokemon Card eBay Listing System

## Project Overview

Automates listing ~10,000 Pokemon cards on eBay using an agentic architecture with MCP servers and a Telegram bot for photo intake.

**Workflow:** `/next` via Telegram -> pick next unlisted card -> fetch price -> route by tier -> request photo -> build listing -> post to eBay -> mark done -> wait for next command.

## Architecture

See `STRUCTURE.md` for full folder layout.

| Component | Location | Purpose |
|-----------|----------|---------|
| `card-server` | `services/card_server/` | Data microservice. SQLite DB + CRUD + TCGplayer fetching + pricing algorithm. |
| `telegram` | `services/telegram/` | Transport layer. Routes commands/photos/text between user and orchestrator. |
| `ebay` | `services/ebay/` | eBay listing service [stub]. |
| `orchestrator` | `dashboard/backend/orchestrator/` | Coded event loop. Picks card, fetches price, routes by tier, drives workflow. |
| `dashboard` | `dashboard/` | Web UI (React frontend + Flask backend). |
| `entry point` | `dashboard/backend/orchestrator/main.py` | Wires services + orchestrator, starts app. |

### Tiered Escalation Model

LLM involvement scales with pricing difficulty. Most cards go through a dumb coded pipeline.

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
| MySQL -> SQLite migration | Done | `services/card_server/migrate.py` |
| Telegram bot | Done | Commands, photos, inline keyboards |
| Tier routing | Done | Confidence-based, configurable thresholds |
| Tier 1 pipeline | Done | Template listing builder, photo request flow |
| Orchestrator (Tier 1) | Done | Full state machine for the dumb pipe |
| **Python conversion** | **Done** | Full TS→Python port. React frontend stays TS. See `CONVERSION_PLAN.md` |
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

The orchestrator calls **two service files**. Everything else is internal.

```
services/card_server/         (imported as `services.card_server`)
├── data/cards.db             <- SQLite database (gitignored)
├── index.py                  <- MCP entry (FastMCP): 30+ tools, delegates to services
├── db.py                     <- SQLite init/close
├── schema.sql                <- 4-table schema (cards, skus, inventory, prices)
├── types.py                  <- Pydantic models + type aliases
├── services/
│   ├── collection_service.py <- Cards + SKUs + Inventory management
│   └── pricing_service.py    <- Pricing operations + TCGplayer fetch workflows
├── helpers/                  <- INTERNAL (services compose these)
│   ├── crud/                 <- Pure DB CRUD (cards, skus, inventory, prices)
│   ├── pricing/
│   │   ├── algorithm.py      <- Pricing algorithm (see docs/pricing-algorithm.md)
│   │   └── config.py         <- All pricing magic numbers
│   ├── tcgplayer/
│   │   ├── fetch_prices.py   <- Active + sold listings (see docs/tcgplayer-api.md)
│   │   ├── fetch_card_info.py<- Card metadata fetching
│   │   └── formatters.py     <- Condition/finish format conversion
│   └── market/
│       ├── collector.py      <- Market data collection orchestration
│       ├── fetchers.py       <- External API calls
│       ├── aggregators.py    <- Raw data → aggregate stats
│       └── snapshots.py      <- DB read/write for market_snapshots
├── collect.py                <- CLI runner for market data collection
├── dedup_inventory.py        <- One-off utility script
└── migrate.py                <- MySQL -> SQLite migration (historical)
```

## Database Schema

4 tables in SQLite. `cards` (TCGplayer product metadata) -> `skus` (condition+finish variants, composite UNIQUE) -> `inventory` (physical cards owned) -> `prices` (historical estimates per SKU per date).

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

Python (3.11+). The TypeScript codebase was fully converted to Python (see `CONVERSION_PLAN.md` for the historical mapping). The React frontend remains TypeScript.

Python | SQLite via stdlib `sqlite3` | `mcp` (FastMCP) | `python-telegram-bot` | Pydantic | `httpx` | `python-dotenv`

**No packaging / no install.** Dependencies are in `requirements.txt` (`pip install -r requirements.txt`). Imports resolve via the repo root being on `sys.path` — always run modules from the repo root with `python -m <dotted.path>`. Import paths mirror the folder layout: `services.card_server.*`, `services.telegram.*`, `services.ebay.*`, `dashboard.backend.orchestrator.*`. (Folders use underscores, not hyphens, so they're valid module names.)

Dashboard: React + Vite + TypeScript (frontend) | Flask (backend)

## Environment Variables

See `.env.example`. Required: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. Optional: `EBAY_*`, `TCGPLAYER_AUTH_COOKIE`, `DB_PATH`, `PHOTOS_DIR`.

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
python -m services.card_server.collect          # market data collection runner
python dashboard/backend/app.py                 # Flask API on :5000
cd dashboard/frontend && npm run dev            # Vite dev server on :5173

pytest -q                                       # run tests
```

## Git Workflow

**Auto-commit and push after every significant code change.** Don't wait for the user to ask. After completing a meaningful unit of work (feature, refactor, bug fix), stage relevant files, write a descriptive commit message, and push to main. Don't batch unrelated changes into one commit.

## Common Tasks for AI Assistants

- **Adding an MCP tool:** Pydantic/type in `services/card_server/types.py` -> handler method in `collection_service.py` or `pricing_service.py` -> register as an `@mcp.tool()` in `services/card_server/index.py`.
- **Changing the pricing algorithm:** Edit `services/card_server/helpers/pricing/algorithm.py`. Constants are in `config.py`.
- **Adding a TCGplayer data source:** Fetch function in `services/card_server/helpers/tcgplayer/` -> format conversions in `formatters.py` -> wire into service.
- **Changing condition/finish mappings:** Edit `formatters.py`.

## Testing

Tests live in `tests/` (pytest). Run with `pytest -q` from the repo root.

**Maintain the tests as the code changes** — when you add or change behavior (pricing logic, tier routing, formatters, listing/render output, service methods), update or add the corresponding test in the same change so `pytest` stays green. Don't land logic changes without adjusting the tests they affect.
