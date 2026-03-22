# TcgAutoList — Agentic Pokemon Card eBay Listing System

## Project Overview

TcgAutoList automates listing ~10,000 Pokemon cards on eBay. It uses an **agentic architecture** with MCP servers (Model Context Protocol) and a Telegram bot for photo intake.

**Workflow:** Agent picks next unlisted card → requests photos via Telegram → waits for photos → gathers card info from API → builds listing → creates eBay listing → marks as done → repeats.

## Architecture

### Components

| Component | Type | Location | Purpose |
|-----------|------|----------|---------|
| `collectionServer` | MCP Server | `src/servers/collection/` | SQLite DB of user's card collection with CRUD tools |
| `cardInfoServer` | MCP Server | `src/servers/cardinfo/` | Fetches card metadata from Pokemon TCG API + pricing |
| `ebay-mcp` | MCP Server (external) | npm package | eBay Sell API access (325 tools) |
| `telegram/bot` | Module | `src/telegram/` | Sends photo requests & receives photos via Telegram Bot API |
| `listingAgent` | Orchestrator | `src/agent/` | State machine driving the card → listing workflow |

### MCP Server Pattern

Each custom MCP server follows this structure:
- `index.ts` — Server setup, tool registration, STDIO transport
- `db.ts` or API wrapper — Data layer
- `types.ts` — Zod schemas for validation
- `__tests__/` — Unit tests

Servers communicate via **STDIO transport** using `@modelcontextprotocol/sdk`.

### Data Flow

```
collectionServer (pick card)
        ↓ tcgplayer_id + condition
Telegram bot (request & receive photos)
        ↓ photo file paths
cardInfoServer (get name, set, rarity, price)
        ↓ card info + price estimate
ebay-mcp (create inventory item + offer)
        ↓ listing ID
collectionServer (mark as listed)
```

## Tech Stack

- **Runtime:** Node.js with TypeScript (ES2022 target)
- **MCP SDK:** `@modelcontextprotocol/sdk` — server & client implementations
- **Database:** SQLite via `better-sqlite3` — local, zero-config, single-file DB
- **Card Data API:** Pokemon TCG API (`pokemontcg.io`) — free, open, JSON
- **Telegram:** `node-telegram-bot-api` — bot for photo request/receive
- **eBay:** `ebay-mcp` — open-source MCP server wrapping eBay Sell APIs
- **Validation:** Zod — runtime type checking for tool inputs/outputs
- **Config:** dotenv — environment variable management

## Key Concepts (Educational Reference)

### MCP Servers
MCP servers expose **tools** (functions an agent can call) and **resources** (data an agent can read). They communicate over STDIO or HTTP. Each server is a separate process.

### MCP Sampling (Future)
Sampling lets an MCP **server** request an LLM completion from the **client**. This inverts the usual flow — instead of the agent calling the server, the server asks the agent's LLM for help. Use cases here:
- Pricing analysis (cardInfoServer asks LLM to evaluate multiple price sources)
- Listing description generation (cardInfoServer asks LLM to write compelling copy)
- Quality review (agent asks LLM to review listing before submission)

**Current design:** Sampling hooks exist as `SamplingHook<TInput, TOutput>` interfaces with `samplingEnabled: boolean`. Set to `false` now; flip to `true` and implement `sampling/createMessage` later.

### Agent Orchestrator
The orchestrator is a **state machine** with states:
`IDLE → PICK_CARD → REQUEST_PHOTOS → WAIT_PHOTOS → GATHER_INFO → BUILD_LISTING → CREATE_LISTING → MARK_LISTED → PICK_CARD`

Each state has entry actions, exit conditions, error handlers, and sampling hooks.

## Database Schema

```sql
-- cards table in collection.db
CREATE TABLE cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tcgplayer_id TEXT UNIQUE NOT NULL,
    condition TEXT NOT NULL,          -- NM, LP, MP, HP, DMG
    special_notes TEXT,               -- hidden details, play wear
    status TEXT DEFAULT 'unlisted',   -- unlisted, photo_requested, listed, skipped, sold
    ebay_listing_id TEXT,
    listed_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

## Environment Variables

Required in `.env`:
```
TELEGRAM_BOT_TOKEN=       # From @BotFather
TELEGRAM_CHAT_ID=         # Your personal chat ID
EBAY_CLIENT_ID=           # eBay developer credentials
EBAY_CLIENT_SECRET=       # eBay developer credentials
EBAY_ENVIRONMENT=sandbox  # 'sandbox' or 'production'
POKEMON_TCG_API_KEY=      # Optional, for higher rate limits
```

## Commands

```bash
npm run build          # Compile TypeScript
npm run start          # Start the listing workflow
npm run status         # Show collection stats
npm run import <file>  # Import cards from CSV/JSON
npm run server:collection  # Run collectionServer standalone
npm run server:cardinfo    # Run cardInfoServer standalone
npm run test           # Run all tests
```

## Design Principles

1. **Sampling-ready:** Every decision point has a `SamplingHook` interface. Current implementation uses templates/rules; future versions use LLM sampling.
2. **Data source abstraction:** Card data and pricing use provider interfaces (`PriceProvider`, etc.) so sources can be swapped without changing the orchestrator.
3. **Idempotent operations:** Re-running the agent skips already-listed cards. No duplicate listings.
4. **Auditable:** Every state transition and tool call is logged with timestamps.
5. **Graceful degradation:** If the Pokemon TCG API is down, the agent skips info gathering and asks you via Telegram. If eBay fails, it queues the card for retry.

## File Structure

```
TcgAutoList/
├── src/
│   ├── servers/
│   │   ├── collection/    # collectionServer MCP
│   │   └── cardinfo/      # cardInfoServer MCP
│   ├── telegram/          # Telegram bot module
│   ├── agent/             # Orchestrator
│   ├── ebay/              # eBay adapter
│   ├── config.ts          # Central config
│   └── index.ts           # CLI entry point
├── data/                  # SQLite database
├── photos/                # Downloaded card photos
├── claude.md              # This file
├── .env.example
├── package.json
└── tsconfig.json
```

## Common Tasks for AI Assistants

- **Adding a new MCP tool:** Add tool definition in the server's `index.ts`, implement handler, add Zod schema in `types.ts`, write test.
- **Changing the pricing strategy:** Implement `PriceProvider` interface in `src/servers/cardinfo/pricing.ts`.
- **Enabling sampling at a hook point:** Set `samplingEnabled: true` on the hook, implement `sampling/createMessage` call in the hook's `process()` method.
- **Adding a new agent state:** Add state to the enum in `state.ts`, implement entry/exit/error in `orchestrator.ts`.
