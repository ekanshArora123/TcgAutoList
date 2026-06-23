"""fetch_sales_history — Comprehensive raw sold-listing history from TCGplayer.

A separate code path from `fetch_prices.fetch_sold_listings` (which feeds the
pricing algorithm and caps results at a small recent window). This walks the
full `latestsales` pagination back ~1 year and preserves each sale's purchase
price, shipping, quantity, and date as raw rows for the per-card sales graph.

It deliberately does NOT touch the pricing path. It only borrows the shared
transport constants (endpoint headers + condition/finish id maps) from
`fetch_prices`; the pricing functions there are untouched.

Pagination requires the TCGAuthTicket_Production cookie (TCGPLAYER_AUTH_COOKIE).
Without it the sales API returns at most 5 rows and no offset, so history is
limited to that first page.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

import httpx

from .fetch_prices import (
    MPAPI_HEADERS,
    SALES_CONDITION_IDS,
    SALES_VARIANT_IDS,
    _get_auth_cookie,
    _parse_condition_from_sales_api,
    _parse_finish_from_sales_api,
)

_PAGE_SIZE = 25
DEFAULT_MAX_DAYS = 365
_MAX_PAGES = 400  # safety cap (~10k sales) so a runaway total can't loop forever


def _map_sale(sale: dict[str, Any], card_id: str, fallback_condition: str) -> Optional[dict[str, Any]]:
    """Map one raw latestsales row to a sales-table dict, or None to skip.

    Skips custom/photo listings (customListingId != "0") and rows with no date.
    """
    custom_id = sale.get("customListingId")
    if not (custom_id == "0" or custom_id == 0 or not custom_id):
        return None
    order_date = sale.get("orderDate") or ""
    if not order_date:
        return None
    return {
        "card_id": card_id,
        "condition": _parse_condition_from_sales_api(sale.get("condition") or fallback_condition or ""),
        "finish": _parse_finish_from_sales_api(sale.get("variant") or ""),
        "source": "tcgplayer",
        "order_date": order_date,
        "purchase_price": sale.get("purchasePrice") or 0,
        "shipping_price": sale.get("shippingPrice") or 0,
        "quantity": sale.get("quantity") or 1,
    }


def map_sales(data: list[dict[str, Any]], card_id: str, fallback_condition: str, cutoff: str) -> tuple[list[dict[str, Any]], bool]:
    """Map a raw page of sales to rows, dropping any older than `cutoff`.

    Returns (rows, reached_cutoff). `reached_cutoff` is True once a sale older
    than the cutoff is seen — the caller stops paginating (sales are newest-first).
    Pure function (no I/O) so it can be unit-tested without the network.
    """
    rows: list[dict[str, Any]] = []
    reached_cutoff = False
    for sale in data:
        mapped = _map_sale(sale, card_id, fallback_condition)
        if mapped is None:
            continue
        if mapped["order_date"] < cutoff:
            reached_cutoff = True
            continue
        rows.append(mapped)
    return rows, reached_cutoff


async def _fetch_page(
    card_id: str,
    condition: Optional[str],
    finish: Optional[str],
    offset: int,
    auth_cookie: Optional[str],
) -> tuple[list[dict[str, Any]], int]:
    """One raw page of latestsales data plus TCGplayer's totalResults."""
    url = f"https://mpapi.tcgplayer.com/v2/product/{card_id}/latestsales?mpfev=4952"

    conditions: list[int] = []
    if condition:
        cid = SALES_CONDITION_IDS.get(condition)
        if cid:
            conditions.append(cid)

    variants: list[int] = []
    if finish:
        vid = SALES_VARIANT_IDS.get(finish)
        if vid:
            variants.append(vid)

    payload = {
        "variants": variants,
        "listingType": "standard",  # exclude custom/photo listings
        "conditions": conditions,
        "languages": [1],  # English
        "limit": _PAGE_SIZE,
        "offset": offset,
    }

    headers = dict(MPAPI_HEADERS)
    if auth_cookie:
        headers["cookie"] = f"TCGAuthTicket_Production={auth_cookie}"

    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, json=payload)

    if response.status_code != 200:
        if response.status_code == 404:
            return [], 0
        raise RuntimeError(
            f"TCGplayer sales API error: {response.status_code} {response.reason_phrase}"
        )

    json_data = response.json()
    data = json_data.get("data")
    if not isinstance(data, list):
        return [], 0
    return data, int(json_data.get("totalResults") or 0)


async def fetch_sales_history(
    card_id: str,
    condition: Optional[str] = None,
    finish: Optional[str] = None,
    max_days: int = DEFAULT_MAX_DAYS,
) -> list[dict[str, Any]]:
    """Fetch all sold listings for a card within the last `max_days`.

    With no condition/finish the walk returns every condition+finish mixed (each
    mapped row carries its own), so one call covers the whole card. Walks pages
    newest-first until it crosses the date cutoff or exhausts totalResults.
    """
    auth_cookie = _get_auth_cookie()
    cutoff = (datetime.now() - timedelta(days=max_days)).isoformat()

    out: list[dict[str, Any]] = []
    offset = 0
    total: Optional[int] = None
    pages = 0

    while pages < _MAX_PAGES:
        data, page_total = await _fetch_page(card_id, condition, finish, offset, auth_cookie)
        if total is None:
            total = page_total
        if not data:
            break

        rows, reached_cutoff = map_sales(data, card_id, condition or "", cutoff)
        out.extend(rows)

        pages += 1
        offset += _PAGE_SIZE

        if reached_cutoff:
            break
        if not auth_cookie:
            break  # no pagination without auth — first page is all we get
        if total <= 0 or offset >= total:
            break

    return out
