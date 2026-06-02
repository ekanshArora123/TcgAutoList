"""Tier Router — evaluates pricing confidence + flags to determine which
processing tier a card should go through. Ported from tierRouter.ts.

Tier 1 (>=80% confidence): No LLM. Template listing. ~80% of cards.
Tier 2 (40-79% confidence): LLM with scoped context. ~15% of cards.
Tier 3 (<40% confidence): LLM with full context. ~5% of cards.
"""

from __future__ import annotations

from typing import Any, Optional

from shared.types import DEFAULT_TIER_CONFIG, TierConfig


def route_to_tier(
    price: Optional[dict[str, Any]],
    specialty_two: str,
    config: TierConfig = DEFAULT_TIER_CONFIG,
) -> dict[str, Any]:
    """Route a card to the appropriate processing tier.

    Returns {"tier": 1|2|3, "reason": str}.
    """
    # No price data at all -> Tier 3
    if not price or price.get("estimated_price") is None:
        return {"tier": 3, "reason": "No price data available — full LLM analysis needed."}

    confidence = price.get("confidence_percent") or 0
    price_value = price["estimated_price"]

    # Specialty cards (graded, errors) always need LLM review
    if specialty_two != "None":
        return {"tier": 3, "reason": f"Specialty card ({specialty_two}) — full LLM analysis needed."}

    # Very high value -> Tier 3
    if price_value > config.very_high_value_threshold:
        return {
            "tier": 3,
            "reason": (
                f"Very high value (${price_value:.2f} > ${config.very_high_value_threshold}) — "
                "full LLM analysis needed."
            ),
        }

    # High value OR manual check flagged -> at least Tier 2
    if price_value > config.high_value_threshold or price.get("manual_check_necessary"):
        if confidence >= config.tier1_min_confidence:
            return {
                "tier": 2,
                "reason": "High value or manual review flagged despite high confidence — scoped LLM review.",
            }

    # Tier 1: high confidence, not high value
    if confidence >= config.tier1_min_confidence:
        return {"tier": 1, "reason": f"High confidence ({confidence}%) — dumb pipe."}

    # Tier 2: medium confidence
    if confidence >= config.tier2_min_confidence:
        return {"tier": 2, "reason": f"Medium confidence ({confidence}%) — scoped LLM review."}

    # Tier 3: low confidence
    return {"tier": 3, "reason": f"Low confidence ({confidence}%) — full LLM analysis needed."}
