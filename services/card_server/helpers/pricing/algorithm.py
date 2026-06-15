"""Pricing Algorithm — Lowest Listing Anchor (blended-v2).

PHILOSOPHY:
  The price of a card is what someone will pay for it. For liquid cards,
  the lowest active listing is the best proxy — it almost always sells.
  For illiquid cards, recent sold prices are a better signal.
  TCGplayer "market price" is unreliable and is NOT used.

Anchors on the lowest active TCGplayer listing. When the listing is >30% above
recent sales, blends 50% listing + 50% last-month sold avg. Divergence check
uses max(last 7 days, last 3 sales). MINT is priced as NM.

Sold stats (fallback average, low/high range, data volume) use only a RECENT
window of sales — max(last 5 days, last 25 sales) — so a full sales history
(now retrievable via paginated fetch) can't drag pricing toward stale values.

Ported from algorithm.ts. Listings/solds are plain dicts:
  active listing: {listed_price, shipping_price, ...}
  sold listing:   {sold_price, sold_date, ...}

See docs/pricing-algorithm.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional

from .config import (
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
    RECENT_SOLDS_MAX_COUNT,
    RECENT_SOLDS_DAYS,
)


@dataclass
class PricingResult:
    estimated_price: Optional[float]
    estimated_liquid_value: Optional[float]
    estimated_low_price: Optional[float]
    estimated_high_price: Optional[float]
    estimated_low_price_liquid: Optional[float]
    estimated_high_price_liquid: Optional[float]
    confidence_percent: float
    manual_check_necessary: bool
    algorithm_version: str
    reasoning: str


def _round(n: float) -> float:
    return round(n * 100) / 100


def compute_price(
    active_listings: list[dict[str, Any]],
    sold_listings: list[dict[str, Any]],
    condition: str,
    finish: str,
    has_manual_review_specialty: bool,
) -> PricingResult:
    """Compute a listing price from available TCGplayer data.

    MINT cards are priced as NM — TCGplayer has no MINT-specific data.
    """
    reasons: list[str] = []
    estimated_price: Optional[float] = None
    confidence: float = 0
    manual_check = False

    if condition == "MINT":
        reasons.append("MINT condition — pricing as Near Mint.")

    lowest_listing = _get_lowest_listing_price(active_listings)
    # Cap sold stats to a recent window so a long sales history (now retrievable
    # via paginated fetch) can't drag the price toward stale values. Divergence
    # and blend helpers keep their own (broader) windows over the full list.
    recent_solds = _recent_solds(sold_listings)
    sold_stats = _compute_sold_stats(recent_solds)

    # ── Step 1: Try lowest active listing ──
    if lowest_listing is not None:
        estimated_price = lowest_listing
        confidence = CONFIDENCE_LISTING_BASE
        reasons.append(f"Anchored on lowest active listing: ${lowest_listing:.2f}.")

        is_cheap_card = lowest_listing < CHEAP_CARD_THRESHOLD

        if sold_stats is not None:
            divergence_sale_price = _compute_divergence_sale_price(sold_listings)
            effective_sale_price = (
                divergence_sale_price if divergence_sale_price is not None else sold_stats["average"]
            )
            divergence = abs(lowest_listing - effective_sale_price) / effective_sale_price
            listing_is_higher = lowest_listing > effective_sale_price

            if divergence > PRICE_DIVERGENCE_THRESHOLD:
                if is_cheap_card:
                    confidence = CONFIDENCE_CHEAP_DIVERGENT
                    reasons.append(
                        f"Sold price (${effective_sale_price:.2f}) diverges {divergence * 100:.0f}% from listing. "
                        f"Card is under ${CHEAP_CARD_THRESHOLD} — sold prices are unreliable (shipping noise). "
                        "Keeping listing price."
                    )
                elif listing_is_higher:
                    month_avg = _compute_month_sold_avg(sold_listings)
                    if month_avg is not None:
                        estimated_price = _round(BLEND_RATIO * lowest_listing + BLEND_RATIO * month_avg)
                        confidence = CONFIDENCE_BLENDED
                        reasons.append(
                            f"Listing (${lowest_listing:.2f}) is {divergence * 100:.0f}% above recent sales "
                            f"(${effective_sale_price:.2f}). Blending 50/50 with last-month sold avg "
                            f"(${month_avg:.2f}) → ${estimated_price:.2f}."
                        )
                    else:
                        reasons.append(
                            f"Listing is {divergence * 100:.0f}% above recent sales but no monthly sold data — "
                            "keeping listing price."
                        )
                else:
                    confidence = CONFIDENCE_SOLDS_HIGHER
                    reasons.append(
                        f"Sold price (${effective_sale_price:.2f}) is above lowest listing — "
                        "someone is undercutting. Lowest listing is a competitive price."
                    )
            else:
                confidence = CONFIDENCE_SOLDS_CONFIRM
                reasons.append(
                    f"Sold price (${effective_sale_price:.2f}) confirms listing price "
                    f"({divergence * 100:.0f}% divergence)."
                )

            if sold_stats["count"] >= MIN_SOLDS_FOR_CONFIDENCE:
                confidence = min(confidence + CONFIDENCE_VOLUME_BONUS, CONFIDENCE_MAX)
                reasons.append(f"{sold_stats['count']} recent solds — good data volume.")
            else:
                reasons.append(f"Only {sold_stats['count']} recent sold(s) — limited data.")
        else:
            confidence = CONFIDENCE_NO_SOLDS
            reasons.append("No sold data available. Pricing based on listings only — lower confidence.")

    # ── Step 3: No active listings — fall back to solds ──
    elif sold_stats is not None:
        estimated_price = sold_stats["average"]
        confidence = CONFIDENCE_SOLDS_ONLY
        reasons.append(
            f"No active listings. Using average of {sold_stats['count']} recent sold(s): "
            f"${sold_stats['average']:.2f}."
        )

        if sold_stats["count"] < MIN_SOLDS_FOR_CONFIDENCE:
            confidence = CONFIDENCE_FEW_SOLDS
            manual_check = True
            reasons.append(f"Fewer than {MIN_SOLDS_FOR_CONFIDENCE} solds — flagged for manual review.")

    # ── Step 3b: No data at all ──
    else:
        estimated_price = None
        confidence = 0
        manual_check = True
        reasons.append("No active listings and no sold data. Card is unpriceable automatically.")

    # ── Step 4: Edge case flags ──
    if estimated_price is not None and estimated_price > HIGH_VALUE_THRESHOLD:
        manual_check = True
        reasons.append(
            f"High-value card (${estimated_price:.2f} > ${HIGH_VALUE_THRESHOLD}) — flagged for manual review."
        )

    if has_manual_review_specialty:
        manual_check = True
        reasons.append(
            "Card has manual-review specialty (graded, error, etc.) — flagged for manual review."
        )

    # ── Step 5: Compute derived values ──
    liquid_value = compute_liquid_value(estimated_price) if estimated_price is not None else None

    if sold_stats is not None:
        low_price = sold_stats["min"]
        high_price = sold_stats["max"]
    elif lowest_listing is not None:
        low_price = _round(lowest_listing * LOW_ESTIMATE_MULTIPLIER)
        high_price = _round(lowest_listing * HIGH_ESTIMATE_MULTIPLIER)
    else:
        low_price = None
        high_price = None

    low_price_liquid = compute_liquid_value(low_price) if low_price is not None else None
    high_price_liquid = compute_liquid_value(high_price) if high_price is not None else None

    return PricingResult(
        estimated_price=_round(estimated_price) if estimated_price is not None else None,
        estimated_liquid_value=liquid_value,
        estimated_low_price=low_price,
        estimated_high_price=high_price,
        estimated_low_price_liquid=low_price_liquid,
        estimated_high_price_liquid=high_price_liquid,
        confidence_percent=confidence,
        manual_check_necessary=manual_check,
        algorithm_version=ALGORITHM_VERSION,
        reasoning=" ".join(reasons),
    )


# ─── Liquid Value ────────────────────────────────────────────


def compute_liquid_value(sell_price: float) -> float:
    """Compute liquid value: (sell_price * (1 - FEE_RATE)) - shipping_cost.

    Shipping tiers: <$5 = $0, $5-$25 = $1 (PWE), >$25 = $5 (tracked).
    """
    if sell_price < CHEAP_CARD_THRESHOLD:
        shipping = 0
    elif sell_price > SHIPPING_THRESHOLD:
        shipping = SHIPPING_COST_HIGH
    else:
        shipping = SHIPPING_COST_LOW
    after_fees = sell_price * (1 - FEE_RATE)
    return _round(max(after_fees - shipping, 0))


# ─── Cross-Condition Extrapolation ───────────────────────────


def extrapolate_across_conditions(
    source_price: float,
    source_condition: str,
    target_condition: str,
) -> Optional[float]:
    """Estimate a price for a condition that has no direct data, based on a
    different condition of the same card. 30% discount per tier, compounding.
    """
    effective_source = "NM" if source_condition == "MINT" else source_condition
    effective_target = "NM" if target_condition == "MINT" else target_condition

    if effective_source == effective_target:
        return source_price

    resolved_source_price = _resolve_in_between_source(source_price, effective_source)
    resolved_source_condition = _get_resolved_primary(effective_source)

    if resolved_source_price is None or resolved_source_condition is None:
        return None

    if _is_primary(effective_target):
        return _extrapolate_between_primaries(
            resolved_source_price, resolved_source_condition, effective_target
        )

    neighbors = IN_BETWEEN_CONDITIONS.get(effective_target)
    if not neighbors:
        return None

    better_condition, worse_condition = neighbors
    better_price = _extrapolate_between_primaries(
        resolved_source_price, resolved_source_condition, better_condition
    )
    worse_price = _extrapolate_between_primaries(
        resolved_source_price, resolved_source_condition, worse_condition
    )

    if better_price is None or worse_price is None:
        return None
    return _round((better_price + worse_price) / 2)


def _extrapolate_between_primaries(
    source_price: float, source_condition: str, target_condition: str
) -> Optional[float]:
    try:
        source_idx = PRIMARY_CONDITIONS.index(source_condition)
        target_idx = PRIMARY_CONDITIONS.index(target_condition)
    except ValueError:
        return None

    steps = target_idx - source_idx  # positive = worse condition
    multiplier = CONDITION_STEP_MULTIPLIER ** steps
    return _round(source_price * multiplier)


def _is_primary(condition: str) -> bool:
    return condition in PRIMARY_CONDITIONS


def _resolve_in_between_source(source_price: float, source_condition: str) -> Optional[float]:
    if _is_primary(source_condition):
        return source_price

    neighbors = IN_BETWEEN_CONDITIONS.get(source_condition)
    if not neighbors:
        return None

    # source_price = better * (1 + MULTIPLIER) / 2  =>  better = source_price * 2 / (1 + MULTIPLIER)
    better_price = source_price * 2 / (1 + CONDITION_STEP_MULTIPLIER)
    return _round(better_price)


def _get_resolved_primary(condition: str) -> Optional[str]:
    if _is_primary(condition):
        return condition
    neighbors = IN_BETWEEN_CONDITIONS.get(condition)
    return neighbors[0] if neighbors else None


# ─── Divergence & Blend Helpers ──────────────────────────────


def _recent_solds(solds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Restrict sold data to a recent window so a long history can't skew stats.

    Returns whichever group is LARGER: all sales within the last
    RECENT_SOLDS_DAYS days, or the RECENT_SOLDS_MAX_COUNT most recent sales.
    This keeps fast-moving cards on truly fresh data while still giving illiquid
    cards a floor of recent comps. Mirrors the max(window, count) pattern used
    for divergence.
    """
    if not solds:
        return []

    cutoff_str = (datetime.now() - timedelta(days=RECENT_SOLDS_DAYS)).date().isoformat()
    within_window = [s for s in solds if s["sold_date"] >= cutoff_str]

    most_recent = sorted(solds, key=lambda s: s["sold_date"], reverse=True)[:RECENT_SOLDS_MAX_COUNT]

    return within_window if len(within_window) >= len(most_recent) else most_recent


def _compute_divergence_sale_price(solds: list[dict[str, Any]]) -> Optional[float]:
    """Avg of whichever group has more entries: last 7 days, or 3 most recent."""
    if not solds:
        return None

    cutoff_str = (datetime.now() - timedelta(days=DIVERGENCE_RECENT_DAYS)).date().isoformat()
    last_week = [s for s in solds if s["sold_date"] >= cutoff_str]

    sorted_solds = sorted(solds, key=lambda s: s["sold_date"], reverse=True)
    last3 = sorted_solds[:3]

    group = last_week if len(last_week) >= len(last3) else last3
    if not group:
        return None

    return _round(sum(s["sold_price"] for s in group) / len(group))


def _compute_month_sold_avg(solds: list[dict[str, Any]]) -> Optional[float]:
    if not solds:
        return None

    cutoff_str = (datetime.now() - timedelta(days=BLEND_SOLDS_DAYS)).date().isoformat()
    month_solds = [s for s in solds if s["sold_date"] >= cutoff_str]

    if not month_solds:
        return None

    return _round(sum(s["sold_price"] for s in month_solds) / len(month_solds))


# ─── Helpers ─────────────────────────────────────────────────


def _get_lowest_listing_price(listings: list[dict[str, Any]]) -> Optional[float]:
    """Lowest listing price, accounting for TCGplayer's shipping model.

    For cards under $5: use listed_price only. For cards >= $5: listed + shipping.
    """
    if not listings:
        return None

    lowest = float("inf")
    for listing in listings:
        total = (
            listing["listed_price"]
            if listing["listed_price"] < CHEAP_CARD_THRESHOLD
            else listing["listed_price"] + listing["shipping_price"]
        )
        if total < lowest:
            lowest = total
    return None if lowest == float("inf") else _round(lowest)


def _compute_sold_stats(solds: list[dict[str, Any]]) -> Optional[dict[str, float]]:
    if not solds:
        return None

    prices = sorted(s["sold_price"] for s in solds)
    total = sum(prices)
    mid = len(prices) // 2
    median = (prices[mid - 1] + prices[mid]) / 2 if len(prices) % 2 == 0 else prices[mid]

    return {
        "average": _round(total / len(prices)),
        "median": _round(median),
        "min": prices[0],
        "max": prices[-1],
        "count": len(prices),
    }
