"""Tool Bridge — Tool definitions for the Claude API (Tier 2/3).

PSEUDOCODE — Not yet implemented. Bridges card-server tools into Claude API tool
format. Tier 2 gets a scoped subset, Tier 3 gets everything. Ported from tools.ts.
"""

from __future__ import annotations

from typing import Any


def get_tier2_tools() -> list[dict[str, Any]]:
    """Tier 2 tools — pricing and research only.

    The LLM can fetch listings/solds/card info and compare conditions, but CANNOT
    modify cards/inventory/prices, access eBay tools, or mark cards listed/sold.
    """
    # PSEUDOCODE — see tools.ts for the intended tool schemas:
    #   fetch_active_listings, fetch_sold_listings, get_card_info,
    #   compare_conditions, set_price_decision
    return []


def get_tier3_tools() -> list[dict[str, Any]]:
    """Tier 3 tools — full access.

    Everything from Tier 2 plus full card-server tools, eBay research tools, and
    the ability to request manual review or ask the user via Telegram.
    """
    # PSEUDOCODE — get_tier2_tools() plus search_cards, search_ebay_solds,
    #   request_manual_review, ask_user
    return []


async def execute_tool(tool_name: str, tool_input: Any) -> Any:
    """Execute a tool call from the LLM. Routes to the appropriate handler.

    PSEUDOCODE — not implemented.
    """
    return {"error": "Tool execution not implemented"}
