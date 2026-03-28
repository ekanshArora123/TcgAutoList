/**
 * Tier Router — evaluates pricing confidence + flags to determine
 * which processing tier a card should go through.
 *
 * Tier 1 (≥80% confidence): No LLM. Template listing. ~80% of cards.
 * Tier 2 (40-79% confidence): LLM with scoped context. ~15% of cards.
 * Tier 3 (<40% confidence): LLM with full context. ~5% of cards.
 */

import type { Price, TierConfig } from '../types.js';
import { DEFAULT_TIER_CONFIG } from '../types.js';

export interface TierResult {
  tier: 1 | 2 | 3;
  reason: string;
}

/**
 * Route a card to the appropriate processing tier based on
 * pricing confidence and card characteristics.
 */
export function routeToTier(
  price: Price | null,
  specialtyTwo: string,
  config: TierConfig = DEFAULT_TIER_CONFIG,
): TierResult {
  // No price data at all → Tier 3
  if (!price || price.estimated_price === null) {
    return { tier: 3, reason: 'No price data available — full LLM analysis needed.' };
  }

  const confidence = price.confidence_percent ?? 0;
  const priceValue = price.estimated_price;

  // Specialty cards (graded, errors) always need LLM review
  if (specialtyTwo !== 'None') {
    return { tier: 3, reason: `Specialty card (${specialtyTwo}) — full LLM analysis needed.` };
  }

  // Very high value → Tier 3
  if (priceValue > config.veryHighValueThreshold) {
    return { tier: 3, reason: `Very high value ($${priceValue.toFixed(2)} > $${config.veryHighValueThreshold}) — full LLM analysis needed.` };
  }

  // High value OR manual check flagged → at least Tier 2
  if (priceValue > config.highValueThreshold || price.manual_check_necessary) {
    if (confidence >= config.tier1MinConfidence) {
      return { tier: 2, reason: `High value or manual review flagged despite high confidence — scoped LLM review.` };
    }
  }

  // Tier 1: high confidence, not high value
  if (confidence >= config.tier1MinConfidence) {
    return { tier: 1, reason: `High confidence (${confidence}%) — dumb pipe.` };
  }

  // Tier 2: medium confidence
  if (confidence >= config.tier2MinConfidence) {
    return { tier: 2, reason: `Medium confidence (${confidence}%) — scoped LLM review.` };
  }

  // Tier 3: low confidence
  return { tier: 3, reason: `Low confidence (${confidence}%) — full LLM analysis needed.` };
}
