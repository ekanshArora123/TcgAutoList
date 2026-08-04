"""Pricing Algorithm — Lowest-Listing Anchor with a weighted sold blend (v3).

PHILOSOPHY:
  The price of a card is what someone will pay for it. For liquid cards,
  the lowest active listing is the best proxy — it almost always sells.
  For illiquid cards, recent sold prices are a better signal.
  TCGplayer "market price" is unreliable and is NOT used.

The lowest active listing is the anchor. When it disagrees with the sold signal
by more than the divergence threshold, the price becomes a weighted blend of the
listing and an age-weighted sold mean rather than the listing alone:

  * divergence is measured on the plain mean of the last few sales,
  * the sold side's share of the price scales with how large that divergence is
    (listing above solds) or is a flat maximum (listing below solds),
  * within that share, each sale counts by a half-life on its age, so a stale
    history fades out instead of either dominating or being cut off,
  * the listing always retains the remaining share, so it keeps materially
    influencing the price no matter how extreme the gap.

Cards under the cheap-card threshold bypass all of the above and price on the
lowest listing alone — at that level sold prices are shipping noise.

Everything the algorithm reads arrives as a `PricingInputs` (see inputs.py), so
a price can be recomputed from stored data with no network call. All thresholds
come from the `PricingConfig` passed in, defaulting to `DEFAULT_CONFIG`.

See docs/pricing-algorithm.md.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional

from .config import (
    DEFAULT_CONFIG,
    IN_BETWEEN_CONDITIONS,
    PRIMARY_CONDITIONS,
    PricingConfig,
)
from .conditions import is_primary, normalize_condition, primary_neighbors
from .inputs import PricingInputs


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


def sales_weight(divergence: float, listing_is_higher: bool, config: PricingConfig) -> float:
    """The sold side's share of a blended price.

    Above the sold signal the share ramps linearly with divergence, from
    `sales_weight_min` where the blend first trips to `sales_weight_max` at
    `divergence_at_max_sales_weight`, then clamps. Below it, the share is a flat
    `sales_weight_listing_below`: a listing under the market gives no reading on
    *how far* under it deserves to be trusted, so there is nothing to ramp on.
    """
    if not listing_is_higher:
        return config.sales_weight_listing_below

    over = divergence - config.price_divergence_threshold
    weight = config.sales_weight_min + over * config.sales_weight_slope
    return min(max(weight, config.sales_weight_min), config.sales_weight_max)


def compute_price(
    inputs: PricingInputs,
    config: PricingConfig = DEFAULT_CONFIG,
    *,
    has_manual_review_specialty: bool = False,
) -> PricingResult:
    """Compute a listing price from the stored/derived inputs for one variant.

    MINT cards are priced as NM — TCGplayer has no MINT-specific data.
    """
    reasons: list[str] = []
    estimated_price: Optional[float] = None
    confidence: float = 0
    manual_check = False

    if inputs.condition == "MINT":
        reasons.append("MINT condition — pricing as Near Mint.")

    listing = inputs.lowest_listing_price
    weighted = inputs.weighted_sale_price
    signal = inputs.divergence_sale_price

    if listing is not None:
        estimated_price = listing
        confidence = config.confidence_listing_base
        reasons.append(f"Anchored on lowest active listing: ${listing:.2f}.")

        if listing < config.cheap_card_threshold:
            # Sold prices under the free-shipping threshold are dominated by
            # shipping noise, so they are ignored outright rather than blended.
            confidence = config.confidence_cheap
            reasons.append(
                f"Under ${config.cheap_card_threshold} — sold prices are shipping noise at this "
                "level; priced on listings alone."
            )
        elif inputs.has_sales and signal:
            divergence = abs(listing - signal) / signal
            listing_is_higher = listing > signal

            if divergence > config.price_divergence_threshold:
                weight = sales_weight(divergence, listing_is_higher, config)
                estimated_price = _round(weight * weighted + (1 - weight) * listing)
                direction = "above" if listing_is_higher else "below"
                confidence = (
                    config.confidence_blended if listing_is_higher else config.confidence_solds_higher
                )
                reasons.append(
                    f"Listing is {divergence * 100:.0f}% {direction} the recent sold mean "
                    f"(${signal:.2f}). Blending {weight * 100:.0f}% age-weighted sold average "
                    f"(${weighted:.2f}) with {(1 - weight) * 100:.0f}% listing → "
                    f"${estimated_price:.2f}."
                )
            else:
                confidence = config.confidence_solds_confirm
                reasons.append(
                    f"Recent sold mean (${signal:.2f}) confirms the listing "
                    f"({divergence * 100:.0f}% divergence)."
                )

            if inputs.sale_count >= config.min_solds_for_confidence:
                confidence = min(confidence + config.confidence_volume_bonus, config.confidence_max)
                reasons.append(f"{inputs.sale_count} sales in the weighting window — good data volume.")
            else:
                reasons.append(f"Only {inputs.sale_count} sale(s) available — limited data.")
        else:
            confidence = config.confidence_no_solds
            reasons.append("No sold data available. Pricing based on listings only — lower confidence.")

    # ── No active listings — fall back to the age-weighted sold average ──
    elif inputs.has_sales:
        estimated_price = weighted
        confidence = config.confidence_solds_only
        reasons.append(
            f"No active listings. Using the age-weighted average of {inputs.sale_count} "
            f"sale(s): ${weighted:.2f}."
        )

        if inputs.sale_count < config.min_solds_for_confidence:
            confidence = config.confidence_few_solds
            manual_check = True
            reasons.append(
                f"Fewer than {config.min_solds_for_confidence} sales — flagged for manual review."
            )

    # ── No data at all ──
    else:
        estimated_price = None
        confidence = 0
        manual_check = True
        reasons.append("No active listings and no sold data. Card is unpriceable automatically.")

    # ── Edge case flags ──
    if estimated_price is not None and estimated_price > config.high_value_threshold:
        manual_check = True
        reasons.append(
            f"High-value card (${estimated_price:.2f} > ${config.high_value_threshold}) — "
            "flagged for manual review."
        )

    if has_manual_review_specialty:
        manual_check = True
        reasons.append(
            "Card has manual-review specialty (graded, error, etc.) — flagged for manual review."
        )

    # ── Derived values ──
    low_price, high_price = _price_band(inputs, config)

    return PricingResult(
        estimated_price=_round(estimated_price) if estimated_price is not None else None,
        estimated_liquid_value=(
            compute_liquid_value(estimated_price, config) if estimated_price is not None else None
        ),
        estimated_low_price=low_price,
        estimated_high_price=high_price,
        estimated_low_price_liquid=(
            compute_liquid_value(low_price, config) if low_price is not None else None
        ),
        estimated_high_price_liquid=(
            compute_liquid_value(high_price, config) if high_price is not None else None
        ),
        confidence_percent=confidence,
        manual_check_necessary=manual_check,
        algorithm_version=config.algorithm_version,
        reasoning=" ".join(reasons),
    )


def _price_band(
    inputs: PricingInputs, config: PricingConfig
) -> tuple[Optional[float], Optional[float]]:
    """The low/high band bracketing the estimate.

    With both signals present the band is simply the two of them, lower first —
    and because a blended price is a convex combination of exactly those two
    numbers, the estimate always lands inside its own band. With only one
    signal there is nothing to bracket against, so the band falls back to the
    sold range, or to fixed multipliers on the listing.
    """
    listing = inputs.lowest_listing_price
    weighted = inputs.weighted_sale_price

    if listing is not None and weighted is not None:
        return min(listing, weighted), max(listing, weighted)
    if weighted is not None:
        return inputs.min_sale_price, inputs.max_sale_price
    if listing is not None:
        return (
            _round(listing * config.low_estimate_multiplier),
            _round(listing * config.high_estimate_multiplier),
        )
    return None, None


# ─── Liquid Value ────────────────────────────────────────────


def compute_liquid_value(sell_price: float, config: PricingConfig = DEFAULT_CONFIG) -> float:
    """Compute liquid value: (sell_price * (1 - fee_rate)) - shipping_cost.

    Shipping tiers: <$5 = $0, $5-$25 = $1 (PWE), >$25 = $5 (tracked).
    """
    if sell_price < config.cheap_card_threshold:
        shipping = 0.0
    elif sell_price > config.shipping_threshold:
        shipping = config.shipping_cost_high
    else:
        shipping = config.shipping_cost_low
    after_fees = sell_price * (1 - config.fee_rate)
    return _round(max(after_fees - shipping, 0))


# ─── In-Between Conditions ───────────────────────────────────


def interpolate_in_between(
    condition: str,
    better: Optional[PricingResult],
    better_condition: str,
    worse: Optional[PricingResult],
    worse_condition: str,
    config: PricingConfig = DEFAULT_CONFIG,
) -> Optional[PricingResult]:
    """Price an in-between grade (MP-LP) from its two neighboring primaries.

    TCGplayer has no in-between grade, so these SKUs can only be derived. When
    BOTH neighbors priced this run we interpolate — the midpoint of two directly
    measured prices — which is materially better than the compounding-discount
    guess in `extrapolate_across_conditions`, so it is NOT force-flagged for
    review; the neighbors' own flags still carry through.

    With only ONE neighbor available we fall back to true extrapolation, which
    IS always flagged for manual review (see docs/pricing-algorithm.md).

    Returns None when neither neighbor produced a price — the caller leaves the
    SKU's previous estimate alone rather than writing a fabricated one.
    """
    usable = [
        (res, cond)
        for res, cond in ((better, better_condition), (worse, worse_condition))
        if res is not None and res.estimated_price is not None
    ]
    if not usable:
        return None

    if len(usable) == 1:
        source, source_condition = usable[0]
        price = extrapolate_across_conditions(
            source.estimated_price, source_condition, condition, config
        )
        if price is None:
            return None
        reasoning = (
            f"No {condition} tier on TCGplayer and only {source_condition} data available. "
            f"Extrapolated from {source_condition} (${source.estimated_price:.2f}) → ${price:.2f}. "
            "Cross-condition extrapolation — flagged for manual review."
        )
        return _derived_result(price, source.confidence_percent, True, reasoning, source, source, config)

    (better_res, _), (worse_res, _) = usable
    price = _round((better_res.estimated_price + worse_res.estimated_price) / 2)
    reasoning = (
        f"{condition} sits between {better_condition} (${better_res.estimated_price:.2f}) and "
        f"{worse_condition} (${worse_res.estimated_price:.2f}); interpolated to ${price:.2f}. "
        "Both neighbors priced from live data this run."
    )
    return _derived_result(
        price,
        min(better_res.confidence_percent, worse_res.confidence_percent),
        better_res.manual_check_necessary or worse_res.manual_check_necessary,
        reasoning,
        better_res,
        worse_res,
        config,
    )


def relabel_alias(source: PricingResult, condition: str, primary: str) -> PricingResult:
    """Copy a primary's result onto an alias SKU (MINT->NM, DM->DMG).

    The alias is the same physical tier under a different name, so the estimate
    carries over verbatim — only the reasoning changes.
    """
    return replace(
        source,
        reasoning=f"{condition} has no distinct TCGplayer tier — priced as {primary}. {source.reasoning}",
    )


def flag_error_variant(source: PricingResult, specialty_two: str) -> PricingResult:
    """Force manual review on an error variant that inherited a base price.

    TCGplayer sells no miscut/holo-bleed/etc. variant, so the only estimate
    available for one is the plain card's — which is exactly why a human has to
    look: the error is usually worth a multiple of the base card, sometimes
    less. `compute_price`'s own `has_manual_review_specialty` can't cover this,
    because the SKU being priced there is always the plain (`specialty_two =
    'None'`) one; the error SKUs are derived afterwards from its result.
    """
    return replace(
        source,
        manual_check_necessary=True,
        reasoning=(
            f"{source.reasoning} Error variant ({specialty_two}) — no TCGplayer tier exists "
            "for it, so this is the base variant's price; flagged for manual review."
        ),
    )


def _derived_result(
    price: float,
    confidence: float,
    manual_check: bool,
    reasoning: str,
    low_source: PricingResult,
    high_source: PricingResult,
    config: PricingConfig = DEFAULT_CONFIG,
) -> PricingResult:
    """Assemble a PricingResult for a price derived from other conditions.

    Applies the same high-value review rule `compute_price` does, so a derived
    estimate can't slip past the threshold that a direct one would trip.
    """
    if price > config.high_value_threshold:
        manual_check = True
        reasoning += (
            f" High-value card (${price:.2f} > ${config.high_value_threshold}) — "
            "flagged for manual review."
        )

    low = _mean_of(low_source.estimated_low_price, high_source.estimated_low_price)
    high = _mean_of(low_source.estimated_high_price, high_source.estimated_high_price)

    return PricingResult(
        estimated_price=price,
        estimated_liquid_value=compute_liquid_value(price, config),
        estimated_low_price=low,
        estimated_high_price=high,
        estimated_low_price_liquid=compute_liquid_value(low, config) if low is not None else None,
        estimated_high_price_liquid=compute_liquid_value(high, config) if high is not None else None,
        confidence_percent=confidence,
        manual_check_necessary=manual_check,
        algorithm_version=config.algorithm_version,
        reasoning=reasoning,
    )


def _mean_of(a: Optional[float], b: Optional[float]) -> Optional[float]:
    present = [v for v in (a, b) if v is not None]
    return _round(sum(present) / len(present)) if present else None


# ─── Cross-Condition Extrapolation ───────────────────────────


def extrapolate_across_conditions(
    source_price: float,
    source_condition: str,
    target_condition: str,
    config: PricingConfig = DEFAULT_CONFIG,
) -> Optional[float]:
    """Estimate a price for a condition that has no direct data, based on a
    different condition of the same card. 30% discount per tier, compounding.

    Both ends go through the shared condition vocabulary first, so alternate
    spellings (MINT -> NM, DM -> DMG, stray case/whitespace) resolve instead of
    falling off the tier ladder and returning None.
    """
    effective_source = normalize_condition(source_condition)
    effective_target = normalize_condition(target_condition)

    if effective_source == effective_target:
        return source_price

    resolved_source_price = _resolve_in_between_source(source_price, effective_source, config)
    resolved_source_condition = _get_resolved_primary(effective_source)

    if resolved_source_price is None or resolved_source_condition is None:
        return None

    if is_primary(effective_target):
        return _extrapolate_between_primaries(
            resolved_source_price, resolved_source_condition, effective_target, config
        )

    neighbors = primary_neighbors(effective_target)
    if not neighbors:
        return None

    better_condition, worse_condition = neighbors
    better_price = _extrapolate_between_primaries(
        resolved_source_price, resolved_source_condition, better_condition, config
    )
    worse_price = _extrapolate_between_primaries(
        resolved_source_price, resolved_source_condition, worse_condition, config
    )

    if better_price is None or worse_price is None:
        return None
    return _round((better_price + worse_price) / 2)


def _extrapolate_between_primaries(
    source_price: float, source_condition: str, target_condition: str, config: PricingConfig
) -> Optional[float]:
    try:
        source_idx = PRIMARY_CONDITIONS.index(source_condition)
        target_idx = PRIMARY_CONDITIONS.index(target_condition)
    except ValueError:
        return None

    steps = target_idx - source_idx  # positive = worse condition
    multiplier = config.condition_step_multiplier ** steps
    return _round(source_price * multiplier)


def _resolve_in_between_source(
    source_price: float, source_condition: str, config: PricingConfig
) -> Optional[float]:
    if is_primary(source_condition):
        return source_price

    neighbors = primary_neighbors(source_condition)
    if not neighbors:
        return None

    # source_price = better * (1 + MULTIPLIER) / 2  =>  better = source_price * 2 / (1 + MULTIPLIER)
    better_price = source_price * 2 / (1 + config.condition_step_multiplier)
    return _round(better_price)


def _get_resolved_primary(condition: str) -> Optional[str]:
    if is_primary(condition):
        return condition
    neighbors = primary_neighbors(condition)
    return neighbors[0] if neighbors else None


__all__ = [
    "PricingResult",
    "compute_price",
    "compute_liquid_value",
    "sales_weight",
    "interpolate_in_between",
    "relabel_alias",
    "flag_error_variant",
    "extrapolate_across_conditions",
    "IN_BETWEEN_CONDITIONS",
]
