# Project Structure

## Hierarchy

```
project/
├── services/                  <- Microservices (each owns an external system)
│   ├── card-server/           <- DB + TCGplayer + pricing
│   ├── telegram/              <- Telegram bot I/O
│   └── ebay/                  <- eBay listing CRUD [stub]
├── dashboard/                 <- Web UI + orchestrator (control plane)
│   ├── frontend/              <- React + Vite + TypeScript
│   └── backend/
│       ├── app.py             <- Flask REST API
│       └── orchestrator/      <- Workflow engine (state machine)
├── shared/                    <- Entry point + shared types (package `shared`)
│   ├── main.py                <- Wires services + orchestrator, starts app
│   └── types.py               <- Shared types (re-exports card_server models)
└── docs/                      <- Reference docs (pricing algorithm, TCGplayer API)
```

## Services (services/)

Each service owns one external system and provides abstraction over it. Callers should not know the service's internal API format, auth, or data model.

### card-server (services/card-server/)
- **Owns:** SQLite database, TCGplayer API, pricing algorithm, market data collection
- **Exposes:** CollectionService + PricingService (simple API for coded callers), MCP tools (rich API for LLM)
- **Internal:** CRUD helpers, TCGplayer fetchers, pricing math, market aggregators
- **Status:** Done
- **Standalone:** Importable as the `card_server` package (`services/card-server/card_server/`). Can run independently as an MCP server via `python -m card_server.index`.

### telegram (services/telegram/)
- **Owns:** Telegram Bot API (send/receive messages, photos, inline keyboards)
- **Exposes:** Bot class with sendMessage(), requestPhoto(), onCommand() etc.
- **Status:** Done

### ebay (services/ebay/)
- **Owns:** eBay Sell API (listing CRUD, photo upload, category mapping)
- **Exposes:** postToEbay(), ListingTemplate interface
- **Status:** Stub — returns fake listing IDs

## Dashboard + Orchestrator (dashboard/)

### frontend (dashboard/frontend/)
- React + Vite + TypeScript
- Pages: CollectionGridPage (card images), CollectionPage (table), AnalyticsPage (charts)
- Read-only for now. Will gain control capabilities (trigger workflows, override prices, manage listings).

### backend (dashboard/backend/)
- Flask REST API serving the frontend
- **Important:** Backend should NOT duplicate functionality owned by card-server. DB queries, pricing logic, and TCGplayer interactions should go through card-server services. (The read-only dashboard `app.py` currently queries SQLite directly for reporting; write operations should go through `card_server` services.)
- The whole backend is now Python, so it can import `card_server` services directly — the former TS/Python language boundary is resolved.

### orchestrator (dashboard/backend/orchestrator/)
- State machine that drives the listing workflow
- Processes one card at a time: pick → price → route tier → photo → list → done
- Consumes all three services (card-server, telegram, ebay)
- **Current:** Tier 1 pipeline works. Tier 2/3 LLM and eBay posting are stubs.

## Language

The backend/services are Python; the React frontend stays TypeScript. The TS→Python
conversion is complete — see CONVERSION_PLAN.md for the historical file mapping.
