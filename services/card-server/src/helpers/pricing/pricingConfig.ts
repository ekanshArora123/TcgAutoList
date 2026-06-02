/**
 * Pricing Configuration — All tunable constants for the pricing algorithm.
 *
 * Change values here to adjust pricing behavior without touching algorithm logic.
 * See docs/pricing-algorithm.md for full documentation of how these are used.
 */

// ─── Algorithm Metadata ─────────────────────────────────────

export const ALGORITHM_VERSION = 'blended-v2';

// ─── Price Thresholds ───────────────────────────────────────

/** Cards above this price always get flagged for manual review. */
export const HIGH_VALUE_THRESHOLD = 50;

/** If lowest listing and sold average diverge by more than this %, flag for review. */
export const PRICE_DIVERGENCE_THRESHOLD = 0.30;

/** Minimum number of sold listings for "confident" pricing. */
export const MIN_SOLDS_FOR_CONFIDENCE = 3;

// ─── Fees ───────────────────────────────────────────────────

/**
 * Platform fee rate. Both TCGplayer and eBay take ~15%.
 * Used in liquid value: (sell_price * (1 - FEE_RATE)) - shipping_cost
 */
export const FEE_RATE = 0.15;

// ─── Shipping ───────────────────────────────────────────────

/**
 * TCGplayer free-shipping threshold.
 * Cards under this price effectively have $0 seller shipping cost
 * because buyers bundle to hit TCGplayer's $5 free shipping threshold.
 *
 * Implications for cards under this threshold:
 *   - Use listed_price only (ignore shipping_price from API)
 *   - Listings take priority over solds in divergence checks
 *   - Liquid value shipping cost = $0
 */
export const CHEAP_CARD_THRESHOLD = 5;

/** Price above which tracked shipping is required. */
export const SHIPPING_THRESHOLD = 25;

/** Shipping cost for cards $5 - $25 (PWE / plain white envelope). */
export const SHIPPING_COST_LOW = 1;

/** Shipping cost for cards over $25 (tracked bubble mailer). */
export const SHIPPING_COST_HIGH = 5;

// ─── Condition Extrapolation ────────────────────────────────

/**
 * Price multiplier per full condition tier step (compounding).
 * Each step worse = price * this value.
 * 0.70 = 30% discount per tier.
 *
 * Example: NM $10 -> LP $7.00 -> MP $4.90 -> HP $3.43
 *
 * Future: Should be data-driven per era/rarity. Vintage WOTC has
 * steeper curves; modern barely differs between NM and LP.
 */
export const CONDITION_STEP_MULTIPLIER = 0.70;

/** Primary conditions in order, best to worst. */
export const PRIMARY_CONDITIONS = ['MINT', 'NM', 'LP', 'MP', 'HP', 'DMG'] as const;

/**
 * In-between conditions and their two neighboring primary conditions.
 * Price = average of the two neighbors.
 * Note: TCGplayer does NOT support in-between conditions for listing.
 */
export const IN_BETWEEN_CONDITIONS: Record<string, [string, string]> = {
  'LP-NM': ['NM', 'LP'],
  'MP-LP': ['LP', 'MP'],
  'HP-MP': ['MP', 'HP'],
};

// ─── Confidence Adjustments ─────────────────────────────────

/** Base confidence when anchored on lowest active listing. */
export const CONFIDENCE_LISTING_BASE = 70;

/** Confidence when cheap card has divergent solds (listings win). */
export const CONFIDENCE_CHEAP_DIVERGENT = 75;

/** Confidence when sold average overrides stale listings. */
export const CONFIDENCE_SOLDS_OVERRIDE = 60;

/** Confidence when using 50/50 blend of listing + monthly sold avg. */
export const CONFIDENCE_BLENDED = 70;

/** Blend ratio for listing vs sold avg when listing is too high. */
export const BLEND_RATIO = 0.5;

/** Number of days to look back for "last month" sold average in blend calculation. */
export const BLEND_SOLDS_DAYS = 30;

/** Number of days to look back for "last week" solds in divergence check. */
export const DIVERGENCE_RECENT_DAYS = 7;

/** Confidence when solds are higher than listing (healthy undercutting). */
export const CONFIDENCE_SOLDS_HIGHER = 75;

/** Confidence when solds confirm listing price (< divergence threshold). */
export const CONFIDENCE_SOLDS_CONFIRM = 85;

/** Bonus confidence for having >= MIN_SOLDS_FOR_CONFIDENCE solds. */
export const CONFIDENCE_VOLUME_BONUS = 10;

/** Max confidence cap. */
export const CONFIDENCE_MAX = 95;

/** Confidence when listing exists but no sold data. */
export const CONFIDENCE_NO_SOLDS = 50;

/** Confidence when using sold average (no active listings). */
export const CONFIDENCE_SOLDS_ONLY = 55;

/** Confidence when few solds and no active listings. */
export const CONFIDENCE_FEW_SOLDS = 40;

// ─── Derived Price Estimates ────────────────────────────────

/** Low price estimate multiplier (when no sold min available). */
export const LOW_ESTIMATE_MULTIPLIER = 0.9;

/** High price estimate multiplier (when no sold max available). */
export const HIGH_ESTIMATE_MULTIPLIER = 1.15;

// ─── TCGplayer Seller Filtering ─────────────────────────────

/** Minimum seller rating to include in pricing data. */
export const MIN_SELLER_RATING = 80;

/** Minimum seller sales count to include in pricing data. */
export const MIN_SELLER_SALES = 30;
