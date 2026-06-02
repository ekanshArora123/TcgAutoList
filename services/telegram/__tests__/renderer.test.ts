/**
 * Unit tests for the Telegram message renderer.
 */

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import {
  renderCardDetails, renderPriceInfo, renderCardSummary,
  renderPhotoRequest, renderListingConfirmation,
  buildReviewKeyboard, buildPhotoRequestKeyboard,
} from '../renderer.js';
import type { InventoryDetail, Price } from '../../../src/types.js';

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
    card_id: '42382',
    card_name: 'Charizard',
    set_name: 'Base Set',
    condition: 'NM',
    finish: 'Holo',
    specialty_one: 'None',
    specialty_two: 'None',
    rarity: 'Rare Holo',
    card_number: '4/102',
    estimated_price: 650,
    ...overrides,
  } as InventoryDetail;
}

function makePrice(overrides: Partial<Price> = {}): Price {
  return {
    sku_id: 1,
    calculation_date: '2025-01-01',
    estimated_price: 650,
    estimated_liquid_value: 547.5,
    confidence_percent: 85,
    manual_check_necessary: true,
    manually_checked: false,
    algorithm_version: 'lowest-listing-v1',
    estimated_low_price: 580,
    estimated_high_price: 750,
    estimated_low_price_liquid: 488,
    estimated_high_price_liquid: 632.5,
    ...overrides,
  };
}

describe('Renderer', () => {
  describe('renderCardDetails', () => {
    it('includes card name, set, condition, finish', () => {
      const detail = makeDetail();
      const result = renderCardDetails(detail);
      assert.ok(result.includes('Charizard'));
      assert.ok(result.includes('Base Set'));
      assert.ok(result.includes('NM'));
      assert.ok(result.includes('Holo'));
    });

    it('includes rarity when present', () => {
      const result = renderCardDetails(makeDetail());
      assert.ok(result.includes('Rare Holo'));
    });

    it('includes tags when present', () => {
      const result = renderCardDetails(makeDetail({ tags: 'hidden crease' }));
      assert.ok(result.includes('hidden crease'));
    });

    it('handles null set_name', () => {
      const result = renderCardDetails(makeDetail({ set_name: null }));
      assert.ok(result.includes('Unknown'));
    });
  });

  describe('renderPriceInfo', () => {
    it('shows price and confidence', () => {
      const result = renderPriceInfo(makePrice());
      assert.ok(result.includes('650.00'));
      assert.ok(result.includes('85%'));
    });

    it('shows manual review flag', () => {
      const result = renderPriceInfo(makePrice({ manual_check_necessary: true }));
      assert.ok(result.includes('Manual review'));
    });

    it('shows unpriceable for null price', () => {
      const result = renderPriceInfo(makePrice({ estimated_price: null }));
      assert.ok(result.includes('Unpriceable'));
    });

    it('shows price range', () => {
      const result = renderPriceInfo(makePrice());
      assert.ok(result.includes('580.00'));
      assert.ok(result.includes('750.00'));
    });
  });

  describe('renderCardSummary', () => {
    it('combines card details and price info', () => {
      const result = renderCardSummary(makeDetail(), makePrice());
      assert.ok(result.includes('Charizard'));
      assert.ok(result.includes('650.00'));
    });

    it('works without price', () => {
      const result = renderCardSummary(makeDetail(), null);
      assert.ok(result.includes('Charizard'));
      assert.ok(!result.includes('650.00'));
    });
  });

  describe('renderPhotoRequest', () => {
    it('includes card name and side', () => {
      const result = renderPhotoRequest(makeDetail(), 'front');
      assert.ok(result.includes('Charizard'));
      assert.ok(result.includes('front'));
    });
  });

  describe('renderListingConfirmation', () => {
    it('includes card name and price', () => {
      const result = renderListingConfirmation(makeDetail(), 650);
      assert.ok(result.includes('Charizard'));
      assert.ok(result.includes('650.00'));
    });
  });

  describe('Keyboard builders', () => {
    it('buildReviewKeyboard returns 2 rows', () => {
      const kb = buildReviewKeyboard(42);
      assert.equal(kb.length, 2);
      assert.ok(kb[0].some(b => b.callbackData === 'approve:42'));
      assert.ok(kb[0].some(b => b.callbackData === 'skip:42'));
    });

    it('buildPhotoRequestKeyboard returns skip option', () => {
      const kb = buildPhotoRequestKeyboard(42);
      assert.equal(kb.length, 1);
      assert.ok(kb[0][0].callbackData === 'skip:42');
    });
  });
});
