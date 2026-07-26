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
)
from .formatters import parse_finish_from_api
from .transport import RateLimiter, make_client, request_json

_PAGE_SIZE = 25
DEFAULT_MAX_DAYS = 365
_MAX_PAGES = 400  # safety cap (~10k sales) so a runaway total can't loop forever


def _map_sale(sale: dict[str, Any], card_id: str, fallback_condition: str) -> Optional[dict[str, Any]]:
    """Map one raw latestsales row to a sales-table dict, or None to skip.

    Unlike the pricing fetch (which drops them), photo/custom listings are kept
    and flagged via has_image so the graph can ignore them by default but still
    surface them on demand. A non-zero customListingId marks a photo listing
    (seller-uploaded image of the actual card). Rows with no date are skipped.
    """
    order_date = sale.get("orderDate") or ""
    if not order_date:
        return None
    custom_id = sale.get("customListingId")
    is_photo = not (custom_id == "0" or custom_id == 0 or not custom_id)
    return {
        "card_id": card_id,
        "condition": _parse_condition_from_sales_api(sale.get("condition") or fallback_condition or ""),
        # `sales` has no specialty_one column, so 1st Edition and Unlimited
        # merge here. Graph path only — the pricing path keys on specialty.
        "finish": parse_finish_from_api(sale.get("variant") or "")["finish"],
        "source": "tcgplayer",
        "order_date": order_date,
        "purchase_price": sale.get("purchasePrice") or 0,
        "shipping_price": sale.get("shippingPrice") or 0,
        "quantity": sale.get("quantity") or 1,
        "has_image": 1 if is_photo else 0,
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
    *,
    client: httpx.AsyncClient,
    limiter: Optional[RateLimiter],
) -> tuple[list[dict[str, Any]], int]:
    """One raw page of latestsales data plus TCGplayer's totalResults.

    Raises ``transport.RateLimited`` on a 403/429 so the caller can back off.
    """
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
        "listingType": "All",  # include photo/custom listings; flagged via has_image
        "conditions": conditions,
        "languages": [1],  # English
        "limit": _PAGE_SIZE,
        "offset": offset,
    }

    headers = dict(MPAPI_HEADERS)
    if auth_cookie:
        headers["cookie"] = f"TCGAuthTicket_Production={auth_cookie}"

    json_data = await request_json(
        client, "POST", url, limiter=limiter, headers=headers, json=payload
    )
    if json_data is None:  # 404
        return [], 0
    data = json_data.get("data")
    if not isinstance(data, list):
        return [], 0
    return data, int(json_data.get("totalResults") or 0)


async def fetch_sales_history(
    card_id: str,
    condition: Optional[str] = None,
    finish: Optional[str] = None,
    max_days: int = DEFAULT_MAX_DAYS,
    *,
    client: Optional[httpx.AsyncClient] = None,
    limiter: Optional[RateLimiter] = None,
) -> list[dict[str, Any]]:
    """Fetch all sold listings for a card within the last `max_days`.

    With no condition/finish the walk returns every condition+finish mixed (each
    mapped row carries its own), so one call covers the whole card. Walks pages
    newest-first until it crosses the date cutoff or exhausts totalResults.

    Pass a shared ``client``/``limiter`` (from ``transport``) to reuse one
    keep-alive connection and one paced request stream across a whole run; the
    limiter spaces out every page, so no per-page sleep is needed here. When
    omitted (standalone/one-off use) an ephemeral client is created and closed.
    Propagates ``transport.RateLimited`` on a 403/429.
    """
    auth_cookie = _get_auth_cookie()
    cutoff = (datetime.now() - timedelta(days=max_days)).isoformat()

    own_client = client is None
    if own_client:
        client = make_client()

    out: list[dict[str, Any]] = []
    offset = 0
    total: Optional[int] = None
    pages = 0

    try:
        while pages < _MAX_PAGES:
            data, page_total = await _fetch_page(
                card_id, condition, finish, offset, auth_cookie,
                client=client, limiter=limiter,
            )
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
    finally:
        if own_client:
            await client.aclose()

    return out
