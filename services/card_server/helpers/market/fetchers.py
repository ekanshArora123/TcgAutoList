"""Market Data Fetchers — All external API calls for market data collection.

Delegates all TCGplayer logic to the tcgplayer/ helpers and adds
market-collection-oriented wrappers. Ported from fetchers.ts.

A MarketFetchResult is a dict:
  {cardId, condition, finish, source, activeListings, soldListings}
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from ..tcgplayer.fetch_card_info import (
    fetch_card_info,
    fetch_card_metadata,
    fetch_card_price_info,
)
from ..tcgplayer.fetch_prices import (
    fetch_active_listings,
    fetch_set_info,
    fetch_sold_listings,
)

# Re-exports so consumers can import from this module.
__all__ = [
    "fetch_active_listings",
    "fetch_sold_listings",
    "fetch_set_info",
    "fetch_card_info",
    "fetch_card_metadata",
    "fetch_card_price_info",
    "fetch_market_data_for_card",
    "fetch_market_data_for_variant",
    "fetch_market_data_batch",
    "MarketFetchResult",
]

# A MarketFetchResult is represented as a plain dict (see module docstring).
MarketFetchResult = dict


async def fetch_market_data_for_card(tcgplayer_id: str) -> list[dict[str, Any]]:
    """Fetch all market data for a single card across all conditions/finishes."""
    all_listings, all_solds = await asyncio.gather(
        fetch_active_listings(tcgplayer_id),
        fetch_sold_listings(tcgplayer_id),
    )

    keyed_listings = _group_by_condition_finish(all_listings)
    keyed_solds = _group_by_condition_finish(all_solds)

    all_keys = set(keyed_listings.keys()) | set(keyed_solds.keys())

    results: list[dict[str, Any]] = []
    for key in all_keys:
        condition, finish = key.split("|")
        results.append(
            {
                "cardId": tcgplayer_id,
                "condition": condition,
                "finish": finish,
                "source": "tcgplayer",
                "activeListings": keyed_listings.get(key, []),
                "soldListings": keyed_solds.get(key, []),
            }
        )
    return results


async def fetch_market_data_for_variant(
    tcgplayer_id: str, condition: str, finish: str
) -> dict[str, Any]:
    """Fetch market data for a specific condition+finish of a card."""
    active_listings, sold_listings = await asyncio.gather(
        fetch_active_listings(tcgplayer_id, condition, finish),
        fetch_sold_listings(tcgplayer_id, condition, finish),
    )

    return {
        "cardId": tcgplayer_id,
        "condition": condition,
        "finish": finish,
        "source": "tcgplayer",
        "activeListings": active_listings,
        "soldListings": sold_listings,
    }


async def fetch_market_data_batch(
    tcgplayer_ids: list[str], delay_ms: int = 500
) -> list[dict[str, Any]]:
    """Fetch market data for multiple cards sequentially with rate limiting."""
    all_results: list[dict[str, Any]] = []
    for i, cid in enumerate(tcgplayer_ids):
        if i > 0 and delay_ms > 0:
            await asyncio.sleep(delay_ms / 1000)
        all_results.extend(await fetch_market_data_for_card(cid))
    return all_results


def _group_by_condition_finish(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        key = f"{item['condition']}|{item['finish']}"
        grouped.setdefault(key, []).append(item)
    return grouped
