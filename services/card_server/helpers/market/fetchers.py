"""Market Data Fetchers — All external API calls for market data collection.

Delegates all TCGplayer logic to the tcgplayer/ helpers and adds
market-collection-oriented wrappers. Ported from fetchers.ts.

A MarketFetchResult is a dict:
  {cardId, condition, finish, source, activeListings, soldListings}
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Optional

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


async def fetch_market_data_for_card(
    tcgplayer_id: str,
    variants: Optional[list[tuple[str, str, str]]] = None,
    set_name: Optional[str] = None,
    delay_ms: int = 0,
) -> list[dict[str, Any]]:
    """Fetch all market data for a single card.

    `variants` are the (condition, finish, specialty_one) combinations to pull
    ACTIVE LISTINGS for — one API call each, because TCGplayer's listing search
    filters server-side and returns nothing about the variants you didn't ask
    for. Omitting it previously meant the endpoint silently defaulted to Near
    Mint + Normal, so every other variant was recorded with zero listings and
    priced from solds alone. Callers pass the variants they actually care about
    (the collector: the ones the owner holds) to keep the fan-out small.

    `set_name` must be threaded through: WOTC-era sets use "Unlimited" printing
    names, and without it those cards match no listings at all.

    Sold listings are fetched ONCE — that endpoint returns every condition and
    finish in a single response — then grouped and attached to the matching
    variant. Variants that appear only in the sold data still get a result row,
    so sold-only coverage is unchanged.
    """
    all_solds = await fetch_sold_listings(tcgplayer_id)
    keyed_solds = _group_by_condition_finish(all_solds)

    if variants is None:
        # No caller-supplied plan (e.g. --cohort, where nothing is owned): take
        # the variants the sold data reveals, falling back to NM/Regular so a
        # card with no sales at all still gets one listing probe.
        variants = [(c, f, "None") for c, f in _keys_to_pairs(keyed_solds)] or [
            ("NM", "Regular", "None")
        ]

    results: list[dict[str, Any]] = []
    seen: set[str] = set()

    for i, (condition, finish, specialty_one) in enumerate(variants):
        if i > 0 and delay_ms > 0:
            await asyncio.sleep(delay_ms / 1000)

        listings = await fetch_active_listings(
            tcgplayer_id, condition, finish, set_name, specialty_one
        )
        key = f"{condition}|{finish}"
        seen.add(key)
        results.append(
            {
                "cardId": tcgplayer_id,
                "condition": condition,
                "finish": finish,
                "specialtyOne": specialty_one,
                "source": "tcgplayer",
                "activeListings": listings,
                "soldListings": keyed_solds.get(key, []),
            }
        )

    # Variants we have solds for but were not asked to fetch listings for: keep
    # them so the snapshot table retains its sold-side coverage.
    for key, solds in keyed_solds.items():
        if key in seen:
            continue
        condition, finish = key.split("|")
        results.append(
            {
                "cardId": tcgplayer_id,
                "condition": condition,
                "finish": finish,
                "specialtyOne": "None",
                "source": "tcgplayer",
                "activeListings": [],
                "soldListings": solds,
            }
        )

    return results


def _keys_to_pairs(keyed: dict[str, list[dict[str, Any]]]) -> list[tuple[str, str]]:
    return [tuple(k.split("|")) for k in keyed]  # type: ignore[misc]


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
