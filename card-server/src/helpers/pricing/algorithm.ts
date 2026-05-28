/**
 * Pricing Algorithm v1 — Lowest Listing Anchor
 *
 * PHILOSOPHY:
 *   The price of a card is what someone will pay for it. For liquid cards,
 *   the lowest active listing is the best proxy — it almost always sells.
 *   For illiquid cards, recent sold prices are a better signal.
 *   TCGplayer "market price" is unreliable and is NOT used.
 *
 * CURRENT VERSION (v2):
 *   Anchors on the lowest active TCGplayer listing. When the listing is
 *   >30% above recent sales, blends 50% listing + 50% last-month sold avg.
 *   Divergence check uses max(last 7 days, last 3 sales) for robustness.
 *   MINT condition is priced as NM. Custom/photo listings are excluded
 *   at the fetch layer.
 *
 * FUTURE VERSIONS:
 *   v2: Factor in sales velocity (sales per 48hr window). If a card sells
 *       frequently enough that the lowest listing will be absorbed quickly,
 *       price higher. If it sells rarely, match or undercut lowest.
 *   v3: LLM sampling — the agent's LLM evaluates raw data from tcgplayer
 *       sales, tcgplayer listings, ebay solds, and ebay listings as separate
 *       tool calls, then reasons about the right price. This handles edge
 *       cases (graded cards, errors, volatile prices) better than any
 *       rules-based system.
 */

import type { TcgPlayerSoldListing, TcgPlayerActiveListing } from '../tcgplayer/fetchPrices.js';
import {
  ALGORITHM_VERSION,
  HIGH_VALUE_THRESHOLD,
  PRICE_DIVERGENCE_THRESHOLD,
  MIN_SOLDS_FOR_CONFIDENCE,
  FEE_RATE,
  CHEAP_CARD_THRESHOLD,
  SHIPPING_THRESHOLD,
  SHIPPING_COST_LOW,
  SHIPPING_COST_HIGH,
  CONDITION_STEP_MULTIPLIER,
  PRIMARY_CONDITIONS,
  IN_BETWEEN_CONDITIONS,
  CONFIDENCE_LISTING_BASE,
  CONFIDENCE_CHEAP_DIVERGENT,
  CONFIDENCE_SOLDS_OVERRIDE,
  CONFIDENCE_SOLDS_HIGHER,
  CONFIDENCE_SOLDS_CONFIRM,
  CONFIDENCE_BLENDED,
  CONFIDENCE_VOLUME_BONUS,
  CONFIDENCE_MAX,
  CONFIDENCE_NO_SOLDS,
  CONFIDENCE_SOLDS_ONLY,
  CONFIDENCE_FEW_SOLDS,
  LOW_ESTIMATE_MULTIPLIER,
  HIGH_ESTIMATE_MULTIPLIER,
  BLEND_RATIO,
  BLEND_SOLDS_DAYS,
  DIVERGENCE_RECENT_DAYS,
} from './pricingConfig.js';

// ─── Types ───────────────────────────────────────────────────

export interface PricingInput {
  /** Active for-sale listings on TCGplayer for this card+condition+finish. Sorted by price ascending. */
  activeListings: TcgPlayerActiveListing[];
  /** Recent sold listings on TCGplayer for this card+condition+finish. Sorted by date descending. */
  soldListings: TcgPlayerSoldListing[];
  /** The card's condition. */
  condition: string;
  /** The card's finish. */
  finish: string;
  /**
   * Whether this card has specialties that require manual review.
   * NOTE: 1st Edition and Reverse Holo are NOT specialties — they are
   * TCGplayer-level variants (separate product IDs / finishes) and get
   * their own listings/solds data. Specialties that DO trigger review:
   * graded cards, specific errors, world championship reprints, etc.
   */
  hasManualReviewSpecialty: boolean;
}

export interface PricingResult {
  estimated_price: number | null;
  estimated_liquid_value: number | null;
  estimated_low_price: number | null;
  estimated_high_price: number | null;
  estimated_low_price_liquid: number | null;
  estimated_high_price_liquid: number | null;
  confidence_percent: number;
  manual_check_necessary: boolean;
  algorithm_version: string;
  /** Human-readable explanation of how the price was determined. */
  reasoning: string;
}

// ─── Algorithm ───────────────────────────────────────────────

/**
 * Compute a listing price from available TCGplayer data.
 *
 * Decision tree:
 *
 *   1. Do active listings exist for this condition+finish?
 *      YES → Use lowest listing as the anchor (this is the v1 core).
 *      NO  → Go to step 3.
 *
 *   2. Do sold listings exist as a sanity check?
 *      YES → Compare lowest listing vs sold average.
 *            If they diverge > 30%, flag for manual review.
 *            If sold average is HIGHER than lowest listing, the listing
 *            price is still good (someone is undercutting — fine for us).
 *            If sold average is LOWER, the lowest listing may be stale/overpriced.
 *            In that case, use sold average instead and flag.
 *      NO  → Use lowest listing with lower confidence.
 *
 *   3. (No active listings) Do sold listings exist?
 *      YES → Use average of recent solds as the price.
 *      NO  → No data at all. Return null price, flag as unpriceable.
 *
 *   4. Apply edge case flags:
 *      - Price > HIGH_VALUE_THRESHOLD → manual review
 *      - hasManualReviewSpecialty (graded, errors — NOT 1st Ed/Reverse Holo) → manual review
 *      - Fewer than MIN_SOLDS_FOR_CONFIDENCE solds → lower confidence
 *
 *   5. Compute liquid value and derived prices.
 */
export function computePrice(input: PricingInput): PricingResult {
  // MINT cards are priced as NM — TCGplayer has no MINT-specific data
  const effectiveInput = input.condition === 'MINT'
    ? { ...input, condition: 'NM' }
    : input;

  const reasons: string[] = [];
  let estimatedPrice: number | null = null;
  let confidence = 0;
  let manualCheck = false;

  if (input.condition === 'MINT') {
    reasons.push('MINT condition — pricing as Near Mint.');
  }

  const lowestListing = getLowestListingPrice(effectiveInput.activeListings);
  const soldStats = computeSoldStats(effectiveInput.soldListings);

  // ── Step 1: Try lowest active listing ──

  if (lowestListing !== null) {
    estimatedPrice = lowestListing;
    confidence = CONFIDENCE_LISTING_BASE;
    reasons.push(`Anchored on lowest active listing: $${lowestListing.toFixed(2)}.`);

    // ── Step 2: Sanity check against solds ──

    // For cheap cards (under $5), sold prices are unreliable because they
    // inconsistently include $1 shipping. Listings take priority.
    const isCheapCard = lowestListing < CHEAP_CARD_THRESHOLD;

    if (soldStats !== null) {
      // Divergence check: use avg of max(last 7 days, last 3 sales) for robustness
      const divergenceSalePrice = computeDivergenceSalePrice(effectiveInput.soldListings);
      const effectiveSalePrice = divergenceSalePrice ?? soldStats.average;
      const divergence = Math.abs(lowestListing - effectiveSalePrice) / effectiveSalePrice;
      const listingIsHigher = lowestListing > effectiveSalePrice;

      if (divergence > PRICE_DIVERGENCE_THRESHOLD) {
        if (isCheapCard) {
          // Cheap cards: listings always win. Sold prices are noisy due to
          // inconsistent shipping ($0 bundled vs $1 single-card).
          confidence = CONFIDENCE_CHEAP_DIVERGENT;
          reasons.push(
            `Sold price ($${effectiveSalePrice.toFixed(2)}) diverges ${(divergence * 100).toFixed(0)}% from listing. ` +
            `Card is under $${CHEAP_CARD_THRESHOLD} — sold prices are unreliable (shipping noise). Keeping listing price.`
          );
        } else if (listingIsHigher) {
          // Listing is >30% above recent sales — blend 50/50 with monthly sold avg
          const monthAvg = computeMonthSoldAvg(effectiveInput.soldListings);
          if (monthAvg !== null) {
            estimatedPrice = round(BLEND_RATIO * lowestListing + BLEND_RATIO * monthAvg);
            confidence = CONFIDENCE_BLENDED;
            reasons.push(
              `Listing ($${lowestListing.toFixed(2)}) is ${(divergence * 100).toFixed(0)}% above recent sales ($${effectiveSalePrice.toFixed(2)}). ` +
              `Blending 50/50 with last-month sold avg ($${monthAvg.toFixed(2)}) → $${estimatedPrice.toFixed(2)}.`
            );
          } else {
            // No monthly sales data — keep listing price
            reasons.push(
              `Listing is ${(divergence * 100).toFixed(0)}% above recent sales but no monthly sold data — keeping listing price.`
            );
          }
        } else {
          // Solds are higher than listing — undercutting is healthy
          confidence = CONFIDENCE_SOLDS_HIGHER;
          reasons.push(
            `Sold price ($${effectiveSalePrice.toFixed(2)}) is above lowest listing — ` +
            `someone is undercutting. Lowest listing is a competitive price.`
          );
        }
      } else {
        confidence = CONFIDENCE_SOLDS_CONFIRM;
        reasons.push(`Sold price ($${effectiveSalePrice.toFixed(2)}) confirms listing price (${(divergence * 100).toFixed(0)}% divergence).`);
      }

      if (soldStats.count >= MIN_SOLDS_FOR_CONFIDENCE) {
        confidence = Math.min(confidence + CONFIDENCE_VOLUME_BONUS, CONFIDENCE_MAX);
        reasons.push(`${soldStats.count} recent solds — good data volume.`);
      } else {
        reasons.push(`Only ${soldStats.count} recent sold(s) — limited data.`);
      }
    } else {
      confidence = CONFIDENCE_NO_SOLDS;
      reasons.push('No sold data available. Pricing based on listings only — lower confidence.');
    }

  // ── Step 3: No active listings — fall back to solds ──

  } else if (soldStats !== null) {
    estimatedPrice = soldStats.average;
    confidence = CONFIDENCE_SOLDS_ONLY;
    reasons.push(`No active listings. Using average of ${soldStats.count} recent sold(s): $${soldStats.average.toFixed(2)}.`);

    if (soldStats.count < MIN_SOLDS_FOR_CONFIDENCE) {
      confidence = CONFIDENCE_FEW_SOLDS;
      manualCheck = true;
      reasons.push(`Fewer than ${MIN_SOLDS_FOR_CONFIDENCE} solds — flagged for manual review.`);
    }

  // ── Step 3b: No data at all ──

  } else {
    estimatedPrice = null;
    confidence = 0;
    manualCheck = true;
    reasons.push('No active listings and no sold data. Card is unpriceable automatically.');
  }

  // ── Step 4: Edge case flags ──

  if (estimatedPrice !== null && estimatedPrice > HIGH_VALUE_THRESHOLD) {
    manualCheck = true;
    reasons.push(`High-value card ($${estimatedPrice.toFixed(2)} > $${HIGH_VALUE_THRESHOLD}) — flagged for manual review.`);
  }

  if (input.hasManualReviewSpecialty) {
    manualCheck = true;
    reasons.push('Card has manual-review specialty (graded, error, etc.) — flagged for manual review.');
  }

  // ── Step 5: Compute derived values ──

  const liquidValue = estimatedPrice !== null ? computeLiquidValue(estimatedPrice) : null;

  const lowPrice = soldStats?.min ?? (lowestListing !== null ? round(lowestListing * LOW_ESTIMATE_MULTIPLIER) : null);
  const highPrice = soldStats?.max ?? (lowestListing !== null ? round(lowestListing * HIGH_ESTIMATE_MULTIPLIER) : null);
  const lowPriceLiquid = lowPrice !== null ? computeLiquidValue(lowPrice) : null;
  const highPriceLiquid = highPrice !== null ? computeLiquidValue(highPrice) : null;

  return {
    estimated_price: estimatedPrice !== null ? round(estimatedPrice) : null,
    estimated_liquid_value: liquidValue,
    estimated_low_price: lowPrice,
    estimated_high_price: highPrice,
    estimated_low_price_liquid: lowPriceLiquid,
    estimated_high_price_liquid: highPriceLiquid,
    confidence_percent: confidence,
    manual_check_necessary: manualCheck,
    algorithm_version: ALGORITHM_VERSION,
    reasoning: reasons.join(' '),
  };
}

// ─── Liquid Value ────────────────────────────────────────────

/**
 * Compute liquid value (what you actually pocket after fees and shipping).
 *
 * Formula: (sell_price * (1 - FEE_RATE)) - shipping_cost
 *
 * The sell price IS what the buyer pays (shipping included in the price).
 * Fees are taken as a percentage of that. Shipping is a flat cost you eat.
 *
 * Shipping tiers:
 *   - Cards under $5: $0 (buyer covers via TCGplayer's $5 free shipping threshold)
 *   - Cards $5-$25: $1 (PWE / plain white envelope)
 *   - Cards over $25: $5 (tracked bubble mailer)
 *
 * Example: $2 card  → $2 * 0.85 - $0 = $1.70  (buyer bundles for free shipping)
 * Example: $10 card → $10 * 0.85 - $1 = $7.50
 * Example: $40 card → $40 * 0.85 - $5 = $29.00
 */
export function computeLiquidValue(sellPrice: number): number {
  let shipping: number;
  if (sellPrice < CHEAP_CARD_THRESHOLD) {
    shipping = 0;
  } else if (sellPrice > SHIPPING_THRESHOLD) {
    shipping = SHIPPING_COST_HIGH;
  } else {
    shipping = SHIPPING_COST_LOW;
  }
  const afterFees = sellPrice * (1 - FEE_RATE);
  const profit = afterFees - shipping;
  return round(Math.max(profit, 0));
}

// ─── Cross-Condition Extrapolation ───────────────────────────

/**
 * Estimate a price for a condition that has no direct data,
 * based on data from a different condition of the same card.
 *
 * ┌─────────────────────────────────────────────────────────────┐
 * │  ALL EXTRAPOLATION LOGIC LIVES IN THIS ONE FUNCTION.       │
 * │  To change the pricing model between conditions,           │
 * │  edit ONLY this function and the constants above it.        │
 * └─────────────────────────────────────────────────────────────┘
 *
 * MODEL (v1):
 *   - Primary conditions: MINT, NM, LP, MP, HP, DMG
 *   - Each step down = price * 0.70 (30% discount, compounding)
 *     e.g., NM $10 → LP $7.00 → MP $4.90 → HP $3.43
 *   - In-between conditions (LP-NM, MP-LP, HP-MP) = average of neighbors
 *     e.g., LP-NM price = (NM price + LP price) / 2
 *   - Works in BOTH directions: can go from worse→better or better→worse
 *     Going better = price / 0.70 per step (i.e., the inverse)
 *
 * NOTE ON IN-BETWEEN CONDITIONS:
 *   TCGplayer does NOT support in-between conditions (LP-NM, MP-LP, etc.).
 *   When listing, the user must choose which primary condition to list under.
 *   The in-between grade is for internal inventory tracking only.
 *
 * FUTURE: Replace CONDITION_STEP_MULTIPLIER with data-driven per-era/rarity
 * discount curves. Vintage WOTC has steeper condition curves than modern.
 *
 * @param sourcePrice     - The known price from the source condition.
 * @param sourceCondition - The condition the source price is for (e.g., "NM").
 * @param targetCondition - The condition you want a price for (e.g., "LP").
 * @returns Estimated price for the target condition, or null if conditions unknown.
 */
export function extrapolateAcrossConditions(
  sourcePrice: number,
  sourceCondition: string,
  targetCondition: string,
): number | null {
  // MINT is priced as NM — no premium
  const effectiveSource = sourceCondition === 'MINT' ? 'NM' : sourceCondition;
  const effectiveTarget = targetCondition === 'MINT' ? 'NM' : targetCondition;

  if (effectiveSource === effectiveTarget) return sourcePrice;

  // Resolve in-between source to a primary price
  const resolvedSourcePrice = resolveInBetweenSource(sourcePrice, effectiveSource);
  const resolvedSourceCondition = getResolvedPrimary(effectiveSource);

  if (resolvedSourcePrice === null || resolvedSourceCondition === null) return null;

  // If the target is a primary condition, extrapolate directly
  if (isPrimary(effectiveTarget)) {
    return extrapolateBetweenPrimaries(resolvedSourcePrice, resolvedSourceCondition, effectiveTarget);
  }

  // If the target is an in-between condition, compute both neighbors then average
  const neighbors = IN_BETWEEN_CONDITIONS[effectiveTarget];
  if (!neighbors) return null;

  const [betterCondition, worseCondition] = neighbors;
  const betterPrice = extrapolateBetweenPrimaries(resolvedSourcePrice, resolvedSourceCondition, betterCondition);
  const worsePrice = extrapolateBetweenPrimaries(resolvedSourcePrice, resolvedSourceCondition, worseCondition);

  if (betterPrice === null || worsePrice === null) return null;
  return round((betterPrice + worsePrice) / 2);
}

/**
 * Extrapolate between two primary conditions using compounding multiplier.
 * Steps = index difference in PRIMARY_CONDITIONS array.
 */
function extrapolateBetweenPrimaries(
  sourcePrice: number,
  sourceCondition: string,
  targetCondition: string,
): number | null {
  const sourceIdx = PRIMARY_CONDITIONS.indexOf(sourceCondition as any);
  const targetIdx = PRIMARY_CONDITIONS.indexOf(targetCondition as any);
  if (sourceIdx === -1 || targetIdx === -1) return null;

  const steps = targetIdx - sourceIdx; // positive = worse condition
  // Compounding: each step worse multiplies by CONDITION_STEP_MULTIPLIER
  // Each step better divides by CONDITION_STEP_MULTIPLIER
  const multiplier = Math.pow(CONDITION_STEP_MULTIPLIER, steps);
  return round(sourcePrice * multiplier);
}

/** Check if a condition is a primary (TCGplayer-supported) condition. */
function isPrimary(condition: string): boolean {
  return PRIMARY_CONDITIONS.includes(condition as any);
}

/**
 * If source is an in-between condition, resolve to the nearest primary
 * by computing what the better-neighbor price would be.
 * Returns the resolved price at the better neighbor condition.
 */
function resolveInBetweenSource(sourcePrice: number, sourceCondition: string): number | null {
  if (isPrimary(sourceCondition)) return sourcePrice;

  const neighbors = IN_BETWEEN_CONDITIONS[sourceCondition];
  if (!neighbors) return null;

  // In-between price = average(better, worse) = average(better, better * MULTIPLIER)
  // So: sourcePrice = (better + better * MULTIPLIER) / 2
  //     sourcePrice = better * (1 + MULTIPLIER) / 2
  //     better = sourcePrice * 2 / (1 + MULTIPLIER)
  const [betterCondition] = neighbors;
  const betterPrice = sourcePrice * 2 / (1 + CONDITION_STEP_MULTIPLIER);
  return round(betterPrice);
}

/** Get the resolved primary condition for any condition (primary or in-between). */
function getResolvedPrimary(condition: string): string | null {
  if (isPrimary(condition)) return condition;
  const neighbors = IN_BETWEEN_CONDITIONS[condition];
  return neighbors ? neighbors[0] : null;
}

// ─── Divergence & Blend Helpers ──────────────────────────────

/**
 * Compute the sale price used for the divergence check.
 *
 * Uses whichever group has more entries:
 *   - Sales from the last 7 days
 *   - The 3 most recent sales
 * Returns the average of the chosen group.
 */
function computeDivergenceSalePrice(solds: TcgPlayerSoldListing[]): number | null {
  if (solds.length === 0) return null;

  const now = new Date();
  const cutoff = new Date(now);
  cutoff.setDate(cutoff.getDate() - DIVERGENCE_RECENT_DAYS);
  const cutoffStr = cutoff.toISOString().split('T')[0];

  // Last 7 days' sales
  const lastWeekSolds = solds.filter(s => s.sold_date >= cutoffStr);

  // Last 3 sales by date (most recent first)
  const sorted = [...solds].sort((a, b) => b.sold_date.localeCompare(a.sold_date));
  const last3 = sorted.slice(0, 3);

  // Use whichever group has more entries
  const group = lastWeekSolds.length >= last3.length ? lastWeekSolds : last3;

  if (group.length === 0) return null;

  const sum = group.reduce((acc, s) => acc + s.sold_price, 0);
  return round(sum / group.length);
}

/**
 * Compute the average sold price over the last month.
 * Used for the 50/50 blend when listing is too high.
 */
function computeMonthSoldAvg(solds: TcgPlayerSoldListing[]): number | null {
  if (solds.length === 0) return null;

  const now = new Date();
  const cutoff = new Date(now);
  cutoff.setDate(cutoff.getDate() - BLEND_SOLDS_DAYS);
  const cutoffStr = cutoff.toISOString().split('T')[0];

  const monthSolds = solds.filter(s => s.sold_date >= cutoffStr);

  if (monthSolds.length === 0) return null;

  const sum = monthSolds.reduce((acc, s) => acc + s.sold_price, 0);
  return round(sum / monthSolds.length);
}

// ─── Helpers ─────────────────────────────────────────────────

/**
 * Get the lowest listing price, accounting for TCGplayer's shipping model.
 *
 * For cards under $5: use listed_price only (ignore shipping).
 * TCGplayer offers free shipping at $5+ with one seller, so most buyers
 * bundle cheap cards and never pay shipping. The listed_price IS the
 * real price these cards sell at.
 *
 * For cards >= $5: use listed_price + shipping_price as the total cost.
 */
function getLowestListingPrice(listings: TcgPlayerActiveListing[]): number | null {
  if (listings.length === 0) return null;

  let lowest = Infinity;
  for (const listing of listings) {
    // First pass: check raw listed_price to determine cheap card threshold
    const total = listing.listed_price < CHEAP_CARD_THRESHOLD
      ? listing.listed_price
      : listing.listed_price + listing.shipping_price;
    if (total < lowest) lowest = total;
  }
  return lowest === Infinity ? null : round(lowest);
}

function computeSoldStats(solds: TcgPlayerSoldListing[]): {
  average: number;
  median: number;
  min: number;
  max: number;
  count: number;
} | null {
  if (solds.length === 0) return null;

  const prices = solds.map(s => s.sold_price).sort((a, b) => a - b);
  const sum = prices.reduce((a, b) => a + b, 0);
  const mid = Math.floor(prices.length / 2);
  const median = prices.length % 2 === 0
    ? (prices[mid - 1] + prices[mid]) / 2
    : prices[mid];

  return {
    average: round(sum / prices.length),
    median: round(median),
    min: prices[0],
    max: prices[prices.length - 1],
    count: prices.length,
  };
}

function round(n: number): number {
  return Math.round(n * 100) / 100;
}
