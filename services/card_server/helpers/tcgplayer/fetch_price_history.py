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

from .fetch_prices import _parse_condition_from_sales_api
from .formatters import parse_finish_from_api
from .transport import RateLimiter, make_client, request_json

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
        # market_price_history has no specialty_one column, so 1st Edition and
        # Unlimited buckets still merge here. That is the graph path only — the
        # pricing path keys on specialty (see _group_by_condition_finish).
        finish = parse_finish_from_api(entry.get("variant") or "")["finish"]
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


async def fetch_price_history(
    card_id: str,
    range_: str = "annual",
    *,
    client: Optional[httpx.AsyncClient] = None,
    limiter: Optional[RateLimiter] = None,
) -> list[dict[str, Any]]:
    """Fetch the market-price history for a card across all conditions/finishes.

    Pass a shared ``client``/``limiter`` (from ``transport``) to reuse the run's
    keep-alive connection and paced request stream; otherwise an ephemeral
    client is created. Propagates ``transport.RateLimited`` on a 403/429.
    """
    url = f"https://infinite-api.tcgplayer.com/price/history/{card_id}/detailed?range={range_}"

    own_client = client is None
    if own_client:
        client = make_client()
    try:
        data = await request_json(
            client, "GET", url, limiter=limiter, headers=INFINITE_HEADERS
        )
    finally:
        if own_client:
            await client.aclose()

    if data is None:  # 404
        return []
    return map_price_history(data.get("result"), card_id)
