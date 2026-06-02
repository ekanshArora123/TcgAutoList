# dashboard Architecture

## Role

Web UI for browsing, analyzing, and (eventually) controlling the card collection and listing pipeline. Modular frontend/backend split.

## Structure

```
dashboard/
├── frontend/          <- React + Vite + TypeScript
│   └── src/
│       ├── App.tsx              <- Nav + page routing
│       ├── api.ts               <- API client
│       └── pages/
│           ├── CollectionGridPage.tsx   <- Card grid with images
│           ├── CollectionPage.tsx       <- Table view
│           └── AnalyticsPage.tsx        <- Charts and stats
└── backend/           <- Flask (Python)
    └── app.py         <- REST API endpoints
```

## Design Principle: Thin Backend

The dashboard backend should be a **thin API layer**. card-server is the true backend — it owns the DB, pricing logic, and data fetching. The dashboard backend should:

1. **Call card-server functions** for data retrieval wherever possible
2. **Only add dashboard-specific logic** at the API endpoint layer (pagination formatting, response shaping for the frontend, image serving)
3. **NOT duplicate** card-server's query logic, pricing calculations, or data transformations

### Current Problem

Right now the dashboard backend (`app.py`) writes raw SQL queries directly against the SQLite DB, duplicating logic that already exists in card-server's services. This creates a maintenance burden — when the DB schema or query logic changes, both card-server and dashboard need updating.

## TODO: Eliminate Duplicate Code

- [ ] **`/api/cards` and `/api/collection`** — These endpoints build complex SQL joins (inventory + skus + cards + prices + market_snapshots) that largely duplicate what `CollectionService` already does. card-server should expose a query/search function that the dashboard calls.
- [ ] **`/api/filters`** — Distinct value lookups. card-server should provide this.
- [ ] **`/api/analytics/*`** — Summary stats, histograms, breakdowns. These are pure read queries. Decide: keep as dashboard-specific analytics (reasonable — card-server doesn't need analytics), OR move to card-server if other consumers will want the same stats.
- [ ] **`/api/images/<card_id>`** — Image serving is dashboard-specific, fine to keep here.
- [ ] **Language boundary:** Dashboard backend is Python, card-server is TypeScript. Options for calling card-server:
  - (a) card-server exposes an HTTP API (adds complexity)
  - (b) Dashboard backend imports card-server's SQLite DB directly but uses shared SQL views/queries
  - (c) Rewrite dashboard backend in TypeScript to import card-server services directly (matches orchestrator pattern)
  - (d) Keep raw SQL in Python but centralize the query definitions

## Future: Dashboard as Control Plane

The dashboard should eventually provide UI controls for:
- Triggering listing workflows (currently Telegram-only via `/next`)
- Reviewing and overriding prices (manual_check cards)
- Managing eBay listings (view, edit, relist)
- Viewing orchestrator status and logs

This means the dashboard backend will need to call orchestrator/telegram/eBay services, not just card-server. The backend will grow beyond a thin layer for these control operations.
