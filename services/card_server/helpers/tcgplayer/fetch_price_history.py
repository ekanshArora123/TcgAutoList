"""fetch_price_history — TCGplayer "market price" history (the product-page graph).

Hits the public Infinite price-history API, the same call the TCGplayer product
page makes to draw its market-price-over-time chart:

    GET https://infinite-api.tcgplayer.com/price/history/{productId}/detailed?range=annual

The response groups weekly buckets by condition + variant (printing). Each
bucket carries a `marketPrice` (and low/high sale price, quantity, transactions)
as JSON strings. No auth required.

Graph data only — kept entirely out of the pricing path. Borrows just the
condition/finish parse helpers from fetch_prices.
"""

from __future__ import annotations

from typing import Any, Optional

import httpx

from .fetch_prices import _parse_condition_from_sales_api, _parse_finish_from_sales_api

INFINITE_HEADERS: dict[str, str] = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "en-US,en;q=0.9",
    "origin": "https://www.tcgplayer.com",
    "referer": "https://www.tcgplayer.com/",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
}


def _f(v: Any) -> Optional[float]:
    """Parse a possibly-stringy numeric to float, or None."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _i(v: Any) -> Optional[int]:
    f = _f(v)
    return int(f) if f is not None else None


def map_price_history(results: list[dict[str, Any]], card_id: str) -> list[dict[str, Any]]:
    """Flatten the API's per-(condition,variant) bucket arrays into table rows.

    Pure function (no I/O) so it can be unit-tested without the network.
    """
    rows: list[dict[str, Any]] = []
    for entry in results or []:
        condition = _parse_condition_from_sales_api(entry.get("condition") or "")
        finish = _parse_finish_from_sales_api(entry.get("variant") or "")
        for b in entry.get("buckets") or []:
            date = b.get("bucketStartDate") or ""
            if not date:
                continue
            rows.append(
                {
                    "card_id": card_id,
                    "condition": condition,
                    "finish": finish,
                    "source": "tcgplayer",
                    "bucket_date": date,
                    "market_price": _f(b.get("marketPrice")),
                    "low_sale_price": _f(b.get("lowSalePrice")),
                    "high_sale_price": _f(b.get("highSalePrice")),
                    "quantity_sold": _i(b.get("quantitySold")),
                    "transaction_count": _i(b.get("transactionCount")),
                }
            )
    return rows


async def fetch_price_history(card_id: str, range_: str = "annual") -> list[dict[str, Any]]:
    """Fetch the market-price history for a card across all conditions/finishes."""
    url = f"https://infinite-api.tcgplayer.com/price/history/{card_id}/detailed?range={range_}"

    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=INFINITE_HEADERS)

    if response.status_code != 200:
        if response.status_code == 404:
            return []
        raise RuntimeError(
            f"TCGplayer price history API error: {response.status_code} {response.reason_phrase}"
        )

    data = response.json()
    return map_price_history(data.get("result"), card_id)
