/**
 * Unit tests for the Tier 1 Pipeline.
 *
 * Tests the template listing builder (no LLM, no eBay).
 */

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { buildListingTemplate } from '../agent/tier1Pipeline.js';
import type { InventoryDetail, Price } from '../types.js';

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
    estimated_price: 25,
    ...overrides,
  } as InventoryDetail;
}

function makePrice(overrides: Partial<Price> = {}): Price {
  return {
    sku_id: 1,
    calculation_date: '2025-01-01',
    estimated_price: 25,
    estimated_liquid_value: 20.25,
    confidence_percent: 90,
    manual_check_necessary: false,
    manually_checked: false,
    algorithm_version: 'lowest-listing-v1',
    estimated_low_price: 22,
    estimated_high_price: 28,
    estimated_low_price_liquid: null,
    estimated_high_price_liquid: null,
    ...overrides,
  };
}

describe('Tier 1 Pipeline', () => {
  describe('buildListingTemplate', () => {
    it('generates title, description, price, and condition', () => {
      const listing = buildListingTemplate(makeDetail(), makePrice(), '/photos/test.jpg');

      assert.ok(listing.title.includes('Pikachu VMAX'));
      assert.equal(listing.price, 25);
      assert.equal(listing.condition, 'Near Mint');
      assert.equal(listing.photoPath, '/photos/test.jpg');
    });

    it('title includes card number and set', () => {
      const listing = buildListingTemplate(makeDetail(), makePrice(), '/photos/test.jpg');
      assert.ok(listing.title.includes('44/185'));
      assert.ok(listing.title.includes('Vivid Voltage'));
    });

    it('title stays under 80 characters', () => {
      const detail = makeDetail({
        card_name: 'Mega Rayquaza EX Full Art Secret Ultra Rare',
        set_name: 'XY Ancient Origins Extended Special Edition',
        card_number: '98/98',
      });
      const listing = buildListingTemplate(detail, makePrice(), '/photos/test.jpg');
      assert.ok(listing.title.length <= 80, `Title too long: ${listing.title.length} chars`);
    });

    it('description includes card details', () => {
      const listing = buildListingTemplate(makeDetail(), makePrice(), '/photos/test.jpg');
      assert.ok(listing.description.includes('Pikachu VMAX'));
      assert.ok(listing.description.includes('Vivid Voltage'));
      assert.ok(listing.description.includes('NM'));
      assert.ok(listing.description.includes('Holo'));
    });

    it('description includes shipping info', () => {
      const listing = buildListingTemplate(makeDetail(), makePrice(), '/photos/test.jpg');
      assert.ok(listing.description.includes('penny sleeve'));
    });

    it('description includes tags when present', () => {
      const listing = buildListingTemplate(
        makeDetail({ tags: 'hidden crease' }),
        makePrice(),
        '/photos/test.jpg',
      );
      assert.ok(listing.description.includes('hidden crease'));
    });

    it('maps in-between conditions correctly', () => {
      const listing = buildListingTemplate(
        makeDetail({ condition: 'LP-NM' }),
        makePrice(),
        '/photos/test.jpg',
      );
      assert.equal(listing.condition, 'Near Mint');
    });

    it('maps LP condition', () => {
      const listing = buildListingTemplate(
        makeDetail({ condition: 'LP' }),
        makePrice(),
        '/photos/test.jpg',
      );
      assert.equal(listing.condition, 'Lightly Played');
    });

    it('maps DMG condition', () => {
      const listing = buildListingTemplate(
        makeDetail({ condition: 'DMG' }),
        makePrice(),
        '/photos/test.jpg',
      );
      assert.equal(listing.condition, 'Damaged');
    });

    it('includes finish in title for non-Regular', () => {
      const listing = buildListingTemplate(
        makeDetail({ finish: 'Reverse-Holo' }),
        makePrice(),
        '/photos/test.jpg',
      );
      assert.ok(listing.title.includes('Reverse-Holo'));
    });

    it('excludes finish from title for Regular', () => {
      const listing = buildListingTemplate(
        makeDetail({ finish: 'Regular' }),
        makePrice(),
        '/photos/test.jpg',
      );
      assert.ok(!listing.title.includes('Regular'));
    });
  });
});
