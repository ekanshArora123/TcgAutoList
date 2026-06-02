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

## Design Principle: Don't Duplicate Owned Functionality

The dashboard backend does NOT need to delegate everything to card-server. It only needs to delegate **functionality that card-server already owns**: database access and pricing logic. If card-server owns it, use it — don't rewrite it.

This is a code duplication principle, not a "thin backend" mandate. The dashboard backend is free to have its own logic for:
- Response shaping, pagination, API endpoint formatting
- Dashboard-specific analytics computations
- Image serving
- Any UI-specific business logic

### What card-server owns (delegate to it)
- Database schema and queries (card/sku/inventory/price lookups)
- Pricing algorithm and price computation
- TCGplayer API interactions
- Market data collection and snapshots

### The modularity contract

card-server should expose query functions at the right abstraction level. The goal: **if the DB changes its internal column names or schema, dashboard code shouldn't need to change.** card-server's API should insulate callers from internal structure.

But the opposite extreme is bad too — card-server should NOT expose a unique function for every possible query permutation. The right balance is case-by-case:
- A general `searchInventory(filters)` that returns structured results = good
- A `getCardsWithPricesBetween5And10InNearMintHolofoil()` = bad
- Dashboard writing raw SQL with card-server's column names = bad (couples to schema)

### Current Problem

The dashboard backend (`app.py`) writes raw SQL queries directly against the SQLite DB, duplicating query logic and coupling to the DB schema. When the schema changes, both card-server and dashboard need updating.

### Language boundary

The issue is NOT that SQLite is language-specific — both Python (`sqlite3`) and TypeScript (`better-sqlite3`) can read the same `.db` file. The issue is that card-server's service classes are TypeScript, so the Python dashboard backend can't `import` them directly.

Options:
- (a) **Rewrite dashboard backend in TypeScript** — can import card-server services directly (same pattern as the orchestrator). This is the cleanest solution.
- (b) **card-server exposes an HTTP API** — dashboard calls it over HTTP. Adds network overhead but allows any language.
- (c) **Shared SQL views** — Define views in the SQLite schema that encapsulate the joins. Both languages query the views instead of raw tables. Lightweight compromise.

## TODO: Eliminate Duplicate Code

- [ ] **`/api/cards` and `/api/collection`** — Build complex SQL joins duplicating CollectionService logic. card-server should expose a search/query function.
- [ ] **`/api/filters`** — Distinct value lookups. card-server should provide this.
- [ ] **`/api/analytics/*`** — Pure read queries. May stay dashboard-specific (analytics is a dashboard concern), but the underlying data access should go through card-server if possible.
- [ ] **`/api/images/<card_id>`** — Dashboard-specific, fine as-is.
- [ ] **Resolve the language boundary** — pick an option above.

## Future: Dashboard as Control Plane

The dashboard should eventually provide UI controls for:
- Triggering listing workflows (currently Telegram-only via `/next`)
- Reviewing and overriding prices (manual_check cards)
- Managing eBay listings (view, edit, relist)
- Viewing orchestrator status and logs
- Handling some Telegram functionality through the web UI

The dashboard and orchestrator live at the same level in the project hierarchy. The dashboard is the user-facing control plane; the orchestrator is the automated workflow engine. Both consume the same underlying services (card-server, telegram, eBay).
