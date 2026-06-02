# Python Conversion Plan

> **Status: COMPLETE.** The TypeScript codebase has been fully converted to Python.
> `card_server`, `telegram_service`, `ebay_service`, `orchestrator`, and `shared` are
> installable packages mapped in `pyproject.toml` (`pip install -e .`). The React
> frontend remains TypeScript. This document is retained as the historical file map.

Convert the TypeScript codebase to Python to unify the project language. The dashboard backend is already Python. The Anthropic SDK (for Tier 2/3 LLM) is more natural in Python.

## Conversion Order

Convert bottom-up: services first (no dependencies on each other), then orchestrator (depends on services), then entry point.

### 1. card-server (services/card-server/) — Largest, most standalone

**TypeScript → Python mapping:**

| TypeScript | Python |
|---|---|
| `better-sqlite3` (sync) | `sqlite3` (stdlib, sync) |
| Zod schemas | Pydantic models |
| `fetch()` / node HTTP | `httpx` (async) or `requests` (sync) |
| `@modelcontextprotocol/sdk` | `mcp` Python SDK |
| Class-based services | Class-based services (same pattern) |
| ES module imports | Standard Python imports |

**Files to convert (in dependency order):**
1. `types.ts` → `types.py` (Pydantic models for Card, Sku, InventoryItem, Price, etc.)
2. `db.ts` → `db.py` (sqlite3 init/close, same pattern)
3. `schema.sql` → stays as-is (SQL is language-agnostic)
4. `helpers/crud/*.ts` → `helpers/crud/*.py` (4 files, mechanical port)
5. `helpers/pricing/pricingConfig.ts` → `helpers/pricing/config.py` (constants)
6. `helpers/pricing/algorithm.ts` → `helpers/pricing/algorithm.py` (pure math, direct port)
7. `helpers/tcgplayer/formatters.ts` → `helpers/tcgplayer/formatters.py` (string mappings)
8. `helpers/tcgplayer/fetchPrices.ts` → `helpers/tcgplayer/fetch_prices.py` (HTTP calls)
9. `helpers/tcgplayer/fetchCardInfo.ts` → `helpers/tcgplayer/fetch_card_info.py` (HTTP calls)
10. `helpers/market/*.ts` → `helpers/market/*.py` (4 files)
11. `services/collectionService.ts` → `services/collection_service.py`
12. `services/pricingService.ts` → `services/pricing_service.py`
13. `index.ts` → `index.py` (MCP server entry — use Python MCP SDK)
14. `collect.ts` → `collect.py` (CLI runner)

**Estimated effort:** ~2000 lines of logic. The mapping is mechanical — no TypeScript-specific patterns.

**Package management:** Create `pyproject.toml` or `requirements.txt`. Dependencies: `pydantic`, `httpx`, `mcp`.

### 2. telegram (services/telegram/) — Small, 2 files

**TypeScript → Python mapping:**

| TypeScript | Python |
|---|---|
| `node-telegram-bot-api` | `python-telegram-bot` |
| EventEmitter | Python `asyncio` events or callbacks |

**Files to convert:**
1. `bot.ts` → `bot.py` (Bot class, message handlers, photo download)
2. `renderer.ts` → `renderer.py` (string formatting, pure functions)

**Estimated effort:** ~400 lines. `python-telegram-bot` has a different async API pattern, so bot.ts needs some restructuring, not just mechanical translation.

### 3. ebay (services/ebay/) — Stub, trivial

**Files to convert:**
1. `index.ts` → `__init__.py` or `service.py` (ListingTemplate dataclass, postToEbay stub)

**Estimated effort:** ~30 lines.

### 4. orchestrator (dashboard/backend/orchestrator/) — Medium

**TypeScript → Python mapping:**

| TypeScript | Python |
|---|---|
| Class with async methods | Class with async methods (same) |
| Claude API (`@anthropic-ai/sdk`) | `anthropic` Python SDK |

**Files to convert:**
1. `tierRouter.ts` → `tier_router.py` (pure logic, direct port)
2. `tier1Pipeline.ts` → `tier1_pipeline.py` (template builder, direct port)
3. `orchestrator.ts` → `orchestrator.py` (state machine, main logic)
4. `llm.ts` → `llm.py` (stub → implement with `anthropic` SDK)
5. `tools.ts` → `tools.py` (stub → implement tool schemas)
6. `systemPrompt.ts` → `system_prompt.py` (stub → implement prompt builder)

**Estimated effort:** ~600 lines. The LLM stubs can be implemented directly in Python rather than porting pseudocode.

### 5. Entry point (src/) — Small

**Files to convert:**
1. `types.ts` → integrate into a shared types module (or each service defines its own)
2. `index.ts` → `main.py` or integrate into dashboard backend startup

**Decision:** After conversion, `src/` may no longer be needed. The dashboard backend can be the entry point that starts both the Flask API and the orchestrator.

## Post-Conversion Cleanup

- [x] Remove `package.json`, `tsconfig.json`, `node_modules/` from root and card-server
- [x] Remove `dist/` build artifacts
- [x] Update `.gitignore` (add `__pycache__/`, `*.pyc`, `.venv/`, `*.egg-info/`)
- [x] Create top-level `pyproject.toml` (declares deps + package-dir mapping)
- [x] Update `CLAUDE.md` tech stack section
- [x] Update command equivalents (see CLAUDE.md Commands — `python -m ...`)
- [x] Verify dashboard backend / orchestrator can import `card_server` services directly (language boundary resolved)

## Risks

- **python-telegram-bot** has a different async model than `node-telegram-bot-api`. The Bot class will need restructuring, not just line-by-line translation.
- **MCP Python SDK** may have API differences from the TypeScript SDK. Need to verify tool registration pattern.
- **Tests** need rewriting in pytest. The test patterns should port cleanly but the test runner setup changes.
- **better-sqlite3 is synchronous, Python's sqlite3 is also synchronous** — this is a clean match. No async complications.
