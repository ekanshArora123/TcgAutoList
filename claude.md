# TcgAutoList — Agentic Pokemon Card eBay Listing System

## Project Overview

TcgAutoList automates listing ~10,000 Pokemon cards on eBay. It uses an **agentic architecture** with MCP servers (Model Context Protocol) and a Telegram bot for photo intake.

**Workflow:** Agent picks next unlisted card → requests photos via Telegram → waits for photos → gathers card info + price from TCGplayer → builds listing → creates eBay listing → marks as done → repeats.

## Architecture

### Components

| Component | Type | Location | Purpose |
|-----------|------|----------|---------|
| `card-server` | MCP Server | `card-server/` | **Combined** collection, card info, and pricing server. SQLite DB with CRUD + TCGplayer data fetching + pricing algorithm. Standalone — usable independently of the parent project. |
| `ebay-mcp` | MCP Server (external) | npm package | eBay Sell API access (325 tools) |
| `telegram/bot` | Module | `src/telegram/` | Sends photo requests & receives photos via Telegram Bot API |
| `listingAgent` | Orchestrator | `src/agent/` | State machine driving the card → listing workflow |

### card-server Internal Architecture

The agent interacts with **two service files**. Everything else is a helper.

```
card-server/src/
├── index.ts                        ← MCP entry: 30+ tools, delegates to services
├── db.ts                           ← SQLite init/close (implemented)
├── schema.sql                      ← 4-table schema
├── types.ts                        ← Zod schemas + TS types
├── services/                       ← AGENT-FACING (high-level composed operations)
│   ├── collectionService.ts        ← Cards + SKUs + Inventory management
│   └── pricingService.ts           ← Pricing operations + TCGplayer fetch workflows
└── helpers/                        ← INTERNAL (services compose these, agent doesn't call directly)
    ├── crud/                       ← Pure DB CRUD, one file per table
    │   ├── cards.ts                ← Card metadata CRUD (implemented)
    │   ├── skus.ts                 ← SKU variant CRUD (implemented)
    │   ├── inventory.ts            ← Physical inventory CRUD (implemented)
    │   └── prices.ts               ← Price history CRUD (implemented)
    ├── pricing/
    │   └── algorithm.ts            ← Pricing algorithm: lowest-listing anchor (implemented)
    └── tcgplayer/
        ├── fetchPrices.ts          ← Active listings API (implemented), sold listings (TODO)
        ├── fetchCardInfo.ts        ← Card metadata fetching (TODO)
        └── formatters.ts           ← Condition/finish format conversion for TCGplayer API (implemented)
```

### Data Flow

```
card-server (pick next unlisted card)
        ↓ inventory_id, tcgplayer_id, condition, finish
Telegram bot (request & receive photos)
        ↓ photo file paths
card-server (fetch price from TCGplayer if stale, run pricing algorithm)
        ↓ card info + price estimate + confidence + reasoning
ebay-mcp (create inventory item + offer)
        ↓ listing ID
card-server (mark as listed with eBay listing ID)
```

## Database Schema (card-server)

4 tables in SQLite, migrated from MySQL. Designed with proper foreign keys replacing the old hashed-SKU approach.

```sql
-- cards: canonical card metadata from TCGplayer (keyed by tcgplayer product ID)
-- skus: card variants (card_id + condition + finish + specialty_one + specialty_two) with UNIQUE composite
-- inventory: physical cards owned, each pointing to a SKU + optional pricing_sku_id override
-- prices: historical price estimates per SKU per date (composite PK: sku_id + calculation_date)
```

### Key design decisions:
- **`skus` uses auto-increment PK + composite UNIQUE** instead of hashing the composite key
- **`inventory.pricing_sku_id`** allows pricing a borderline card against a different condition (e.g., price an LP-NM card as NM)
- **`inventory.tags`** is a comma-separated string for card-specific hidden details (hidden creases, marks, trade history). Intentionally not normalized — the set of possible tags is variable and evolving.
- **`lotsmaster` and `sellerslist`** from the original MySQL DB are omitted — not needed for the listing workflow.

### SKU field meanings:
- **`condition`**: NM, LP, MP, HP, DMG. Also supports in-between grades (LP-NM, MP-LP, HP-MP) for internal tracking, but TCGplayer only supports the primary conditions for listing.
- **`finish`**: Regular, Holo, Reverse-Holo.
- **`specialty_one`**: Reproducible TCGplayer-level variants that affect the product ID or finish on TCGplayer: "First Edition", "None". These are NOT edge cases — TCGplayer sells them separately with their own listings/solds data.
- **`specialty_two`**: Reproducible but NOT represented on TCGplayer: graded cards (PSA 10, CGC 9), specific known errors. These DO trigger manual pricing review.
- **`tags`** (on inventory, not SKU): Unique to the physical card — hidden crease, surface marks, bought as trade, etc.

## TCGplayer API Integration

### Listings API (implemented)
- **Endpoint:** `POST https://mp-search-api.tcgplayer.com/v1/product/{id}/listings?mpfev=2163`
- **Auth:** None (browser-mimicking headers)
- **Payload:** JSON with filters for condition, finish ("printing"), language, seller status. Sorted by `price+shipping` ascending.
- **Pagination:** `from` (offset) and `size` (max 50 per request)
- **Seller filtering:** Only include sellers with rating > 80 and sales > 30 (or "X+" format)
- **Format conversion:** Internal codes must be converted to TCGplayer strings:
  - Conditions: `NM` → `"Near Mint"`, `LP` → `"Lightly Played"`, etc.
  - Finishes: `Holo` → `"Holofoil"`, `Reverse-Holo` → `"Reverse Holofoil"`, `Regular` → `"Normal"`
  - 1st Edition: `Holo` + `First Edition` → `"1st Edition Holofoil"`
  - WOTC sets (Base Set Shadowless, Jungle, Fossil, Gym, Neo, Team Rocket): `Holo` → `"Unlimited Holofoil"`, `Regular` → `"Unlimited"`

### Sold Listings API (implemented)
- **Endpoint:** `POST https://mpapi.tcgplayer.com/v2/product/{id}/latestsales?mpfev=4952`
- **Auth:** `TCGAuthTicket_Production` cookie required for full access (25/page with pagination). Without auth: max 5 results, no pagination.
- **Payload:** `{ variants: number[], listingType: "All", conditions: number[], languages: number[], limit: 25, offset: 0 }`
- **Pagination:** With auth cookie, `offset` and `limit` work properly. `previousPage`/`nextPage` fields indicate more pages.
- **Fallback (no auth):** Query each condition separately to get up to 25 results (5 conditions x 5 each)
- **Condition IDs:** 1=Near Mint, 2=Lightly Played, 3=Moderately Played, 4=Heavily Played, 5=Damaged
- **Variant (finish) IDs:** 10=Normal, 11=Holofoil, 77=Reverse Holofoil (product-specific — only IDs valid for that product work)
- **Language IDs:** 1=English
- **Response fields per sale:** `condition`, `variant`, `language`, `quantity`, `title`, `listingType`, `purchasePrice`, `shippingPrice`, `orderDate`
- **Auth cookie source:** `TCGPLAYER_AUTH_COOKIE` env var = the `TCGAuthTicket_Production` cookie value from browser after logging into tcgplayer.com

### Card Info API (implemented)
- **Endpoint:** `POST https://mp-search-api.tcgplayer.com/v1/search/request?q=&isList=false&mpfev=2163`
- **Auth:** None (browser-mimicking headers)
- **Payload:** Search query with `productId` filter: `{ algorithm: "sales_synonym_v2", from: 0, size: 1, filters: { term: { productLineName: ["pokemon"], productId: [numericId] } }, listingSearch: { ... } }`
- **Returns:** Rich product data including:
  - `productName`, `setName`, `rarityName`, `productLineName`, `marketPrice`, `lowestPrice`, `totalListings`, `foilOnly`
  - `customAttributes`: `number` (card number), `hp`, `stage`, `energyType`, `attacks` (1-4), `weakness`, `resistance`, `retreatCost`, `releaseDate`, `flavorText`, `description`
  - `aggregations`: listing counts per condition and per printing (finish)

### Price Points API (informational)
- **Endpoint:** `GET https://mpapi.tcgplayer.com/v2/product/{id}/pricepoints`
- **Returns:** `[{ printingType, marketPrice, buylistMarketPrice, listedMedianPrice }]` per finish
- **Note:** Not currently used — market price is unreliable per pricing philosophy

### Set Catalog API (implemented)
- **Endpoint:** `GET https://mpapi.tcgplayer.com/v2/Catalog/SetName/{setId}?mpfev=4952`
- **Auth:** None
- **Returns:** `{ setNameId, name, cleanSetName, urlName, abbreviation, releaseDate, isSupplemental, active, setDescription }`
- **Usage:** The `setId` from the search API response maps directly to this endpoint (e.g., 604 = Base Set)

## Pricing Algorithm

### Philosophy
- **Goal:** Maximum profit. Pricing aggressiveness should be variable.
- **Primary metric:** Lowest active TCGplayer listing price. Works well for liquid NM modern cards.
- **Secondary metric:** Average of recent sold prices. Takes precedence for illiquid cards.
- **TCGplayer "market price" is NOT used.** It is unreliable.
- **Fees:** Both TCGplayer and eBay take ~15%. Stored as `FEE_RATE = 0.15`.

### Current algorithm (v1: `lowest-listing-v1`)
1. If active listings exist → anchor on lowest listing (price + shipping combined)
2. If sold data also exists → compare. If they diverge >30% and solds are lower, use sold average instead (listings may be stale). Flag for review.
3. If no active listings → fall back to sold average
4. If no data at all → null price, flagged as unpriceable
5. Edge case flags: high value (>$50), specialty_two cards (graded/errors), few solds (<3) → lower confidence / manual review

### Liquid value formula
```
liquid_value = (sell_price * 0.85) - shipping_cost
```
Where shipping = $1 for cards $25 and under, $5 for cards over $25.

### Cross-condition extrapolation
When no data exists for a specific condition, extrapolate from another condition of the same card:
- **30% discount per full condition tier, compounding:** NM $10 → LP $7.00 → MP $4.90 → HP $3.43
- **In-between conditions = average of neighbors:** LP-NM = (NM + LP) / 2
- All extrapolation logic is in one function (`extrapolateAcrossConditions` in `algorithm.ts`) for easy modification
- Always flagged for manual review with max 35% confidence

### Future pricing versions
- **v2:** Factor in sales velocity (sales per 48hr window). If a card sells frequently, price above lowest listing since it'll sell anyway. If it sells rarely, match or undercut.
- **v3 (LLM sampling):** TCGplayer sales, TCGplayer listings, eBay solds, and eBay listings are all separate tools the LLM calls to price edge cases. The model reasons about the data rather than following rules.

## Tech Stack

- **Runtime:** Node.js with TypeScript (ES2022 target, ESM modules)
- **MCP SDK:** `@modelcontextprotocol/sdk` — server & client implementations
- **Database:** SQLite via `better-sqlite3` — local, zero-config, single-file DB
- **Card Data:** TCGplayer internal search API — web requests for listings, solds, card info
- **Telegram:** `node-telegram-bot-api` — bot for photo request/receive
- **eBay:** `ebay-mcp` — open-source MCP server wrapping eBay Sell APIs
- **Validation:** Zod — runtime type checking for MCP tool inputs/outputs
- **Config:** dotenv — environment variable management

## Environment Variables

Required in `.env`:
```
TELEGRAM_BOT_TOKEN=       # From @BotFather
TELEGRAM_CHAT_ID=         # Your personal chat ID
EBAY_CLIENT_ID=           # eBay developer credentials
EBAY_CLIENT_SECRET=       # eBay developer credentials
EBAY_ENVIRONMENT=sandbox  # 'sandbox' or 'production'
TCGPLAYER_AUTH_COOKIE=    # Optional: TCGAuthTicket_Production cookie for full sales data
```

## Commands

```bash
# Parent project
npm run build              # Compile TypeScript
npm run start              # Start the listing workflow
npm run test               # Run all tests

# card-server (standalone)
cd card-server
npm run build              # Compile TypeScript
npm run start              # Run MCP server via STDIO
npm run test               # Run card-server tests
```

## Design Principles

1. **Standalone card-server:** The MCP server in `card-server/` is independently deployable. It has its own package.json, tsconfig, and no imports from the parent project.
2. **Two-file agent interface:** The agent only calls `collectionService.ts` and `pricingService.ts`. All CRUD helpers and TCGplayer fetchers are internal.
3. **Idempotent operations:** Re-running the agent skips already-listed cards. No duplicate listings.
4. **Pricing transparency:** Every price computation returns a human-readable `reasoning` string explaining the decision.
5. **Graceful degradation:** If TCGplayer is unreachable, the agent flags the card for manual pricing. If eBay fails, it queues for retry.

## Next Steps

### Immediate (get card-server fully functional)
1. ~~**Implement `fetchSoldListings`**~~ — Done. Uses `mpapi.tcgplayer.com/v2/product/{id}/latestsales` with per-condition querying for up to 25 results.
2. ~~**Implement `fetchCardInfo`**~~ — Done. Uses search API with productId filter for full card metadata.
3. **Write migration script** (`card-server/src/migrate.ts`) — Read the MySQL dump (`database-dump.sql`) and populate the new SQLite schema. Map: `cardinfo` → `cards`, `skutable` → `skus`, `actualinventory` → `inventory`, `cardprices` → `prices`.
4. **Run `npm install` and verify TypeScript compilation** in `card-server/`.
5. **Write tests** for CRUD helpers and pricing algorithm.

### Medium-term (integrate with parent project)
6. **Wire card-server into the parent project's MCP client** — The orchestrator connects to card-server via STDIO and calls its tools.
7. **Build the Telegram bot module** — Photo request/receive workflow.
8. **Build the eBay listing builder** — Takes an `InventoryDetail` and constructs an eBay listing via `ebay-mcp`.
9. **Build the orchestrator state machine** — IDLE → PICK_CARD → REQUEST_PHOTOS → WAIT_PHOTOS → GATHER_INFO → BUILD_LISTING → CREATE_LISTING → MARK_LISTED → repeat.

### Long-term (advanced pricing + optimization)
10. **Pricing v2:** Sales velocity analysis — track sales per 48hr window to decide if pricing above lowest listing is viable.
11. **Pricing v3 (LLM sampling):** TCGplayer solds, TCGplayer listings, eBay solds, eBay listings as separate MCP tools the LLM reasons over for edge cases.
12. **In-between condition listing choice** — When a card is LP-NM, the user chooses whether to list as LP or NM on TCGplayer/eBay. Build a tool or prompt for this decision.
13. **Bulk re-pricing** — Periodically re-fetch prices for cards with stale `latest_calc_date` (>14 days old, matching the SQL query in `QuickCollectionValueFinder.py`).

## Common Tasks for AI Assistants

- **Adding a new MCP tool:** Add Zod schema in `types.ts`, add handler in `collectionService.ts` or `pricingService.ts`, register in `index.ts`.
- **Changing the pricing algorithm:** Edit `helpers/pricing/algorithm.ts`. The `computePrice()` function and the constants above it are the only things to change. The extrapolation model lives entirely in `extrapolateAcrossConditions()`.
- **Adding a new TCGplayer data source:** Add a fetch function in `helpers/tcgplayer/`, add format conversions in `formatters.ts`, wire it into the relevant service.
- **Changing condition/finish mappings:** Edit `helpers/tcgplayer/formatters.ts`. All TCGplayer API format conversions live there.
