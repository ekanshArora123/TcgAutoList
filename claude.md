# TcgAutoList — Agentic Pokemon Card eBay Listing System

## Project Overview

Automates listing ~10,000 Pokemon cards on eBay using an agentic architecture with MCP servers and a Telegram bot for photo intake.

**Workflow:** `/next` via Telegram -> pick next unlisted card -> fetch price -> route by tier -> request photo -> build listing -> post to eBay -> mark done -> wait for next command.

## Architecture

See `STRUCTURE.md` for full folder layout.

| Component | Location | Purpose |
|-----------|----------|---------|
| `card-server` | `services/card-server/` | Data microservice. SQLite DB + CRUD + TCGplayer fetching + pricing algorithm. |
| `telegram` | `services/telegram/` | Transport layer. Routes commands/photos/text between user and orchestrator. |
| `ebay` | `services/ebay/` | eBay listing service [stub]. |
| `orchestrator` | `dashboard/backend/orchestrator/` | Coded event loop. Picks card, fetches price, routes by tier, drives workflow. |
| `dashboard` | `dashboard/` | Web UI (React frontend + Flask backend). |
| `entry point` | `src/index.ts` | Wires services + orchestrator, starts app. |

### Tiered Escalation Model

LLM involvement scales with pricing difficulty. Most cards go through a dumb coded pipeline.

| Tier | Criteria | LLM? | Tokens | % of Cards |
|------|----------|------|--------|------------|
| **1** | Confidence >= 80%, price < $50, no specialties | No | 0 | ~80% |
| **2** | Confidence 40-79%, OR $50-200, OR manual flag | Scoped context + pricing tools | ~5K | ~15% |
| **3** | Confidence < 40%, OR > $200, OR graded/errors, OR no data | Full context + all tools | ~12K | ~5% |

Estimated cost for 10,000 cards: **~9.75M tokens**

Tier thresholds are configurable in `src/types.ts` (`DEFAULT_TIER_CONFIG`). Pricing magic numbers are in `services/card-server/src/helpers/pricing/pricingConfig.ts`.

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
| MySQL -> SQLite migration | Done | `services/card-server/src/migrate.ts` |
| Telegram bot | Done | Commands, photos, inline keyboards, dedup |
| Tier routing | Done | Confidence-based, configurable thresholds |
| Tier 1 pipeline | Done | Template listing builder, photo request flow |
| Orchestrator (Tier 1) | Done | Full state machine for the dumb pipe |
| **eBay posting** | **Stub** | `services/ebay/index.ts` — returns stub ID |
| **Price override** | **Stub** | Orchestrator detects numeric input but doesn't execute |
| **Tier 2/3 LLM** | **Stub** | `llm.ts`, `tools.ts`, `systemPrompt.ts` are pseudocode |
| **Python conversion** | **Planned** | See `CONVERSION_PLAN.md` |

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
services/card-server/src/
├── index.ts                  <- MCP entry: 30+ tools, delegates to services
├── db.ts                     <- SQLite init/close
├── schema.sql                <- 4-table schema (cards, skus, inventory, prices)
├── types.ts                  <- Zod schemas + TS types
├── services/
│   ├── collectionService.ts  <- Cards + SKUs + Inventory management
│   └── pricingService.ts     <- Pricing operations + TCGplayer fetch workflows
├── helpers/                  <- INTERNAL (services compose these)
│   ├── crud/                 <- Pure DB CRUD (cards, skus, inventory, prices)
│   ├── pricing/
│   │   ├── algorithm.ts      <- Pricing algorithm (see docs/pricing-algorithm.md)
│   │   └── pricingConfig.ts  <- All pricing magic numbers
│   ├── tcgplayer/
│   │   ├── fetchPrices.ts    <- Active + sold listings (see docs/tcgplayer-api.md)
│   │   ├── fetchCardInfo.ts  <- Card metadata fetching
│   │   └── formatters.ts     <- Condition/finish format conversion
│   └── market/
│       ├── collector.ts      <- Market data collection orchestration
│       ├── fetchers.ts       <- External API calls
│       ├── aggregators.ts    <- Raw data → aggregate stats
│       └── snapshots.ts      <- DB read/write for market_snapshots
├── collect.ts                <- CLI runner for market data collection
├── dedup-inventory.ts        <- One-off utility script
└── migrate.ts                <- MySQL -> SQLite migration (historical)
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

All pricing constants live in `services/card-server/src/helpers/pricing/pricingConfig.ts`.

## Tech Stack

Currently TypeScript (converting to Python). See `CONVERSION_PLAN.md`.

Node.js + TypeScript (ES2022, ESM) | SQLite via `better-sqlite3` | `@modelcontextprotocol/sdk` | `node-telegram-bot-api` | Zod | dotenv

Dashboard: React + Vite (frontend) | Flask (backend)

## Environment Variables

See `.env.example`. Required: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. Optional: `EBAY_*`, `TCGPLAYER_AUTH_COOKIE`, `DB_PATH`, `PHOTOS_DIR`.

## Commands

```bash
npm run build && npm run start    # Parent project
cd services/card-server && npm run build   # card-server (standalone)
```

## Git Workflow

**Auto-commit and push after every significant code change.** Don't wait for the user to ask. After completing a meaningful unit of work (feature, refactor, bug fix), stage relevant files, write a descriptive commit message, and push to main. Don't batch unrelated changes into one commit.

## Common Tasks for AI Assistants

- **Adding an MCP tool:** Zod schema in `services/card-server/src/types.ts` -> handler in `collectionService.ts` or `pricingService.ts` -> register in `services/card-server/src/index.ts`.
- **Changing the pricing algorithm:** Edit `services/card-server/src/helpers/pricing/algorithm.ts`. Constants are in `pricingConfig.ts`.
- **Adding a TCGplayer data source:** Fetch function in `services/card-server/src/helpers/tcgplayer/` -> format conversions in `formatters.ts` -> wire into service.
- **Changing condition/finish mappings:** Edit `formatters.ts`.
