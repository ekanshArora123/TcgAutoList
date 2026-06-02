# Project Structure

## Hierarchy

```
project/
├── services/                  <- Microservices (each owns an external system)
│   ├── card_server/           <- DB + TCGplayer + pricing (data/cards.db lives here)
│   ├── telegram/              <- Telegram bot I/O
│   └── ebay/                  <- eBay listing CRUD [stub]
├── dashboard/                 <- Web UI + orchestrator (control plane)
│   ├── frontend/              <- React + Vite + TypeScript
│   └── backend/
│       ├── app.py             <- Flask REST API
│       └── orchestrator/      <- Workflow engine (state machine)
│           ├── main.py        <- Entry point: wires services + orchestrator, starts app
│           └── types.py       <- Shared types (re-exports card_server models)
├── tests/                     <- pytest suite
├── requirements.txt           <- Python deps (no packaging / no install)
└── docs/                      <- Reference docs (pricing algorithm, TCGplayer API)
```

Imports resolve via the repo root on `sys.path` (run `python -m <dotted.path>` from
the root). Folders use underscores so they're valid module names, e.g.
`services.card_server`, `dashboard.backend.orchestrator`.

## Services (services/)

Each service owns one external system and provides abstraction over it. Callers should not know the service's internal API format, auth, or data model.

### card-server (services/card_server/)
- **Owns:** SQLite database, TCGplayer API, pricing algorithm, market data collection
- **Exposes:** CollectionService + PricingService (simple API for coded callers), MCP tools (rich API for LLM)
- **Internal:** CRUD helpers, TCGplayer fetchers, pricing math, market aggregators
- **Status:** Done
- **Standalone:** Imported as `services.card_server`. Runs independently as an MCP server via `python -m services.card_server.index` (from the repo root).

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
- **Important:** Backend does NOT duplicate functionality owned by card-server. `app.py` holds no SQL — it's a thin HTTP layer that delegates all reads/analytics to `card_server`'s `ReportingService` and serializes the result.
- The whole backend is now Python, so it can import `card_server` services directly — the former TS/Python language boundary is resolved.

### orchestrator (dashboard/backend/orchestrator/)
- State machine that drives the listing workflow
- Processes one card at a time: pick → price → route tier → photo → list → done
- Consumes all three services (card-server, telegram, ebay)
- **Current:** Tier 1 pipeline works. Tier 2/3 LLM and eBay posting are stubs.

## Language

The backend/services are Python; the React frontend stays TypeScript. The TS→Python
conversion is complete.
