# Project Structure

The folder layout and structural conventions. For the platform vision, capability catalog, and status, see the root `CLAUDE.md`. The layout reflects a core tenet — modular, generally-reusable components with clear boundaries: each `services/*` folder owns one external system, and `dashboard/` is the control-plane + analytics surface.

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
- **Reused by everything:** Imported as `services.card_server` — the shared substrate the other capabilities build on. Also runs as an MCP server via `python -m services.card_server.index` (from the repo root).

### telegram (services/telegram/)
- **Owns:** Telegram Bot API (send/receive messages, photos, inline keyboards)
- **Exposes:** a `Bot` class (send messages/photos, request photos, dispatch command/photo/text/callback events) + message renderers.

### ebay (services/ebay/)
- **Owns:** eBay Sell API (listing CRUD, photo upload, category mapping)
- **Exposes:** a post-to-eBay call over a listing-template type.

## Dashboard + Orchestrator (dashboard/)

### frontend (dashboard/frontend/)
- React + Vite + TypeScript
- Pages: CollectionGridPage (card images), CollectionPage (table), AnalyticsPage (charts), CardDetailPage (per-card sales/price graph)
- The analytics + browsing surface, built on the card-data store's reporting layer.

### backend (dashboard/backend/)
- Flask REST API serving the frontend
- **Important:** Backend does NOT duplicate functionality owned by card-server. `app.py` holds no SQL — it's a thin HTTP layer that delegates all reads/analytics to `card_server`'s `ReportingService` and serializes the result.

### orchestrator (dashboard/backend/orchestrator/)
- The **seller listing pipeline** — one capability of the platform, composed from several others.
- State machine that drives the listing workflow: processes one card at a time — pick → price → route tier → photo → list → done
- Reuses the card-data store, pricing, Telegram, and eBay services rather than reimplementing any of them. See `orchestrator/PIPELINE.md` for pipeline-specific detail.

## Language

Backend/services are Python; the React frontend is TypeScript.
