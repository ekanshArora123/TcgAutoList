# src/ Architecture — Current State & Restructuring Plan

## Current State

`src/` contains three distinct concerns mixed together:

```
src/
├── index.ts                  <- Entry point: wires everything together
├── types.ts                  <- Shared types (re-exports card-server types)
├── telegram/
│   ├── bot.ts                <- Telegram transport layer
│   └── renderer.ts           <- Message formatting for Telegram
├── agent/
│   ├── orchestrator.ts       <- State machine driving the workflow
│   ├── tierRouter.ts         <- Routes cards to Tier 1/2/3
│   ├── tier1Pipeline.ts      <- Template listing builder (no LLM)
│   ├── llm.ts                <- [STUB] Claude API client for Tier 2/3
│   ├── tools.ts              <- [STUB] Tool schemas for LLM
│   └── systemPrompt.ts       <- [STUB] Dynamic prompts for LLM
└── __tests__/                <- Tests for the above
```

## Target Structure (Option A — Separate Services)

```
project/
├── services/
│   ├── card-server/       <- Data + pricing service (exists, move here)
│   ├── telegram/          <- Telegram I/O service (extract from src/)
│   └── ebay/              <- eBay I/O service (new, currently stub)
├── dashboard/             <- Web UI (exists, stays at top level)
├── orchestrator/          <- Workflow engine (extract from src/agent/)
│   ├── orchestrator.ts    <- State machine
│   ├── tierRouter.ts      <- Tier routing logic
│   ├── tier1Pipeline.ts   <- Template listing builder
│   ├── llm.ts             <- LLM client for Tier 2/3
│   ├── tools.ts           <- Tool schemas for LLM
│   └── systemPrompt.ts    <- Dynamic prompts
└── src/
    ├── index.ts           <- Entry point: wires services + orchestrator
    └── types.ts           <- Shared types
```

### Hierarchy

**Top level (peer to each other):**
- `dashboard/` — User-facing web control plane
- `orchestrator/` — Automated workflow engine
- `src/` — Entry point / wiring

**Services (inside `services/`, peer to each other):**
- `card-server/` — Owns: SQLite DB, pricing algorithm, TCGplayer API, market data
- `telegram/` — Owns: Telegram bot API, message sending/receiving, photo handling
- `ebay/` — Owns: eBay Sell API, listing CRUD, category mapping, photo upload

Dashboard and orchestrator both consume the services. They sit at the same level because they're both "consumers" — one driven by a web UI, the other driven by automated workflows.

### Service Boundary Principle

Each service owns an external system and provides abstraction over it. Callers should not need to know the service's internal API format, auth mechanism, or data model.

**card-server** already follows this well (CollectionService/PricingService hide DB schema and TCGplayer API details).

**telegram** should follow the same pattern — expose `sendMessage()`, `requestPhoto()`, `onCommand()` etc. without leaking `node-telegram-bot-api` internals. (Current `bot.ts` already does this reasonably well.)

**ebay** should expose `createListing()`, `updateListing()`, `getListing()` etc. without leaking the eBay Sell API's 325-tool complexity.

### Future: card-server internal separation

card-server currently bundles two distinct concerns:
1. **Database access** — CRUD operations, schema, queries
2. **TCGplayer/pricing** — External API calls, pricing algorithm

These could eventually be separated into `db-server` and `pricing-server` (or similar), with Telegram and eBay as additional peer services. For now, keeping them together in card-server is fine — the internal `helpers/` structure already separates them cleanly.

## What the Orchestrator Does

### Current (Tier 1 only)

A state machine driven by Telegram `/next` commands:

1. `/next` → picks next unlisted card from DB (via card-server)
2. Fetches/refreshes price (via card-server)
3. Routes to Tier 1/2/3 based on confidence + card properties
4. **Tier 1:** requests front photo → waits → requests back photo → waits → builds listing template → calls `postToEbay()` [stub] → marks as listed
5. `/status`, `/skip`, `/pause` — workflow controls
6. Handles inline keyboard callbacks (skip, approve, override, details)
7. Detects price override text input [stub — not executed]
8. Tier 2/3 stubs — fall through to Tier 1 with a warning message

### Full Functionality (future)

Everything above, plus:
- **Tier 2/3 LLM:** Spawn per-card Claude conversations with scoped tools and context
- **LLM relay:** Forward user free-text from Telegram/dashboard to the active LLM conversation
- **Price overrides:** Actually apply user-specified prices
- **eBay posting:** Real `postToEbay()` via ebay service
- **Sell/hold decisions:** Use market data trends to recommend listing or holding
- **Dashboard triggers:** Accept commands from dashboard (not just Telegram)
- **Multi-input:** Dashboard and Telegram as parallel input channels to the same orchestrator

### How it interacts with the new organization

The orchestrator imports and calls service functions:

```
orchestrator
├── calls card-server   → getNextUnlisted(), computePrice(), markAsListed()
├── calls telegram      → sendMessage(), requestPhoto(), onCommand()
├── calls ebay          → createListing(), uploadPhotos()
└── calls llm (internal)→ Claude API for Tier 2/3 reasoning
```

Dashboard also calls services + orchestrator:

```
dashboard
├── calls card-server   → searchInventory(), getAnalytics(), getPriceHistory()
├── calls orchestrator  → triggerNext(), getStatus(), overridePrice()
├── calls telegram      → (maybe) send notifications
└── calls ebay          → viewListings(), editListing()
```

## Migration Steps

1. [ ] Create `services/` folder, move `card-server/` into it
2. [ ] Extract `src/telegram/` → `services/telegram/`
3. [ ] Create `services/ebay/` (extract from `tier1Pipeline.ts` stub)
4. [ ] Extract `src/agent/` → `orchestrator/`
5. [ ] Update all import paths
6. [ ] Update `src/index.ts` to wire the new locations
7. [ ] Update build config (`tsconfig.json`, `package.json`)
