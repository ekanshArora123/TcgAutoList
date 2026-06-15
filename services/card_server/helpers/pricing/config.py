"""Pricing Configuration — All tunable constants for the pricing algorithm.

Change values here to adjust pricing behavior without touching algorithm logic.
See docs/pricing-algorithm.md for full documentation. Ported from pricingConfig.ts.
"""

# ─── Algorithm Metadata ─────────────────────────────────────

ALGORITHM_VERSION = "blended-v2"

# ─── Price Thresholds ───────────────────────────────────────

# Cards above this price always get flagged for manual review.
HIGH_VALUE_THRESHOLD = 50

# If lowest listing and sold average diverge by more than this %, flag for review.
PRICE_DIVERGENCE_THRESHOLD = 0.30

# Minimum number of sold listings for "confident" pricing.
MIN_SOLDS_FOR_CONFIDENCE = 3

# ─── Recent-Sales Window ────────────────────────────────────

# Sold-listing fetches can now return a card's full sales history (hundreds of
# rows). Stats that feed pricing (fallback average, low/high range, data volume)
# must use only RECENT sales, or stale historical prices drag the estimate.
# Use whichever group is LARGER: all sales within RECENT_SOLDS_DAYS, or the
# RECENT_SOLDS_MAX_COUNT most recent sales. (Divergence/blend windows are
# separate — see DIVERGENCE_RECENT_DAYS / BLEND_SOLDS_DAYS.)
RECENT_SOLDS_MAX_COUNT = 25
RECENT_SOLDS_DAYS = 5

# ─── Fees ───────────────────────────────────────────────────

# Platform fee rate. Both TCGplayer and eBay take ~15%.
# Used in liquid value: (sell_price * (1 - FEE_RATE)) - shipping_cost
FEE_RATE = 0.15

# ─── Shipping ───────────────────────────────────────────────

# TCGplayer free-shipping threshold. Cards under this price effectively have
# $0 seller shipping cost because buyers bundle to hit the $5 free-shipping
# threshold. For cards under this threshold: use listed_price only, listings
# take priority over solds, and liquid value shipping cost = $0.
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

# In-between conditions and their two neighboring primary conditions.
# Price = average of the two neighbors. TCGplayer does NOT support in-between.
IN_BETWEEN_CONDITIONS: dict[str, tuple[str, str]] = {
    "LP-NM": ("NM", "LP"),
    "MP-LP": ("LP", "MP"),
    "HP-MP": ("MP", "HP"),
}

# ─── Confidence Adjustments ─────────────────────────────────

CONFIDENCE_LISTING_BASE = 70
CONFIDENCE_CHEAP_DIVERGENT = 75
CONFIDENCE_SOLDS_OVERRIDE = 60
CONFIDENCE_BLENDED = 70
BLEND_RATIO = 0.5
BLEND_SOLDS_DAYS = 30
DIVERGENCE_RECENT_DAYS = 7
CONFIDENCE_SOLDS_HIGHER = 75
CONFIDENCE_SOLDS_CONFIRM = 85
CONFIDENCE_VOLUME_BONUS = 10
CONFIDENCE_MAX = 95
CONFIDENCE_NO_SOLDS = 50
CONFIDENCE_SOLDS_ONLY = 55
CONFIDENCE_FEW_SOLDS = 40

# ─── Derived Price Estimates ────────────────────────────────

LOW_ESTIMATE_MULTIPLIER = 0.9
HIGH_ESTIMATE_MULTIPLIER = 1.15

# ─── TCGplayer Seller Filtering ─────────────────────────────

MIN_SELLER_RATING = 80
MIN_SELLER_SALES = 30
