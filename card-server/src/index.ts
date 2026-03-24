import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { z } from 'zod';
import { initDatabase, closeDatabase } from './db.js';
import { CollectionService } from './services/collectionService.js';
import { PricingService } from './services/pricingService.js';
import {
  CreateCardInput, UpdateCardInput, SearchCardsInput,
  CreateSkuInput, SearchSkusInput,
  SearchInventoryInput, UpdateInventoryInput,
  CreatePriceInput, SearchPricesInput,
  CardCondition, CardFinish,
} from './types.js';

// ─── Server Setup ────────────────────────────────────────────

const server = new McpServer({
  name: 'card-server',
  version: '1.0.0',
  description: 'Combined collection, card info, and pricing MCP server for TCG cards',
});

const db = initDatabase();
const collection = new CollectionService(db);
const pricing = new PricingService(db);

function ok(data: unknown) {
  return { content: [{ type: 'text' as const, text: JSON.stringify(data, null, 2) }] };
}

function err(msg: string) {
  return { content: [{ type: 'text' as const, text: msg }], isError: true as const };
}

// ─── Card Tools ──────────────────────────────────────────────

server.tool('get_card', 'Get a card by TCGplayer ID (auto-fetches from TCGplayer if not in DB)',
  { id: z.string() },
  async ({ id }) => {
    const card = await collection.getCard(id);
    return card ? ok(card) : err(`Card ${id} not found`);
  },
);

server.tool('search_cards', 'Search cards by name, set, rarity, etc.',
  SearchCardsInput.shape,
  async (params) => ok(collection.searchCards(params)),
);

server.tool('update_card', 'Update card metadata',
  UpdateCardInput.shape,
  async (params) => {
    const card = collection.updateCard(params);
    return card ? ok(card) : err(`Card ${params.id} not found`);
  },
);

server.tool('bulk_import_cards', 'Bulk import/update cards from an array',
  { cards: z.array(CreateCardInput) },
  async ({ cards }) => ok({ imported: collection.bulkImportCards(cards) }),
);

server.tool('list_sets', 'Get all distinct set names', {},
  async () => ok(collection.listSets()),
);

server.tool('list_rarities', 'Get all distinct rarities', {},
  async () => ok(collection.listRarities()),
);

// ─── SKU Tools ───────────────────────────────────────────────

server.tool('resolve_sku', 'Get or create a SKU for a card+condition+finish+specialty combo',
  CreateSkuInput.shape,
  async (params) => ok(collection.resolveSku(params)),
);

server.tool('get_skus_for_card', 'Get all SKU variants for a card',
  { card_id: z.string() },
  async ({ card_id }) => ok(collection.getSkusForCard(card_id)),
);

server.tool('search_skus', 'Search SKUs with filters',
  SearchSkusInput.shape,
  async (params) => ok(collection.searchSkus(params)),
);

server.tool('list_conditions', 'Get all card conditions in use', {},
  async () => ok(collection.listConditions()),
);

server.tool('list_specialties', 'Get all specialty values in use', {},
  async () => ok(collection.listSpecialties()),
);

// ─── Inventory Tools ─────────────────────────────────────────

server.tool('add_to_inventory', 'Add a card to your physical inventory (resolves SKU automatically)',
  {
    card_id: z.string(),
    condition: CardCondition,
    finish: CardFinish.optional(),
    specialty_one: z.string().optional(),
    specialty_two: z.string().optional(),
    pricing_condition: CardCondition.optional(),
    pricing_finish: CardFinish.optional(),
    qty: z.number().optional(),
    tags: z.string().optional(),
  },
  async (params) => ok(collection.addToInventory(params)),
);

server.tool('get_inventory_item', 'Get an inventory item with full card/price details',
  { inventory_id: z.number() },
  async ({ inventory_id }) => {
    const item = collection.getInventoryDetail(inventory_id);
    return item ? ok(item) : err(`Inventory item ${inventory_id} not found`);
  },
);

server.tool('get_next_unlisted', 'Get the next unlisted card for the listing workflow', {},
  async () => {
    const item = collection.getNextUnlisted();
    return item ? ok(item) : err('No unlisted cards remaining');
  },
);

server.tool('search_inventory', 'Search inventory with filters',
  SearchInventoryInput.shape,
  async (params) => ok(collection.searchInventory(params)),
);

server.tool('search_inventory_detailed', 'Search inventory with full card/price details',
  {
    status: z.string().optional(),
    card_id: z.string().optional(),
    limit: z.number().optional(),
    offset: z.number().optional(),
  },
  async (params) => ok(collection.searchInventoryWithDetails(params)),
);

server.tool('update_inventory', 'Update an inventory item',
  UpdateInventoryInput.shape,
  async (params) => {
    const item = collection.updateInventory(params);
    return item ? ok(item) : err(`Inventory item ${params.inventory_id} not found`);
  },
);

server.tool('mark_as_listed', 'Mark an inventory item as listed on eBay',
  { inventory_id: z.number(), ebay_listing_id: z.string() },
  async ({ inventory_id, ebay_listing_id }) => {
    const item = collection.markAsListed(inventory_id, ebay_listing_id);
    return item ? ok(item) : err(`Inventory item ${inventory_id} not found`);
  },
);

server.tool('mark_as_sold', 'Mark an inventory item as sold',
  { inventory_id: z.number() },
  async ({ inventory_id }) => {
    const item = collection.markAsSold(inventory_id);
    return item ? ok(item) : err(`Inventory item ${inventory_id} not found`);
  },
);

server.tool('update_status', 'Update inventory item status',
  { inventory_id: z.number(), status: z.string() },
  async ({ inventory_id, status }) => {
    const item = collection.updateStatus(inventory_id, status);
    return item ? ok(item) : err(`Inventory item ${inventory_id} not found`);
  },
);

server.tool('set_pricing_sku', 'Override the pricing SKU for borderline condition cards',
  { inventory_id: z.number(), pricing_sku_id: z.number().nullable() },
  async ({ inventory_id, pricing_sku_id }) => {
    const item = collection.setPricingSku(inventory_id, pricing_sku_id);
    return item ? ok(item) : err(`Inventory item ${inventory_id} not found`);
  },
);

server.tool('add_tag', 'Add a hidden tag to an inventory item',
  { inventory_id: z.number(), tag: z.string() },
  async ({ inventory_id, tag }) => {
    const item = collection.addTag(inventory_id, tag);
    return item ? ok(item) : err(`Inventory item ${inventory_id} not found`);
  },
);

server.tool('remove_tag', 'Remove a tag from an inventory item',
  { inventory_id: z.number(), tag: z.string() },
  async ({ inventory_id, tag }) => {
    const item = collection.removeTag(inventory_id, tag);
    return item ? ok(item) : err(`Inventory item ${inventory_id} not found`);
  },
);

server.tool('collection_stats', 'Get collection statistics (counts by status, total value)', {},
  async () => ok(collection.getStats()),
);

// ─── Pricing Tools ───────────────────────────────────────────

server.tool('get_latest_price', 'Get the most recent price for a SKU',
  { sku_id: z.number() },
  async ({ sku_id }) => {
    const price = pricing.getLatestPrice(sku_id);
    return price ? ok(price) : err(`No price found for SKU ${sku_id}`);
  },
);

server.tool('get_price_for_item', 'Get latest price for an inventory item (respects pricing SKU override)',
  { inventory_id: z.number() },
  async ({ inventory_id }) => {
    const price = pricing.getLatestPriceForItem(inventory_id);
    return price ? ok(price) : err(`No price found for inventory item ${inventory_id}`);
  },
);

server.tool('get_price_history', 'Get price history for a SKU',
  { sku_id: z.number(), limit: z.number().optional(), offset: z.number().optional() },
  async ({ sku_id, limit, offset }) => ok(pricing.getPriceHistory(sku_id, limit, offset)),
);

server.tool('search_prices', 'Search prices with filters',
  SearchPricesInput.shape,
  async (params) => ok(pricing.searchPrices(params)),
);

server.tool('compare_prices', 'Compare prices for a card across all conditions',
  { card_id: z.string() },
  async ({ card_id }) => ok(pricing.comparePricesAcrossConditions(card_id)),
);

server.tool('record_price', 'Manually record a price estimate for a SKU',
  CreatePriceInput.shape,
  async (params) => ok(pricing.recordPrice(params)),
);

server.tool('bulk_import_prices', 'Bulk import price records',
  { prices: z.array(CreatePriceInput) },
  async ({ prices }) => ok({ imported: pricing.bulkImportPrices(prices) }),
);

server.tool('get_pending_manual_checks', 'Get prices flagged for manual review', {},
  async () => ok(pricing.getPendingManualChecks()),
);

server.tool('mark_price_checked', 'Mark a price as manually verified',
  { sku_id: z.number(), calculation_date: z.string() },
  async ({ sku_id, calculation_date }) => {
    const updated = pricing.markPriceChecked(sku_id, calculation_date);
    return updated ? ok({ success: true }) : err('Price record not found');
  },
);

// ─── TCGplayer Fetch Tools (active data gathering) ───────────

server.tool('fetch_prices', 'Fetch fresh prices from TCGplayer for all conditions and store them (includes algorithm reasoning)',
  { tcgplayer_id: z.string() },
  async ({ tcgplayer_id }) => {
    const prices = await pricing.fetchAndStorePrices(tcgplayer_id);
    return ok({ fetched: prices.length, prices });
  },
);

server.tool('compute_price', 'Fetch data and compute price for a specific card+condition+finish (includes algorithm reasoning)',
  { tcgplayer_id: z.string(), condition: z.string(), finish: z.string() },
  async ({ tcgplayer_id, condition, finish }) => {
    const result = await pricing.computeAndStorePrice(tcgplayer_id, condition, finish);
    return result ? ok(result) : err('Could not compute price — no data available');
  },
);

server.tool('fetch_sold_listings', 'Fetch recent sold listings from TCGplayer for price analysis',
  { tcgplayer_id: z.string(), condition: z.string().optional(), finish: z.string().optional() },
  async ({ tcgplayer_id, condition, finish }) => {
    const listings = await pricing.fetchSoldListingsData(tcgplayer_id, condition, finish);
    return ok(listings);
  },
);

server.tool('fetch_active_listings', 'Fetch current for-sale listings from TCGplayer for competitive pricing',
  { tcgplayer_id: z.string(), condition: z.string().optional(), finish: z.string().optional() },
  async ({ tcgplayer_id, condition, finish }) => {
    const listings = await pricing.fetchActiveListingsData(tcgplayer_id, condition, finish);
    return ok(listings);
  },
);

// ─── Start Server ────────────────────────────────────────────

async function main() {
  const transport = new StdioServerTransport();
  await server.connect(transport);

  process.on('SIGINT', () => {
    closeDatabase();
    process.exit(0);
  });
}

main().catch((err) => {
  console.error('Failed to start card-server:', err);
  process.exit(1);
});
