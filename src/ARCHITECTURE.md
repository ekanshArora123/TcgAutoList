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

## The Problem

This mixes three different scopes:

1. **Telegram service** — An I/O service (like card-server is for DB/TCGplayer). Receives commands, sends messages, handles photos.
2. **eBay service** — Another I/O service (stub). Posts listings, manages active listings.
3. **Orchestrator / Manager** — The high-level controller that coordinates card-server + telegram + eBay to execute the listing workflow.

Telegram and eBay are **peer services** to card-server — each wraps an external system. The orchestrator sits above all three.

## Restructuring Options

### Option A: Separate sibling services (recommended direction)

```
project/
├── card-server/          <- Data + pricing microservice (exists)
├── telegram-server/      <- Telegram I/O microservice (extract from src/)
├── ebay-server/          <- eBay I/O microservice (new, currently stub in tier1Pipeline)
├── dashboard/            <- Web UI (exists)
└── src/                  <- Orchestrator + agent logic ONLY
    ├── index.ts
    ├── orchestrator.ts
    ├── tierRouter.ts
    ├── tier1Pipeline.ts
    ├── llm.ts / tools.ts / systemPrompt.ts
    └── types.ts
```

**Pros:** Clear boundaries. Each service is independently testable. Matches the mental model (card-server:DB :: telegram-server:Telegram :: ebay-server:eBay).
**Cons:** More folders. Telegram is thin enough that a separate "server" may be over-engineering.

### Option B: Fold Telegram + eBay into card-server

card-server becomes the unified backend for all external I/O (DB, TCGplayer, Telegram, eBay).

```
project/
├── card-server/          <- All external I/O (DB, TCGplayer, Telegram, eBay)
│   └── src/services/
│       ├── collectionService.ts
│       ├── pricingService.ts
│       ├── telegramService.ts    <- new
│       └── ebayService.ts        <- new
├── dashboard/
└── src/                  <- Orchestrator only
```

**Pros:** Fewer top-level folders. One place for all "talk to external systems" code.
**Cons:** card-server becomes a monolith. Telegram depends on `node-telegram-bot-api` which is unrelated to cards. eBay has its own auth/API complexity. Muddies the "card data" identity.

### Option C: Hybrid — keep Telegram in src/, extract eBay

Telegram stays in `src/` because it's tightly coupled to the orchestrator (it IS the user interface for the orchestrator). eBay becomes a sibling service because it's more like card-server (CRUD against an external platform).

```
project/
├── card-server/          <- Data + pricing
├── ebay-server/          <- eBay listing CRUD (new)
├── dashboard/            <- Web UI
└── src/                  <- Orchestrator + Telegram (user interface)
    ├── telegram/
    ├── agent/
    └── index.ts
```

## Where Does the Orchestrator Live?

The orchestrator is the **project-level controller**. It doesn't belong inside any microservice. Options:

1. **Keep in `src/`** (current) — `src/` = "the app that runs the show". Simple.
2. **Move to `dashboard/`** — If dashboard becomes the primary control plane, the orchestrator could live alongside it. But dashboard is a web UI; the orchestrator is a background process. Mixing them is awkward unless dashboard becomes a full backend service.
3. **Top-level `orchestrator/`** — Explicit separation. Makes sense if `src/` feels too generic.

**Recommendation:** Keep in `src/` for now. Rename mentally: `src/` = "the listing engine". When dashboard gains control capabilities, it will call into the orchestrator via an API, not absorb it.

## Decision Needed

Choose between Options A, B, or C above. Key question: **is Telegram/eBay functionality different enough from card data to warrant separate services, or is "all external I/O" a coherent single service?**

Factors:
- Telegram is ~2 files and tightly coupled to orchestrator UX
- eBay will be substantial (listing CRUD, auth, category mapping, photo upload)
- card-server is already well-scoped as "card data + pricing"
- Dashboard will eventually need to call Telegram and eBay functions too
