"""Tier 1 Pipeline — Dumb pipe for high-confidence cards.

No LLM involvement. Builds a template listing from DB data, requests a photo via
Telegram, and posts to eBay. Handles ~80% of cards with zero token cost.
Ported from tier1Pipeline.ts.
"""

from __future__ import annotations

from typing import Any

from ebay_service.service import ListingTemplate, post_to_ebay

__all__ = ["build_listing_template", "post_to_ebay", "ListingTemplate"]

_CONDITION_MAP = {
    "MINT": "Brand New",
    "NM": "Near Mint",
    "LP-NM": "Near Mint",  # List as NM on eBay (in-between goes up)
    "LP": "Lightly Played",
    "MP-LP": "Lightly Played",
    "MP": "Moderately Played",
    "HP-MP": "Moderately Played",
    "HP": "Heavily Played",
    "DM": "Damaged",
    "DMG": "Damaged",
}


def build_listing_template(
    detail: dict[str, Any], price: dict[str, Any], photo_paths: list[str]
) -> ListingTemplate:
    """Build a template eBay listing from card data and price. Pure code — no LLM."""
    ebay_condition = _CONDITION_MAP.get(detail["condition"], "Near Mint")
    return ListingTemplate(
        title=_build_title(detail),
        description=_build_description(detail, price),
        price=price["estimated_price"],
        condition=ebay_condition,
        category="Pokemon Individual Cards",
        photo_paths=photo_paths,
    )


def _build_title(detail: dict[str, Any]) -> str:
    """Build eBay listing title (max 80 chars)."""
    finish = detail["finish"]
    parts = [
        detail["card_name"],
        detail.get("card_number") or "",
        detail.get("set_name") or "",
        finish if finish != "Regular" else "",
        "Pokemon Card",
    ]
    title = " ".join(p for p in parts if p)

    if len(title) > 80:
        # Trim set name first, then card number.
        parts = [detail["card_name"], finish if finish != "Regular" else "", "Pokemon Card"]
        title = " ".join(p for p in parts if p)

    return title[:80]


def _build_description(detail: dict[str, Any], price: dict[str, Any]) -> str:
    """Build eBay listing description."""
    lines = [
        f"{detail['card_name']}",
        "",
        f"Set: {detail.get('set_name') or 'Unknown'}",
        f"Card Number: {detail.get('card_number') or 'N/A'}",
        f"Condition: {detail['condition']}",
        f"Finish: {detail['finish']}",
        f"Rarity: {detail.get('rarity') or 'N/A'}",
    ]

    if detail.get("tags"):
        lines.append("")
        lines.append(f"Note: {detail['tags']}")

    lines.append("")
    lines.append("Ships in a penny sleeve + toploader in a PWE (plain white envelope).")
    lines.append("Cards over $25 ship in a tracked bubble mailer.")

    return "\n".join(lines)
