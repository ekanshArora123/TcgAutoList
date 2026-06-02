"""Message renderer — formats card details, prices, and status into Telegram
messages with inline keyboards. Ported from renderer.ts.

Operates on plain dicts (InventoryDetail / Price shapes).
"""

from __future__ import annotations

import re
from typing import Any, Optional


def render_card_details(detail: dict[str, Any]) -> str:
    """Format card details for display."""
    lines = [
        f"*{_esc_md(detail['card_name'])}*",
        f"Set: {_esc_md(detail.get('set_name') or 'Unknown')}",
        f"Condition: {_esc_md(detail['condition'])}  |  Finish: {_esc_md(detail['finish'])}",
    ]

    if detail.get("rarity"):
        lines.append(f"Rarity: {_esc_md(detail['rarity'])}")
    if detail.get("card_number"):
        lines.append(f"Number: {_esc_md(detail['card_number'])}")
    if detail.get("tags"):
        lines.append(f"Tags: {_esc_md(detail['tags'])}")

    return "\n".join(lines)


def render_price_info(price: dict[str, Any]) -> str:
    """Format price info for display."""
    if price.get("estimated_price") is None:
        return f"Price: *Unpriceable*\nConfidence: {price.get('confidence_percent') or 0}%"

    lines = [
        f"Price: *${price['estimated_price']:.2f}*",
        f"Liquid Value: ${price['estimated_liquid_value']:.2f}"
        if price.get("estimated_liquid_value") is not None
        else "Liquid Value: $N/A",
        f"Confidence: {price.get('confidence_percent')}%",
    ]

    if price.get("estimated_low_price") is not None and price.get("estimated_high_price") is not None:
        lines.append(
            f"Range: ${price['estimated_low_price']:.2f} — ${price['estimated_high_price']:.2f}"
        )

    if price.get("manual_check_necessary"):
        lines.append("⚠ Manual review flagged")

    return "\n".join(lines)


def render_card_summary(detail: dict[str, Any], price: Optional[dict[str, Any]]) -> str:
    """Format a combined card + price summary for the /next flow."""
    parts = [render_card_details(detail)]
    if price:
        parts.append("")
        parts.append(render_price_info(price))
    return "\n".join(parts)


def render_status(current: Optional[dict[str, Any]], stats: dict[str, Any]) -> str:
    """Format status update message."""
    lines = ["*Collection Status*", ""]

    for status, count in stats["by_status"].items():
        lines.append(f"{_esc_md(status)}: {count}")
    lines.append(f"Total: {stats['total']}")

    if current:
        lines.append("")
        lines.append("*Currently processing:*")
        lines.append(f"{_esc_md(current['card_name'])} ({_esc_md(current.get('set_name') or 'Unknown')})")
        lines.append(f"Status: {_esc_md(current['status'])}")

    return "\n".join(lines)


def build_photo_request_keyboard(inventory_id: int) -> list[list[dict[str, str]]]:
    """Build inline keyboard for photo request."""
    return [[{"text": "Skip this card", "callback_data": f"skip:{inventory_id}"}]]


def build_review_keyboard(inventory_id: int) -> list[list[dict[str, str]]]:
    """Build inline keyboard for manual review."""
    return [
        [
            {"text": "Approve", "callback_data": f"approve:{inventory_id}"},
            {"text": "Skip", "callback_data": f"skip:{inventory_id}"},
        ],
        [
            {"text": "Override Price", "callback_data": f"override:{inventory_id}"},
            {"text": "Details", "callback_data": f"details:{inventory_id}"},
        ],
    ]


def render_photo_request(detail: dict[str, Any], side: str) -> str:
    """Format photo request message."""
    return "\n".join(
        [
            f"📷 Send *{side}* photo for:",
            f"*{_esc_md(detail['card_name'])}*",
            f"{_esc_md(detail.get('set_name') or 'Unknown')} — "
            f"{_esc_md(detail['condition'])} {_esc_md(detail['finish'])}",
        ]
    )


def render_listing_confirmation(detail: dict[str, Any], price: float) -> str:
    """Render listing confirmation."""
    return "\n".join(
        [
            "✅ *Listing created*",
            f"{_esc_md(detail['card_name'])} — {_esc_md(detail.get('set_name') or 'Unknown')}",
            f"{_esc_md(detail['condition'])} {_esc_md(detail['finish'])}",
            f"Price: ${price:.2f}",
        ]
    )


_MD_SPECIAL = re.compile(r"([_*\[\]()~`>#+\-=|{}.!])")


def _esc_md(text: str) -> str:
    """Escape Markdown special characters for Telegram."""
    return _MD_SPECIAL.sub(r"\\\1", text)
