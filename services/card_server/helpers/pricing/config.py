"""Pricing Configuration — All tunable constants for the pricing algorithm.

The module-level names below are the DEFAULTS. What the algorithm actually reads
is a `PricingConfig` instance, so a caller can price under different parameters
(tuning, backtesting a half-life) without editing this file:

    compute_price(inputs, replace(DEFAULT_CONFIG, sold_weight_half_life_days=45))

Change a value here to move the default for every caller. See
docs/pricing-algorithm.md for the full decision tree.
"""

from __future__ import annotations

from dataclasses import dataclass

# ─── Algorithm Metadata ─────────────────────────────────────

# v3: divergence no longer just flags — it blends the listing with an
# age-weighted sold average, in both directions. See PricingConfig below.
ALGORITHM_VERSION = "weighted-blend-v3"

# ─── Price Thresholds ───────────────────────────────────────

# Cards above this price always get flagged for manual review.
HIGH_VALUE_THRESHOLD = 50

# Listing vs sold signal must diverge by more than this before the blend fires.
# At or below it the lowest listing stands unmodified.
PRICE_DIVERGENCE_THRESHOLD = 0.30

# Minimum number of sold listings for "confident" pricing.
MIN_SOLDS_FOR_CONFIDENCE = 3

# ─── Sold Signal ────────────────────────────────────────────

# Divergence is measured against the plain mean of the N most recent sales.
# This is the DETECTION signal — deliberately small and recent, so a fresh move
# in the market trips the blend even when the deeper history disagrees.
DIVERGENCE_SALE_COUNT = 5

# The BLEND value is an age-weighted mean over up to this many recent sales,
# each weighted 0.5 ** (age_days / half_life). A 30-day half-life means a sale
# from a month ago counts half as much as one from today, 90 days an eighth,
# and a year 0.02% — so old sales fade out instead of being cut off at a cliff.
#
# Note this mean is time-invariant: ageing every sale by the same interval
# scales every weight by the same factor, which cancels in the ratio. A stored
# weighted average therefore does NOT drift between collections — it only moves
# when new sales arrive. Changing the half-life below DOES invalidate a stored
# value, which is why re-tuning it means recomputing from raw `sales` rows.
MAX_SALES_CONSIDERED = 25
SOLD_WEIGHT_HALF_LIFE_DAYS = 30.0

# ─── Divergence Blend ───────────────────────────────────────

# When the listing sits ABOVE the sold signal, the sold side's share of the
# price ramps linearly with how far apart they are: SALES_WEIGHT_MIN at the
# moment divergence trips, SALES_WEIGHT_MAX once divergence reaches
# DIVERGENCE_AT_MAX_SALES_WEIGHT. The listing always keeps the remainder, so it
# never drops below (1 - SALES_WEIGHT_MAX) of the price no matter how extreme
# the gap — a lone absurd listing is pulled toward the sales, not discarded.
#
# Worked example: listing $350, last-5 mean $200 -> divergence 0.75 -> sales
# weight 0.60. With an age-weighted sold mean of $175 the price is
# 0.60*175 + 0.40*350 = $245.
SALES_WEIGHT_MIN = 0.30
SALES_WEIGHT_MAX = 0.70
DIVERGENCE_AT_MAX_SALES_WEIGHT = 0.90

# When the listing sits BELOW the sold signal, someone is either undercutting or
# the listing is a lowball; either way the sold side gets a flat maximum share
# rather than a ramp, because there is no "how far below" reading that makes a
# below-market listing more trustworthy.
SALES_WEIGHT_LISTING_BELOW = 0.70

# ─── Fees ───────────────────────────────────────────────────

# Platform fee rate. Both TCGplayer and eBay take ~15%.
# Used in liquid value: (sell_price * (1 - FEE_RATE)) - shipping_cost
FEE_RATE = 0.15

# ─── Shipping ───────────────────────────────────────────────

# TCGplayer free-shipping threshold. Cards under this price effectively have
# $0 seller shipping cost because buyers bundle to hit the $5 free-shipping
# threshold. Cards under it are ALSO priced on listings alone — sold prices at
# that level are dominated by shipping noise, so they are ignored outright
# rather than blended (no divergence check, no weighting).
CHEAP_CARD_THRESHOLD = 5

# Price above which tracked shipping is required.
SHIPPING_THRESHOLD = 25

# Shipping cost for cards $5 - $25 (PWE / plain white envelope).
SHIPPING_COST_LOW = 1

# Shipping cost for cards over $25 (tracked bubble mailer).
SHIPPING_COST_HIGH = 5

# ─── Condition Extrapolation ────────────────────────────────

# Price multiplier per full condition tier step (compounding).
# 0.70 = 30% discount per tier. Example: NM $10 -> LP $7.00 -> MP $4.90 -> HP $3.43
CONDITION_STEP_MULTIPLIER = 0.70

# Primary conditions in order, best to worst.
PRIMARY_CONDITIONS = ("MINT", "NM", "LP", "MP", "HP", "DMG")

# In-between conditions and their two neighboring primary conditions, ordered
# (better, worse). Price = average of the two neighbors. TCGplayer does NOT
# support in-between grades, so these are always derived from their neighbors.
# See helpers/pricing/conditions.py for the resolution helpers built on this.
IN_BETWEEN_CONDITIONS: dict[str, tuple[str, str]] = {
    "LP-NM": ("NM", "LP"),
    "MP-LP": ("LP", "MP"),
    "HP-MP": ("MP", "HP"),
    "DM-HP": ("HP", "DMG"),
}

# ─── Confidence Adjustments ─────────────────────────────────

CONFIDENCE_LISTING_BASE = 70
CONFIDENCE_CHEAP = 75
CONFIDENCE_BLENDED = 70
CONFIDENCE_SOLDS_HIGHER = 75
CONFIDENCE_SOLDS_CONFIRM = 85
CONFIDENCE_VOLUME_BONUS = 10
CONFIDENCE_MAX = 95
CONFIDENCE_NO_SOLDS = 50
CONFIDENCE_SOLDS_ONLY = 55
CONFIDENCE_FEW_SOLDS = 40

# ─── Derived Price Estimates ────────────────────────────────

# Only used for the low/high band when there is NO sold data to bracket against.
LOW_ESTIMATE_MULTIPLIER = 0.9
HIGH_ESTIMATE_MULTIPLIER = 1.15

# ─── TCGplayer Seller Filtering ─────────────────────────────

MIN_SELLER_RATING = 80
MIN_SELLER_SALES = 30


@dataclass(frozen=True)
class PricingConfig:
    """Every knob the algorithm reads, defaulted from the constants above.

    Frozen so a config can be shared freely; use `dataclasses.replace` to derive
    a variant. `listing_count` is deliberately absent from the pricing maths —
    it is collected and stored, but does not currently influence a price.
    """

    algorithm_version: str = ALGORITHM_VERSION

    high_value_threshold: float = HIGH_VALUE_THRESHOLD
    price_divergence_threshold: float = PRICE_DIVERGENCE_THRESHOLD
    min_solds_for_confidence: int = MIN_SOLDS_FOR_CONFIDENCE

    divergence_sale_count: int = DIVERGENCE_SALE_COUNT
    max_sales_considered: int = MAX_SALES_CONSIDERED
    sold_weight_half_life_days: float = SOLD_WEIGHT_HALF_LIFE_DAYS

    sales_weight_min: float = SALES_WEIGHT_MIN
    sales_weight_max: float = SALES_WEIGHT_MAX
    divergence_at_max_sales_weight: float = DIVERGENCE_AT_MAX_SALES_WEIGHT
    sales_weight_listing_below: float = SALES_WEIGHT_LISTING_BELOW

    fee_rate: float = FEE_RATE
    cheap_card_threshold: float = CHEAP_CARD_THRESHOLD
    shipping_threshold: float = SHIPPING_THRESHOLD
    shipping_cost_low: float = SHIPPING_COST_LOW
    shipping_cost_high: float = SHIPPING_COST_HIGH

    condition_step_multiplier: float = CONDITION_STEP_MULTIPLIER
    low_estimate_multiplier: float = LOW_ESTIMATE_MULTIPLIER
    high_estimate_multiplier: float = HIGH_ESTIMATE_MULTIPLIER

    confidence_listing_base: float = CONFIDENCE_LISTING_BASE
    confidence_cheap: float = CONFIDENCE_CHEAP
    confidence_blended: float = CONFIDENCE_BLENDED
    confidence_solds_higher: float = CONFIDENCE_SOLDS_HIGHER
    confidence_solds_confirm: float = CONFIDENCE_SOLDS_CONFIRM
    confidence_volume_bonus: float = CONFIDENCE_VOLUME_BONUS
    confidence_max: float = CONFIDENCE_MAX
    confidence_no_solds: float = CONFIDENCE_NO_SOLDS
    confidence_solds_only: float = CONFIDENCE_SOLDS_ONLY
    confidence_few_solds: float = CONFIDENCE_FEW_SOLDS

    @property
    def sales_weight_slope(self) -> float:
        """Sales weight gained per unit of divergence above the threshold."""
        span = self.divergence_at_max_sales_weight - self.price_divergence_threshold
        if span <= 0:
            return 0.0
        return (self.sales_weight_max - self.sales_weight_min) / span


DEFAULT_CONFIG = PricingConfig()
