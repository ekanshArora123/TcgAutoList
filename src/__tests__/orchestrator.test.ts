/**
 * Unit tests for the Orchestrator.
 *
 * Uses mock Bot and mock services to test the event loop logic
 * without needing a real Telegram connection or database.
 */

import { describe, it, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'events';
import { Orchestrator } from '../agent/orchestrator.js';
import type { TelegramEvent, InventoryDetail, Price } from '../types.js';

// ─── Mock Bot ────────────────────────────────────────────────

class MockBot extends EventEmitter {
  messages: string[] = [];
  keyboards: Array<{ text: string; buttons: unknown[][] }> = [];
  expectedPhoto: { inventoryId: number; side: string } | null = null;

  async sendMessage(text: string, _parseMode?: string): Promise<void> {
    this.messages.push(text);
  }

  async sendInlineKeyboard(text: string, buttons: unknown[][], _parseMode?: string): Promise<number> {
    this.keyboards.push({ text, buttons });
    return 1;
  }

  async editMessage(_messageId: number, text: string, _parseMode?: string): Promise<void> {
    this.messages.push(`[edit] ${text}`);
  }

  async sendPhoto(_filePath: string, _caption?: string): Promise<void> {}

  setExpectedPhoto(inventoryId: number, side: string): void {
    this.expectedPhoto = { inventoryId, side };
  }

  clearExpectedPhoto(): void {
    this.expectedPhoto = null;
  }

  async stop(): Promise<void> {}

  /** Simulate a Telegram event. */
  simulateEvent(event: TelegramEvent): void {
    this.emit('event', event);
  }
}

// ─── Mock Services ───────────────────────────────────────────

function makeDetail(overrides: Partial<InventoryDetail> = {}): InventoryDetail {
  return {
    inventory_id: 1,
    sku_id: 1,
    pricing_sku_id: null,
    qty: 1,
    tags: null,
    status: 'unlisted',
    ebay_listing_id: null,
    listed_at: null,
    card_id: '226432',
    card_name: 'Pikachu VMAX',
    set_name: 'Vivid Voltage',
    condition: 'NM',
    finish: 'Holo',
    specialty_one: 'None',
    specialty_two: 'None',
    rarity: 'Ultra Rare',
    card_number: '44/185',
    estimated_price: 5,
    ...overrides,
  } as InventoryDetail;
}

function makePrice(overrides: Partial<Price> = {}): Price {
  return {
    sku_id: 1,
    calculation_date: '2025-01-01',
    estimated_price: 5,
    estimated_liquid_value: 4.25,
    confidence_percent: 90,
    manual_check_necessary: false,
    manually_checked: false,
    algorithm_version: 'lowest-listing-v1',
    estimated_low_price: 4.5,
    estimated_high_price: 5.5,
    estimated_low_price_liquid: null,
    estimated_high_price_liquid: null,
    ...overrides,
  };
}

class MockCollectionService {
  nextUnlisted: InventoryDetail | null = makeDetail();
  statuses: Array<{ id: number; status: string }> = [];
  listings: Array<{ id: number; ebayId: string }> = [];

  getNextUnlisted(): InventoryDetail | null {
    return this.nextUnlisted;
  }

  getSkusForCard(_cardId: string) {
    return [{ sku_id: 1, card_id: '226432', condition: 'NM', finish: 'Holo', specialty_one: 'None', specialty_two: 'None', qty: 1, latest_calc_date: null }];
  }

  searchSkus(_filters: unknown) {
    return [{ sku_id: 1, card_id: '226432', condition: 'NM', finish: 'Holo', specialty_one: 'None', specialty_two: 'None', qty: 1, latest_calc_date: null }];
  }

  searchInventory(_filters: unknown) {
    return [{ inventory_id: 1, sku_id: 1 }];
  }

  updateStatus(inventoryId: number, status: string) {
    this.statuses.push({ id: inventoryId, status });
    return { inventory_id: inventoryId, status } as any;
  }

  markAsListed(inventoryId: number, ebayListingId: string) {
    this.listings.push({ id: inventoryId, ebayId: ebayListingId });
    return { inventory_id: inventoryId, ebay_listing_id: ebayListingId } as any;
  }

  getStats() {
    return { total: 100, by_status: { unlisted: 95, listed: 5 }, total_estimated_value: 500 };
  }

  getInventoryDetail(inventoryId: number) {
    return this.nextUnlisted;
  }
}

class MockPricingService {
  priceToReturn: (Price & { reasoning: string }) | null = { ...makePrice(), reasoning: 'Test reasoning' };

  async computeAndStorePrice(_tcgplayerId: string, _condition: string, _finish: string) {
    return this.priceToReturn;
  }

  getLatestPriceForItem(_inventoryId: number): Price | null {
    return this.priceToReturn;
  }
}

// ─── Tests ───────────────────────────────────────────────────

describe('Orchestrator', () => {
  let bot: MockBot;
  let collection: MockCollectionService;
  let pricing: MockPricingService;
  let orchestrator: Orchestrator;

  beforeEach(() => {
    bot = new MockBot();
    collection = new MockCollectionService();
    pricing = new MockPricingService();
    orchestrator = new Orchestrator({
      bot: bot as any,
      collectionService: collection as any,
      pricingService: pricing as any,
    });
    orchestrator.start();
  });

  it('handles /next command — gets next card and requests photo', async () => {
    bot.simulateEvent({ type: 'command', command: 'next', chatId: 123 });

    // Wait for async processing
    await new Promise(r => setTimeout(r, 100));

    // Should have sent messages about fetching price and card summary
    assert.ok(bot.messages.some(m => m.includes('Fetching price')));
    // Should have requested photo (sent inline keyboard)
    assert.ok(bot.keyboards.length > 0 || bot.messages.some(m => m.includes('Tier')));
    // Should have updated status to photo_requested
    assert.ok(collection.statuses.some(s => s.status === 'photo_requested'));
  });

  it('handles /next when no cards left', async () => {
    collection.nextUnlisted = null;
    bot.simulateEvent({ type: 'command', command: 'next', chatId: 123 });

    await new Promise(r => setTimeout(r, 100));

    assert.ok(bot.messages.some(m => m.includes('No more unlisted cards')));
  });

  it('handles /status command', async () => {
    bot.simulateEvent({ type: 'command', command: 'status', chatId: 123 });

    await new Promise(r => setTimeout(r, 100));

    assert.ok(bot.messages.some(m => m.includes('100') || m.includes('Status')));
  });

  it('handles /skip when no card is active', async () => {
    bot.simulateEvent({ type: 'command', command: 'skip', chatId: 123 });

    await new Promise(r => setTimeout(r, 100));

    assert.ok(bot.messages.some(m => m.includes('No card to skip')));
  });

  it('handles /skip during active card processing', async () => {
    // Start processing a card
    bot.simulateEvent({ type: 'command', command: 'next', chatId: 123 });
    await new Promise(r => setTimeout(r, 100));

    // Skip it
    bot.simulateEvent({ type: 'command', command: 'skip', chatId: 123 });
    await new Promise(r => setTimeout(r, 100));

    assert.ok(collection.statuses.some(s => s.status === 'skipped'));
    assert.ok(bot.messages.some(m => m.includes('Skipped')));
  });

  it('handles /pause toggle', async () => {
    bot.simulateEvent({ type: 'command', command: 'pause', chatId: 123 });
    await new Promise(r => setTimeout(r, 100));
    assert.ok(bot.messages.some(m => m.includes('paused')));

    // Should reject /next while paused
    bot.messages = [];
    bot.simulateEvent({ type: 'command', command: 'next', chatId: 123 });
    await new Promise(r => setTimeout(r, 100));
    assert.ok(bot.messages.some(m => m.includes('paused')));

    // Unpause
    bot.messages = [];
    bot.simulateEvent({ type: 'command', command: 'pause', chatId: 123 });
    await new Promise(r => setTimeout(r, 100));
    assert.ok(bot.messages.some(m => m.includes('resumed')));
  });

  it('handles photo received — completes Tier 1 listing', async () => {
    // Start a card
    bot.simulateEvent({ type: 'command', command: 'next', chatId: 123 });
    await new Promise(r => setTimeout(r, 100));

    // Send photo
    bot.simulateEvent({ type: 'photo', filePath: '/photos/test.jpg', chatId: 123 });
    await new Promise(r => setTimeout(r, 100));

    // Should have created a listing
    assert.ok(collection.listings.length > 0, 'Should have called markAsListed');
    assert.ok(bot.messages.some(m => m.includes('Listing created') || m.includes('listing')));
  });

  it('rejects photo when not expected', async () => {
    bot.simulateEvent({ type: 'photo', filePath: '/photos/test.jpg', chatId: 123 });
    await new Promise(r => setTimeout(r, 100));

    assert.ok(bot.messages.some(m => m.includes('Not expecting a photo')));
  });

  it('routes low-confidence cards to Tier 3', async () => {
    pricing.priceToReturn = {
      ...makePrice({ confidence_percent: 20, estimated_price: 10 }),
      reasoning: 'Low confidence test',
    };

    bot.simulateEvent({ type: 'command', command: 'next', chatId: 123 });
    await new Promise(r => setTimeout(r, 100));

    assert.ok(bot.messages.some(m => m.includes('Tier') && m.includes('3')));
  });

  it('prevents double /next', async () => {
    bot.simulateEvent({ type: 'command', command: 'next', chatId: 123 });
    await new Promise(r => setTimeout(r, 100));

    bot.messages = [];
    bot.simulateEvent({ type: 'command', command: 'next', chatId: 123 });
    await new Promise(r => setTimeout(r, 100));

    assert.ok(bot.messages.some(m => m.includes('Already processing')));
  });
});
