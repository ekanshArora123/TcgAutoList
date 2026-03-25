# TcgAutoList — Agentic Pokemon Card eBay Listing System

## Project Overview

TcgAutoList automates listing ~10,000 Pokemon cards on eBay. It uses an **agentic architecture** with MCP servers (Model Context Protocol) and a Telegram bot for photo intake.

**Workflow:** User sends `/next` via Telegram → orchestrator picks next unlisted card → fetches price → requests photo via Telegram → user sends photo → orchestrator builds listing → creates eBay listing → marks as done → waits for next command.

## Architecture

### Components

| Component | Type | Location | Purpose |
|-----------|------|----------|---------|
| `card-server` | MCP Server | `card-server/` | **Combined** collection, card info, and pricing server. SQLite DB with CRUD + TCGplayer data fetching + pricing algorithm. Standalone — usable independently of the parent project. |
| `ebay-mcp` | MCP Server (external) | npm package | eBay Sell API access (325 tools) |
| `telegram/bot` | Module (NOT MCP) | `src/telegram/` | Bidirectional I/O channel: sends messages/photo requests, receives commands/photos/text. Transport layer, not a tool provider. |
| `orchestrator` | LLM Agent | `src/agent/` | Claude-powered agent that processes cards. Fresh conversation per card, tools = card-server + ebay-mcp, I/O = Telegram. |

### Telegram Module (NOT an MCP server)

Telegram is a **transport layer** for the orchestrator, not a tool provider. MCP servers are for standalone, reusable tool providers (card-server works independently). The Telegram bot only makes sense in the context of this specific workflow and is tightly coupled to the orchestrator.

```
You (Telegram) ←→ Telegram Bot Module ←→ Orchestrator ←→ MCP Servers (card-server, ebay-mcp)
```

The bot forwards user messages/photos to the orchestrator as events, and sends orchestrator output back to the user.

### Orchestrator (LLM Agent)

The orchestrator is a Claude-powered agent (via Anthropic API). For each card, it starts a **fresh conversation** with a system prompt containing the card's details from the DB, and has access to card-server and ebay-mcp tools.

**Per-card conversation lifecycle:**
1. User sends `/next` → orchestrator queries DB for next unlisted card
2. Fresh Claude conversation created with: system prompt + card context + tool definitions
3. Claude decides actions (fetch price, request photo, build listing, etc.)
4. Orchestrator executes tool calls, sends results back to Claude
5. When Claude requests a photo → Telegram message sent to user
6. User sends photo → injected into Claude's current conversation
7. Claude builds listing → orchestrator executes via ebay-mcp
8. Card marked as listed → **conversation discarded** (context reset)

**Context resets per card keep token costs predictable.** No conversation history accumulation across cards. If the program stops mid-card, the DB knows the card's status (e.g., `photo_requested`) and the next conversation can pick up from there.

User free-text messages via Telegram are injected into the active conversation, so you can override decisions ("skip this one", "price it at $5 instead") and Claude reacts naturally.

### Tiered Escalation Model

Not every card needs an LLM. The orchestrator uses a **confidence-based tiered system** where the LLM's involvement scales with the difficulty of the pricing/listing decision. Most cards go through a dumb coded pipeline. As confidence drops, the LLM gets progressively more context and authority.

**Tier 1 — Dumb pipe (majority of cards, 0 tokens):**
Criteria: high confidence (≥80%), high sales volume, high listing count, single clear condition/finish, price under $50.
These are modern NM cards with consistent prices where it's hard to make a mistake. Code handles everything: fetch price → request photo → template listing → post to eBay. No LLM involvement at all.

**Tier 2 — LLM with scoped context (edge cases, ~5,000 tokens):**
Criteria: medium confidence (40-79%), OR low listing count, OR significant sale price variance, OR in-between condition, OR price $50-200.
The LLM receives the already-gathered pricing data (listings, solds, algorithm reasoning) as context and can fetch additional data from less confident sources — eBay last solds, similar card performance, price history trends. It decides the final price and whether to list. Scoped tool set (only pricing/research tools, not full card-server).

**Tier 3 — Full LLM agent (rare cards, ~10,000-15,000 tokens):**
Criteria: low confidence (<40%), OR no sales data, OR no active listings, OR specialty cards (graded, errors), OR price >$200.
Cards with virtually no information. The LLM gets full context, full tool access, and is told to make a judgment call. It can research comparable cards, check eBay solds for similar items, analyze price history, and reason about whether to list at all or hold. May also recommend the card for manual human review via Telegram.

**Future — Tier 2.5: Sell/hold sentiment analysis:**
Every card's price history and market sentiment is analyzed to determine whether it should be sold now or held, and how aggressively to price it. This is a future capability that requires LLM involvement for pattern recognition across price history, set rotation cycles, and market trends.

**Expected distribution:** ~80% Tier 1 (0 tokens), ~15% Tier 2 (~5K tokens), ~5% Tier 3 (~12K tokens).
Estimated cost for 10,000 cards: 0 + 750 × 5K + 500 × 12K = **~9.75M tokens (~$4-8 with Sonnet/Haiku mix).**

### Orchestrator Design Decisions

- **Trigger:** Agent waits for user input (`/next` command) before processing the next card. Does NOT auto-advance.
- **Photo batching:** One card at a time. Agent requests photo, waits for it, then continues.
- **Error recovery:** On eBay/API failure, agent alerts via Telegram and waits for human intervention to retry. No auto-retry.
- **Review UX:** Inline Telegram keyboards for manual review decisions (approve price, override, skip, details).
- **Context persistence:** All durable state lives in SQLite (card-server). Claude's conversation is ephemeral and reset per card. Program can stop/restart without losing progress.
- **Tier routing:** The pricing algorithm's `confidence_percent` and `manual_check_necessary` fields determine which tier a card enters. Tier boundaries are configurable constants.

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
User sends /next via Telegram
        ↓
Orchestrator (code, no LLM yet)
        ↓ calls card-server: get_next_unlisted
card-server → returns card details + condition + finish
        ↓ calls card-server: fetch_prices
card-server → returns price + confidence + reasoning
        ↓
Tier routing (based on confidence_percent + flags)
        ↓                          ↓                          ↓
   Tier 1 (≥80%)            Tier 2 (40-79%)            Tier 3 (<40%)
   Code handles all          LLM gets pricing data      LLM gets full context
   Template listing          + fetches more data         + full tool access
   0 tokens                  ~5K tokens                  ~12K tokens
        ↓                          ↓                          ↓
        └──────────────────────────┴──────────────────────────┘
                                   ↓
                    Telegram: request photo from user
                                   ↓
                    User sends photo via Telegram
                                   ↓
                    Build + post eBay listing (ebay-mcp)
                                   ↓
                    Mark as listed (card-server)
                                   ↓
                    Telegram: send confirmation
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
1. If active listings exist → anchor on lowest listing price
2. If sold data also exists → compare. If they diverge >30% and solds are lower, use sold average instead (listings may be stale). Flag for review. **Exception: cheap cards (under $5) — listings always win** (see below).
3. If no active listings → fall back to sold average
4. If no data at all → null price, flagged as unpriceable
5. Edge case flags: high value (>$50), specialty_two cards (graded/errors), few solds (<3) → lower confidence / manual review

### TCGplayer shipping model for cheap cards (under $5)
TCGplayer offers **free shipping when a buyer spends $5+ with one seller**. If they buy a single cheap card, they pay ~$1 shipping. This has major pricing implications:

- **Listing prices assume free shipping.** The listed price IS the real price. A card listed at $0.25 will sell at $0.25 to a buyer who bundles it with other cards from the same seller. This is how cards sell for 5 cents or less.
- **Sold prices are noisy.** Some buyers paid $1 shipping (single card purchase → sold_price includes shipping), others bundled and paid $0 shipping. The sold price doesn't tell us which happened.
- **For pricing under $5:** Use `listed_price` only — ignore `shipping_price` from the API. The shipping in the API is the single-card rate, but most sales happen through bundling.
- **For divergence checks under $5:** Listings take priority. Don't flag for review or switch to sold average, since the divergence is expected (shipping noise in solds).
- **For liquid value under $5:** Seller shipping cost = $0. The buyer covers shipping via the $5 threshold, or the $1 they pay goes to TCGplayer, not the seller.

### Liquid value formula
```
liquid_value = (sell_price * 0.85) - shipping_cost
```
Shipping tiers:
- **Cards under $5:** $0 (buyer covers via TCGplayer's $5 free shipping threshold)
- **Cards $5-$25:** $1 (PWE / plain white envelope)
- **Cards over $25:** $5 (tracked bubble mailer)

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
7. **Build the Telegram bot module** (`src/telegram/`) — Bot connection, command handlers (`/next`, `/status`, `/skip`, `/pause`), photo handler, inline keyboard support, message renderer for card details/prices.
8. **Build the orchestrator** (`src/agent/`) — Claude API client, per-card conversation management, tool execution bridge to MCP servers, Telegram event handling, system prompt builder from DB state.
9. **Build the eBay listing builder** — Takes an `InventoryDetail` + photos and constructs an eBay listing via `ebay-mcp`.

### Target file structure (parent project)
```
src/
├── telegram/
│   ├── bot.ts              ← TelegramBot wrapper, polling, message routing
│   ├── commands.ts         ← /next, /status, /skip, /pause command handlers
│   ├── handlers.ts         ← Photo handler, free-text → orchestrator, callback (button) handler
│   └── renderer.ts         ← Format card details, prices, status into Telegram messages + inline keyboards
├── agent/
│   ├── orchestrator.ts     ← Event-driven loop: receives Telegram events, dispatches to LLM
│   ├── llm.ts              ← Claude API client, conversation lifecycle (create, turn, reset)
│   ├── tools.ts            ← Tool definitions bridging MCP servers for Claude to call
│   └── systemPrompt.ts     ← Build per-card system prompt from DB state + workflow instructions
└── index.ts                ← Startup: init DB, connect MCP servers, start Telegram bot, start orchestrator
```

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
