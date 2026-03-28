/**
 * Integration tests — real SQLite DB with real card-server services.
 *
 * Tests the full flow: migrate data → get next card → fetch price →
 * tier route → photo → listing, all against a real in-memory DB.
 *
 * Does NOT require Telegram or eBay connections.
 */

import { describe, it, before, after } from 'node:test';
import assert from 'node:assert/strict';
import Database from 'better-sqlite3';
import { readFileSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { EventEmitter } from 'events';

const __dirname = dirname(fileURLToPath(import.meta.url));
const schemaPath = join(__dirname, '..', '..', 'card-server', 'src', 'schema.sql');

/** Get today's date as YYYY-MM-DD. */
function todayStr(): string {
  return new Date().toISOString().split('T')[0];
}

// ─── Shared test DB setup ────────────────────────────────────

function createTestDb(): Database.Database {
  const db = new Database(':memory:');
  db.pragma('journal_mode = WAL');
  db.pragma('foreign_keys = ON');
  const schema = readFileSync(schemaPath, 'utf-8');
  db.exec(schema);
  return db;
}

/** Seed DB with test cards that cover different tiers. */
function seedTestData(db: Database.Database) {
  // Card 1: Modern common — high confidence, cheap (Tier 1)
  db.prepare(`INSERT INTO cards (id, card_name, set_name, product_line, rarity, card_number)
    VALUES ('226432', 'Pikachu VMAX', 'Vivid Voltage', 'Pokemon', 'Ultra Rare', '44/185')`).run();
  db.prepare(`INSERT INTO skus (card_id, condition, finish, specialty_one, specialty_two, qty, latest_calc_date)
    VALUES ('226432', 'NM', 'Holo', 'None', 'None', 1, '${todayStr()}')`).run();
  const sku1 = (db.prepare(`SELECT sku_id FROM skus WHERE card_id = '226432'`).get() as any).sku_id;
  db.prepare(`INSERT INTO inventory (sku_id, qty, status) VALUES (?, 1, 'unlisted')`).run(sku1);
  db.prepare(`INSERT INTO prices (sku_id, calculation_date, estimated_price, estimated_liquid_value, confidence_percent, manual_check_necessary, algorithm_version)
    VALUES (?, '${todayStr()}', 5.00, 4.25, 90, 0, 'lowest-listing-v1')`).run(sku1);

  // Card 2: Mid-value — medium confidence (Tier 2)
  db.prepare(`INSERT INTO cards (id, card_name, set_name, product_line, rarity, card_number)
    VALUES ('42382', 'Charizard', 'Base Set', 'Pokemon', 'Rare Holo', '4/102')`).run();
  db.prepare(`INSERT INTO skus (card_id, condition, finish, specialty_one, specialty_two, qty, latest_calc_date)
    VALUES ('42382', 'LP', 'Holo', 'None', 'None', 1, '${todayStr()}')`).run();
  const sku2 = (db.prepare(`SELECT sku_id FROM skus WHERE card_id = '42382' AND condition = 'LP'`).get() as any).sku_id;
  db.prepare(`INSERT INTO inventory (sku_id, qty, status) VALUES (?, 1, 'unlisted')`).run(sku2);
  db.prepare(`INSERT INTO prices (sku_id, calculation_date, estimated_price, estimated_liquid_value, confidence_percent, manual_check_necessary, algorithm_version)
    VALUES (?, '${todayStr()}', 75.00, 58.75, 60, 1, 'lowest-listing-v1')`).run(sku2);

  // Card 3: Graded specialty (Tier 3)
  db.prepare(`INSERT INTO skus (card_id, condition, finish, specialty_one, specialty_two, qty, latest_calc_date)
    VALUES ('42382', 'NM', 'Holo', 'None', 'PSA 10', 1, '${todayStr()}')`).run();
  const sku3 = (db.prepare(`SELECT sku_id FROM skus WHERE card_id = '42382' AND specialty_two = 'PSA 10'`).get() as any).sku_id;
  db.prepare(`INSERT INTO inventory (sku_id, qty, status) VALUES (?, 1, 'unlisted')`).run(sku3);
  db.prepare(`INSERT INTO prices (sku_id, calculation_date, estimated_price, estimated_liquid_value, confidence_percent, manual_check_necessary, algorithm_version)
    VALUES (?, '${todayStr()}', 500.00, 420.00, 30, 1, 'lowest-listing-v1')`).run(sku3);

  // Card 4: No price data (Tier 3)
  db.prepare(`INSERT INTO cards (id, card_name, set_name, product_line, rarity, card_number)
    VALUES ('99999', 'Mystery Card', 'Unknown Set', 'Pokemon', 'Unknown', '1/1')`).run();
  db.prepare(`INSERT INTO skus (card_id, condition, finish, specialty_one, specialty_two, qty)
    VALUES ('99999', 'NM', 'Regular', 'None', 'None', 1)`).run();
  const sku4 = (db.prepare(`SELECT sku_id FROM skus WHERE card_id = '99999'`).get() as any).sku_id;
  db.prepare(`INSERT INTO inventory (sku_id, qty, status) VALUES (?, 1, 'unlisted')`).run(sku4);
  // No price entry → Tier 3
}

// ─── Import services (after DB setup) ────────────────────────

const { CollectionService } = await import('../../card-server/src/services/collectionService.js');
const { PricingService } = await import('../../card-server/src/services/pricingService.js');
const { routeToTier } = await import('../agent/tierRouter.js');
const { buildListingTemplate } = await import('../agent/tier1Pipeline.js');
const { Orchestrator } = await import('../agent/orchestrator.js');

// ─── Mock Bot for integration tests ─────────────────────────

class MockBot extends EventEmitter {
  messages: string[] = [];
  keyboards: Array<{ text: string; buttons: unknown[][] }> = [];
  expectedPhoto: { inventoryId: number; side: string } | null = null;

  async sendMessage(text: string): Promise<void> { this.messages.push(text); }
  async sendInlineKeyboard(text: string, buttons: unknown[][]): Promise<number> {
    this.keyboards.push({ text, buttons });
    return 1;
  }
  async editMessage(_id: number, text: string): Promise<void> { this.messages.push(text); }
  async sendPhoto(): Promise<void> {}
  setExpectedPhoto(inventoryId: number, side: string): void { this.expectedPhoto = { inventoryId, side }; }
  clearExpectedPhoto(): void { this.expectedPhoto = null; }
  async stop(): Promise<void> {}
  simulateEvent(event: unknown): void { this.emit('event', event); }
}

// ─── Tests ───────────────────────────────────────────────────

describe('Integration: Card-Server Services', () => {
  let db: Database.Database;
  let collection: InstanceType<typeof CollectionService>;
  let pricing: InstanceType<typeof PricingService>;

  before(() => {
    db = createTestDb();
    seedTestData(db);
    collection = new CollectionService(db);
    pricing = new PricingService(db);
  });

  after(() => {
    db.close();
  });

  it('getNextUnlisted returns first unlisted card', () => {
    const detail = collection.getNextUnlisted();
    assert.ok(detail !== null);
    assert.equal(detail!.card_name, 'Pikachu VMAX');
    assert.equal(detail!.status, 'unlisted');
    assert.equal(detail!.condition, 'NM');
    assert.equal(detail!.finish, 'Holo');
  });

  it('getNextUnlisted includes card_id and specialty fields', () => {
    const detail = collection.getNextUnlisted();
    assert.ok(detail !== null);
    assert.equal(detail!.card_id, '226432');
    assert.equal(detail!.specialty_two, 'None');
  });

  it('getLatestPriceForItem returns correct price', () => {
    const detail = collection.getNextUnlisted()!;
    const price = pricing.getLatestPriceForItem(detail.inventory_id);
    assert.ok(price !== null);
    assert.equal(price!.estimated_price, 5);
    assert.equal(price!.confidence_percent, 90);
  });

  it('getStats returns correct counts', () => {
    const stats = collection.getStats();
    assert.equal(stats.total, 4); // 4 inventory items
    assert.equal(stats.by_status['unlisted'], 4);
  });

  it('updateStatus changes card status', () => {
    const detail = collection.getNextUnlisted()!;
    collection.updateStatus(detail.inventory_id, 'photo_requested');
    const updated = collection.getInventoryDetail(detail.inventory_id);
    assert.equal(updated!.status, 'photo_requested');
    // Reset
    collection.updateStatus(detail.inventory_id, 'unlisted');
  });

  it('markAsListed updates status and stores eBay ID', () => {
    const detail = collection.getNextUnlisted()!;
    collection.markAsListed(detail.inventory_id, 'ebay-test-123');
    const updated = collection.getInventoryDetail(detail.inventory_id);
    assert.equal(updated!.status, 'listed');
    assert.equal(updated!.ebay_listing_id, 'ebay-test-123');
  });

  it('getNextUnlisted skips listed cards', () => {
    // Card 1 is now listed, should get card 2
    const detail = collection.getNextUnlisted();
    assert.ok(detail !== null);
    assert.equal(detail!.card_name, 'Charizard');
    assert.equal(detail!.condition, 'LP');
  });
});

describe('Integration: Tier Routing with Real DB', () => {
  let db: Database.Database;
  let collection: InstanceType<typeof CollectionService>;
  let pricing: InstanceType<typeof PricingService>;

  before(() => {
    db = createTestDb();
    seedTestData(db);
    collection = new CollectionService(db);
    pricing = new PricingService(db);
  });

  after(() => {
    db.close();
  });

  it('routes Pikachu VMAX (high confidence, cheap) to Tier 1', () => {
    const items = collection.searchInventoryWithDetails({ status: 'unlisted' });
    const pikachu = items.find(i => i.card_name === 'Pikachu VMAX')!;
    const price = pricing.getLatestPriceForItem(pikachu.inventory_id);
    const result = routeToTier(price, pikachu.specialty_two ?? 'None');
    assert.equal(result.tier, 1);
  });

  it('routes Charizard LP (medium confidence, manual review) to Tier 2', () => {
    const items = collection.searchInventoryWithDetails({ status: 'unlisted' });
    const charizard = items.find(i => i.card_name === 'Charizard' && i.condition === 'LP')!;
    const price = pricing.getLatestPriceForItem(charizard.inventory_id);
    const result = routeToTier(price, charizard.specialty_two ?? 'None');
    assert.equal(result.tier, 2);
  });

  it('routes PSA 10 Charizard (specialty) to Tier 3', () => {
    const items = collection.searchInventoryWithDetails({ status: 'unlisted' });
    const graded = items.find(i => i.specialty_two === 'PSA 10')!;
    const price = pricing.getLatestPriceForItem(graded.inventory_id);
    const result = routeToTier(price, graded.specialty_two ?? 'None');
    assert.equal(result.tier, 3);
  });

  it('routes Mystery Card (no price) to Tier 3', () => {
    const items = collection.searchInventoryWithDetails({ status: 'unlisted' });
    const mystery = items.find(i => i.card_name === 'Mystery Card')!;
    const price = pricing.getLatestPriceForItem(mystery.inventory_id);
    const result = routeToTier(price, mystery.specialty_two ?? 'None');
    assert.equal(result.tier, 3);
  });
});

describe('Integration: Tier 1 Listing Builder with Real DB', () => {
  let db: Database.Database;
  let collection: InstanceType<typeof CollectionService>;
  let pricing: InstanceType<typeof PricingService>;

  before(() => {
    db = createTestDb();
    seedTestData(db);
    collection = new CollectionService(db);
    pricing = new PricingService(db);
  });

  after(() => {
    db.close();
  });

  it('builds a complete listing for a Tier 1 card', () => {
    const detail = collection.getNextUnlisted()!;
    const price = pricing.getLatestPriceForItem(detail.inventory_id)!;

    const listing = buildListingTemplate(detail, price, '/photos/pikachu.jpg');

    assert.ok(listing.title.includes('Pikachu VMAX'));
    assert.equal(listing.price, 5);
    assert.equal(listing.condition, 'Near Mint');
    assert.equal(listing.photoPath, '/photos/pikachu.jpg');
    assert.ok(listing.description.includes('Vivid Voltage'));
  });
});

describe('Integration: Full Workflow with Mock Bot', () => {
  let db: Database.Database;
  let bot: MockBot;
  let orchestrator: InstanceType<typeof Orchestrator>;

  before(() => {
    db = createTestDb();
    seedTestData(db);
    const collection = new CollectionService(db);
    const pricing = new PricingService(db);
    bot = new MockBot();
    orchestrator = new Orchestrator({
      bot: bot as any,
      collectionService: collection,
      pricingService: pricing,
    });
    orchestrator.start();
  });

  after(() => {
    db.close();
  });

  it('full /next → photo → listing flow with Tier 1 card', async () => {
    // Step 1: /next
    bot.simulateEvent({ type: 'command', command: 'next', chatId: 123 });
    await new Promise(r => setTimeout(r, 200));

    // Should have fetched price and sent card summary
    assert.ok(bot.messages.some(m => m.includes('Pikachu VMAX') || m.includes('Fetching')),
      `Expected card summary, got: ${bot.messages.join(' | ')}`);

    // Step 2: Send photo
    bot.simulateEvent({ type: 'photo', filePath: '/photos/test.jpg', chatId: 123 });
    await new Promise(r => setTimeout(r, 200));

    // Should have completed the listing
    assert.ok(bot.messages.some(m => m.includes('Listing created') || m.includes('listing')),
      `Expected listing confirmation, got: ${bot.messages.join(' | ')}`);
    assert.ok(bot.messages.some(m => m.includes('/next')),
      'Should prompt for next card');

    // Verify DB state
    const detail = db.prepare(`SELECT status, ebay_listing_id FROM inventory WHERE inventory_id = 1`).get() as any;
    assert.equal(detail.status, 'listed');
    assert.ok(detail.ebay_listing_id.startsWith('ebay-stub-'));
  });

  it('second /next skips the listed card and gets Charizard', async () => {
    bot.messages = [];
    bot.keyboards = [];

    bot.simulateEvent({ type: 'command', command: 'next', chatId: 123 });
    await new Promise(r => setTimeout(r, 200));

    // Should get Charizard (Tier 2)
    assert.ok(bot.messages.some(m => m.includes('Charizard') || m.includes('Tier')),
      `Expected Charizard, got: ${bot.messages.join(' | ')}`);
  });
});
