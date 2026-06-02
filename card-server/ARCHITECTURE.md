# card-server Architecture

## Role

card-server is the **data microservice** for the entire TcgAutoList project. It owns the SQLite database, all TCGplayer API interactions, pricing algorithms, and market data collection. Every other component (dashboard, orchestrator, future services) gets card/pricing/market data through card-server.

## API Surface Design

card-server exposes **two tiers of access**:

### Simple API (for coded callers — orchestrator Tier 1, dashboard backend, scripts)

A small number of high-level functions that hide internal complexity. Callers should not need to understand the DB schema, pricing algorithm, or TCGplayer API format.

Examples of the right granularity:
- `getNextUnlistedCard()` — not `searchInventory({ status: 'new', limit: 1 })`
- `priceCard(inventoryId)` — not `fetchListings() + fetchSolds() + computePrice() + storePrice()`
- `getCardWithImage(cardId)` — not `getCard() + check image path + fetch if missing`

**Entry points:** `CollectionService` and `PricingService` (in `services/`).

### Rich API (for LLM callers — Tier 2/3 agent conversations)

More granular functions exposed as MCP tools so the LLM can reason over intermediate data. The LLM should still get abstraction where possible — it shouldn't need to write SQL or know the DB schema — but it can access:
- Individual pricing components (active listings, sold listings, market snapshots)
- Pricing algorithm inputs and outputs separately
- Card metadata lookups and search

**Entry point:** MCP tool definitions in `index.ts`.

### Internal (not exposed)

- CRUD helpers (`helpers/crud/`) — raw DB operations
- TCGplayer fetch functions (`helpers/tcgplayer/`) — raw API calls
- Pricing internals (`helpers/pricing/algorithm.ts`) — called by PricingService
- Market data internals (`helpers/market/`) — called by MarketCollector and PricingService

## Directory Structure

```
card-server/
├── src/
│   ├── index.ts              <- MCP server entry (Rich API for LLM)
│   ├── db.ts                 <- SQLite init/close
│   ├── schema.sql            <- DB schema
│   ├── types.ts              <- Zod schemas + TS types
│   ├── services/             <- Simple API (for coded callers)
│   │   ├── collectionService.ts
│   │   └── pricingService.ts
│   ├── helpers/              <- Internal (not exposed)
│   │   ├── crud/             <- Raw DB operations
│   │   ├── pricing/          <- Pricing algorithm + config
│   │   ├── tcgplayer/        <- TCGplayer API calls
│   │   └── market/           <- Market data collection + snapshots
│   ├── collect.ts            <- CLI runner for market data collection
│   ├── dedup-inventory.ts    <- One-off utility script
│   └── migrate.ts            <- MySQL -> SQLite migration (historical)
├── scripts/
│   └── fetch_images.py       <- Download card images from TCGplayer CDN
└── data/
    └── cards.db              <- SQLite database
```

## TODO

- [ ] Audit `CollectionService` and `PricingService` for Simple API completeness — are there high-level operations that callers currently have to compose manually?
- [ ] Consider adding convenience methods: `getNextUnlistedCard()`, `getCardSummary(inventoryId)` (card + sku + latest price + image path in one call)
- [ ] Image management: `fetch_images.py` was moved here from `tools/`. Consider whether to keep it as a Python script or rewrite in TypeScript to match the rest of the codebase.
- [ ] Review MCP tool list in `index.ts` — are there tools that expose too much internal detail? Are there missing tools the LLM needs?
- [ ] `collect.ts` and `dedup-inventory.ts` are standalone scripts. Consider whether they should be wired into the service layer or stay as CLI utilities.
