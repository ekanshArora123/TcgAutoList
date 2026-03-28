/**
 * Unit tests for the Tier Router.
 *
 * Tests the confidence-based routing logic that determines
 * which processing tier a card goes through.
 */

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { routeToTier } from '../agent/tierRouter.js';
import type { Price, TierConfig } from '../types.js';

// Helper to build a Price object with defaults
function makePrice(overrides: Partial<Price> = {}): Price {
  return {
    sku_id: 1,
    calculation_date: '2025-01-01',
    estimated_price: 10,
    estimated_liquid_value: 7.5,
    confidence_percent: 85,
    manual_check_necessary: false,
    manually_checked: false,
    algorithm_version: 'lowest-listing-v1',
    estimated_low_price: 9,
    estimated_high_price: 11,
    estimated_low_price_liquid: 6.65,
    estimated_high_price_liquid: 8.35,
    ...overrides,
  };
}

describe('Tier Router', () => {
  // ─── Tier 1 ────────────────────────────────────────────────

  it('routes high-confidence cheap cards to Tier 1', () => {
    const price = makePrice({ confidence_percent: 90, estimated_price: 5 });
    const result = routeToTier(price, 'None');
    assert.equal(result.tier, 1);
  });

  it('routes 80% confidence to Tier 1', () => {
    const price = makePrice({ confidence_percent: 80, estimated_price: 10 });
    const result = routeToTier(price, 'None');
    assert.equal(result.tier, 1);
  });

  it('routes 95% confidence to Tier 1', () => {
    const price = makePrice({ confidence_percent: 95, estimated_price: 2 });
    const result = routeToTier(price, 'None');
    assert.equal(result.tier, 1);
  });

  // ─── Tier 2 ────────────────────────────────────────────────

  it('routes medium confidence (40-79%) to Tier 2', () => {
    const price = makePrice({ confidence_percent: 60, estimated_price: 10 });
    const result = routeToTier(price, 'None');
    assert.equal(result.tier, 2);
  });

  it('routes 40% confidence to Tier 2', () => {
    const price = makePrice({ confidence_percent: 40, estimated_price: 10 });
    const result = routeToTier(price, 'None');
    assert.equal(result.tier, 2);
  });

  it('routes high-confidence + high-value to Tier 2', () => {
    const price = makePrice({ confidence_percent: 90, estimated_price: 75, manual_check_necessary: true });
    const result = routeToTier(price, 'None');
    assert.equal(result.tier, 2);
  });

  it('routes high-confidence + manual_check_necessary to Tier 2', () => {
    const price = makePrice({ confidence_percent: 85, manual_check_necessary: true });
    const result = routeToTier(price, 'None');
    assert.equal(result.tier, 2);
  });

  // ─── Tier 3 ────────────────────────────────────────────────

  it('routes low confidence (<40%) to Tier 3', () => {
    const price = makePrice({ confidence_percent: 30, estimated_price: 10 });
    const result = routeToTier(price, 'None');
    assert.equal(result.tier, 3);
  });

  it('routes null price to Tier 3', () => {
    const result = routeToTier(null, 'None');
    assert.equal(result.tier, 3);
  });

  it('routes unpriceable (null estimated_price) to Tier 3', () => {
    const price = makePrice({ estimated_price: null, confidence_percent: 0 });
    const result = routeToTier(price, 'None');
    assert.equal(result.tier, 3);
  });

  it('routes specialty cards (graded) to Tier 3', () => {
    const price = makePrice({ confidence_percent: 90, estimated_price: 10 });
    const result = routeToTier(price, 'PSA 10');
    assert.equal(result.tier, 3);
  });

  it('routes very high value (>$200) to Tier 3', () => {
    const price = makePrice({ confidence_percent: 90, estimated_price: 250 });
    const result = routeToTier(price, 'None');
    assert.equal(result.tier, 3);
  });

  // ─── Custom config ────────────────────────────────────────

  it('respects custom tier thresholds', () => {
    const config: TierConfig = {
      tier1MinConfidence: 90,
      tier2MinConfidence: 50,
      highValueThreshold: 100,
      veryHighValueThreshold: 500,
    };

    // 85% would be Tier 1 with defaults, but Tier 2 with stricter config
    const price = makePrice({ confidence_percent: 85, estimated_price: 10 });
    const result = routeToTier(price, 'None', config);
    assert.equal(result.tier, 2);
  });

  // ─── Reason strings ───────────────────────────────────────

  it('includes reason string in result', () => {
    const price = makePrice({ confidence_percent: 90 });
    const result = routeToTier(price, 'None');
    assert.ok(result.reason.length > 0);
    assert.ok(result.reason.includes('90%'));
  });
});
