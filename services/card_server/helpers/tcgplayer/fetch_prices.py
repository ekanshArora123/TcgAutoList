"""fetch_prices — Fetches pricing data from TCGplayer for a product/SKU.

Handles requests to TCGplayer for active listing prices, recent sold listings,
and set catalog info. Async via httpx. Ported from fetchPrices.ts.

Listings/solds are returned as plain dicts.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Optional

import httpx

from ..pricing.config import MIN_SELLER_RATING, MIN_SELLER_SALES
from .formatters import format_condition_for_api, format_finish_for_api

# ─── TCGplayer Listings API ──────────────────────────────────

TCGPLAYER_HEADERS: dict[str, str] = {
    "authority": "mp-search-api.tcgplayer.com",
    "accept": "application/json, text/plain, */*",
    "accept-language": "en-US,en;q=0.9",
    "content-type": "application/json",
    "origin": "https://www.tcgplayer.com",
    "referer": "https://www.tcgplayer.com/",
    "sec-ch-ua": '"Not A Brand";v="99", "Google Chrome";v="121", "Chromium";v="121"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
}


def _build_listings_payload(conditions: list[str], finish: str, offset: int = 0, size: int = 50) -> dict:
    return {
        "filters": {
            "term": {
                "sellerStatus": "Live",
                "channelId": 0,
                "language": ["English"],
                "condition": conditions,
                "printing": finish,
                "listingType": "standard",
            },
            "range": {"quantity": {"gte": 1}},
            "exclude": {"channelExclusion": 0},
        },
        "from": offset,
        "size": size,
        "sort": {"field": "price+shipping", "order": "asc"},
        "context": {"shippingCountry": "US", "cart": {}},
        "aggregations": ["listingType"],
    }


async def fetch_active_listings(
    tcgplayer_id: str,
    condition: Optional[str] = None,
    finish: Optional[str] = None,
    set_name: Optional[str] = None,
    specialty_one: Optional[str] = None,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Fetch active listings from TCGplayer's internal search API.

    Sorted by price+shipping ascending. Low-reputation sellers filtered out.
    """
    api_conditions = format_condition_for_api(condition) if condition else ["Near Mint"]
    api_finish = format_finish_for_api(finish or "Regular", specialty_one or "None", set_name or "")

    url = f"https://mp-search-api.tcgplayer.com/v1/product/{tcgplayer_id}/listings?mpfev=2163"
    payload = _build_listings_payload(api_conditions, api_finish, offset)

    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=TCGPLAYER_HEADERS, json=payload)

    if response.status_code != 200:
        if response.status_code == 404:
            return []
        raise RuntimeError(f"TCGplayer API error: {response.status_code} {response.reason_phrase}")

    json_data = response.json()
    results = json_data.get("results")
    if not results or not results[0].get("results"):
        return []

    return _filter_and_map_listings(
        results[0]["results"], tcgplayer_id, condition or "NM", finish or "Regular"
    )


def _filter_and_map_listings(
    raw: list[dict], tcgplayer_id: str, condition: str, finish: str
) -> list[dict[str, Any]]:
    """Filter out low-quality sellers (rating > 80, sales > 30 or ending with '+')."""
    results: list[dict[str, Any]] = []

    for row in raw:
        rating = _parse_numeric(row.get("sellerRating"))
        sales = row.get("sellerSales")

        if rating is None or rating < MIN_SELLER_RATING:
            continue
        if sales is None or sales == "NA":
            continue
        sales_numeric = _parse_numeric(sales)
        sales_ok = sales.endswith("+") or (sales_numeric is not None and sales_numeric > MIN_SELLER_SALES)
        if not sales_ok:
            continue

        results.append(
            {
                "tcgplayer_id": tcgplayer_id,
                "condition": condition,
                "finish": finish,
                "listed_price": row.get("price") or 0,
                "shipping_price": row.get("shippingPrice") or 0,
                "seller_name": row.get("sellerName"),
                "seller_feedback_count": None,
                "seller_rating": rating,
                "seller_sales": sales,
            }
        )

    results.sort(key=lambda r: r["listed_price"] + r["shipping_price"])
    return results


def _parse_numeric(val: Any) -> Optional[float]:
    if isinstance(val, (int, float)):
        return val
    if isinstance(val, str):
        cleaned = val.replace("+", "").replace(",", "")
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


# ─── TCGplayer Sales API ─────────────────────────────────────

MPAPI_HEADERS: dict[str, str] = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "en-US,en;q=0.9",
    "content-type": "application/json",
    "origin": "https://www.tcgplayer.com",
    "referer": "https://www.tcgplayer.com/",
    "sec-ch-ua": '"Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
}

SALES_CONDITION_IDS: dict[str, int] = {
    "NM": 1, "Near Mint": 1,
    "LP": 2, "Lightly Played": 2,
    "MP": 3, "Moderately Played": 3,
    "HP": 4, "Heavily Played": 4,
    "DMG": 5, "DM": 5, "Damaged": 5,
}

SALES_VARIANT_IDS: dict[str, int] = {
    "Normal": 10, "Regular": 10,
    "Holofoil": 11, "Holo": 11,
    "Reverse Holofoil": 77, "Reverse-Holo": 77,
}


def _get_auth_cookie() -> Optional[str]:
    """TCGAuthTicket_Production cookie value; enables full sold data pagination."""
    return os.environ.get("TCGPLAYER_AUTH_COOKIE")


async def fetch_sold_listings(
    tcgplayer_id: str,
    condition: Optional[str] = None,
    finish: Optional[str] = None,
    max_results: int = 25,
) -> list[dict[str, Any]]:
    """Fetch recent sold listings from TCGplayer's sales API.

    With auth cookie: 25 per page with pagination. Without: 5 max per request,
    so fan out by condition when no condition is specified.
    """
    auth_cookie = _get_auth_cookie()

    if auth_cookie:
        return await _fetch_sold_listings_paginated(
            tcgplayer_id, condition, finish, max_results, auth_cookie
        )

    if not condition:
        conditions = ["NM", "LP", "MP", "HP", "DMG"]
        pages = await asyncio.gather(
            *[_fetch_sold_listings_page(tcgplayer_id, c, finish, 0) for c in conditions]
        )
        return [item for page in pages for item in page]

    return await _fetch_sold_listings_page(tcgplayer_id, condition, finish, 0)


async def _fetch_sold_listings_paginated(
    tcgplayer_id: str,
    condition: Optional[str],
    finish: Optional[str],
    max_results: int,
    auth_cookie: str,
) -> list[dict[str, Any]]:
    """Walk the sales pages (25 at a time) by offset from 0 up to totalResults.

    Drives pagination off TCGplayer's `totalResults`, NOT the per-page row count.
    A page may yield fewer than 25 mapped rows because custom/photo listings are
    filtered out, so the filtered length can't be used to detect the last page —
    doing so stops after page one. We advance offset by the request page size and
    stop once offset reaches totalResults (or max_results is satisfied).
    """
    all_results: list[dict[str, Any]] = []
    page_size = 25
    offset = 0
    total_results: Optional[int] = None

    while len(all_results) < max_results:
        items, total = await _fetch_sold_listings_page_with_total(
            tcgplayer_id, condition, finish, offset, auth_cookie
        )
        if total_results is None:
            total_results = total
        all_results.extend(items)
        offset += page_size
        if total_results <= 0 or offset >= total_results:
            break

    return all_results[:max_results]


async def _fetch_sold_listings_page(
    tcgplayer_id: str,
    condition: Optional[str],
    finish: Optional[str],
    offset: int,
    auth_cookie: Optional[str] = None,
) -> list[dict[str, Any]]:
    """One page of mapped sold listings (without pagination metadata)."""
    items, _total = await _fetch_sold_listings_page_with_total(
        tcgplayer_id, condition, finish, offset, auth_cookie
    )
    return items


async def _fetch_sold_listings_page_with_total(
    tcgplayer_id: str,
    condition: Optional[str],
    finish: Optional[str],
    offset: int,
    auth_cookie: Optional[str] = None,
) -> tuple[list[dict[str, Any]], int]:
    """One page of sold listings plus TCGplayer's `totalResults` for the query.

    Returns (mapped_rows, total_results). total_results is 0 on error/no data.
    """
    url = f"https://mpapi.tcgplayer.com/v2/product/{tcgplayer_id}/latestsales?mpfev=4952"

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
        "limit": 25,
        "offset": offset,
    }

    headers = dict(MPAPI_HEADERS)
    if auth_cookie:
        headers["cookie"] = f"TCGAuthTicket_Production={auth_cookie}"

    try:
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

        total_results = int(json_data.get("totalResults") or 0)

        out: list[dict[str, Any]] = []
        for sale in data:
            # Post-filter: exclude custom/photo listings. customListingId is "0"
            # (string) for standard listings, non-zero for custom.
            custom_id = sale.get("customListingId")
            if not (custom_id == "0" or custom_id == 0 or not custom_id):
                continue
            out.append(
                {
                    "tcgplayer_id": tcgplayer_id,
                    "condition": _parse_condition_from_sales_api(sale.get("condition") or condition or ""),
                    "finish": _parse_finish_from_sales_api(sale.get("variant") or ""),
                    "sold_price": (sale.get("purchasePrice") or 0) + (sale.get("shippingPrice") or 0),
                    "sold_date": sale.get("orderDate") or "",
                    "seller_name": None,
                }
            )
        return out, total_results
    except RuntimeError as err:
        if "TCGplayer sales API error" in str(err):
            raise
        return [], 0
    except Exception:
        return [], 0


def _parse_condition_from_sales_api(api_condition: str) -> str:
    return {
        "Near Mint": "NM",
        "Lightly Played": "LP",
        "Moderately Played": "MP",
        "Heavily Played": "HP",
        "Damaged": "DMG",
    }.get(api_condition, api_condition)


def _parse_finish_from_sales_api(variant: str) -> str:
    return {
        "Normal": "Regular",
        "Holofoil": "Holo",
        "Reverse Holofoil": "Reverse-Holo",
        "1st Edition Holofoil": "Holo",
        "1st Edition": "Regular",
        "Unlimited Holofoil": "Holo",
        "Unlimited": "Regular",
    }.get(variant, variant)


# ─── Set Catalog API ─────────────────────────────────────────


async def fetch_set_info(set_id: int) -> Optional[dict[str, Any]]:
    """Fetch set metadata from TCGplayer's catalog API. No auth required."""
    url = f"https://mpapi.tcgplayer.com/v2/Catalog/SetName/{set_id}?mpfev=4952"

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=MPAPI_HEADERS)

        if response.status_code != 200:
            if response.status_code == 404:
                return None
            raise RuntimeError(
                f"TCGplayer catalog API error: {response.status_code} {response.reason_phrase}"
            )

        json_data = response.json()
        results = json_data.get("results")
        result = results[0] if results else None
        if not result:
            return None

        return {
            "set_id": result.get("setNameId"),
            "name": result.get("name"),
            "clean_name": result.get("cleanSetName"),
            "url_name": result.get("urlName"),
            "abbreviation": result.get("abbreviation") or "",
            "release_date": result.get("releaseDate"),
            "is_supplemental": result.get("isSupplemental") or False,
            "active": result.get("active") if result.get("active") is not None else True,
            "description": result.get("setDescription"),
        }
    except RuntimeError as err:
        if "TCGplayer catalog API error" in str(err):
            raise
        return None
    except Exception:
        return None
