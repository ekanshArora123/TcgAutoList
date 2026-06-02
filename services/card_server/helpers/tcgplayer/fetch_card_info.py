"""fetch_card_info — Fetches card metadata from TCGplayer by product ID.

Uses TCGplayer's internal search API. Returns a dict with two keys:
  metadata:  card identity info (name, set, rarity, attacks, etc.)
  price_info: market/pricing snapshot (market price, lowest price, listing counts)

Ported from fetchCardInfo.ts.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Optional

import httpx

SEARCH_API_HEADERS: dict[str, str] = {
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


async def fetch_card_info(tcgplayer_id: str) -> Optional[dict[str, Any]]:
    """Fetch card data from TCGplayer for a given product ID.

    Returns {"metadata": {...}, "price_info": {...}} or None if not found.
    """
    url = "https://mp-search-api.tcgplayer.com/v1/search/request?q=&isList=false&mpfev=2163"

    payload = {
        "algorithm": "sales_synonym_v2",
        "from": 0,
        "size": 1,
        "filters": {
            "term": {
                "productLineName": ["pokemon"],
                "productId": [int(tcgplayer_id)],
            }
        },
        "listingSearch": {
            "filters": {
                "term": {},
                "range": {"quantity": {"gte": 1}},
                "exclude": {"channelExclusion": 0},
            },
            "context": {"cart": {}},
        },
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=SEARCH_API_HEADERS, json=payload)

        if response.status_code != 200:
            if response.status_code == 404:
                return None
            raise RuntimeError(
                f"TCGplayer search API error: {response.status_code} {response.reason_phrase}"
            )

        json_data = response.json()
        results = json_data.get("results")
        search_result = results[0] if results else None
        if not search_result or not search_result.get("results"):
            return None

        product = search_result["results"][0]
        attrs = product.get("customAttributes") or {}
        aggs = search_result.get("aggregations") or {}

        attacks: list[str] = []
        for key in ("attack1", "attack2", "attack3", "attack4"):
            if attrs.get(key):
                attacks.append(_strip_html(attrs[key]))

        metadata = {
            "tcgplayer_id": tcgplayer_id,
            "card_name": product.get("productName") or "",
            "set_name": product.get("setName"),
            "product_line": product.get("productLineName") or "Pokemon",
            "card_type": (attrs.get("cardType") or [None])[0] if attrs.get("cardType") else None,
            "rarity": product.get("rarityName"),
            "card_number": attrs.get("number"),
            "hp": attrs.get("hp"),
            "stage": attrs.get("stage"),
            "description": _strip_html(attrs["description"]) if attrs.get("description") else None,
            "attacks": attacks,
            "weakness": attrs.get("weakness"),
            "resistance": attrs.get("resistance"),
            "retreat_cost": attrs.get("retreatCost"),
            "flavor_text": _strip_html(attrs["flavorText"]) if attrs.get("flavorText") else None,
            "energy_type": attrs.get("energyType") or [],
            "release_date": attrs.get("releaseDate"),
            "foil_only": product.get("foilOnly") or False,
        }

        price_info = {
            "tcgplayer_id": tcgplayer_id,
            "market_price": product.get("marketPrice"),
            "lowest_price": product.get("lowestPrice"),
            "lowest_price_with_shipping": product.get("lowestPriceWithShipping"),
            "total_listings": product.get("totalListings"),
            "available_conditions": _build_available_conditions(aggs),
        }

        return {"metadata": metadata, "price_info": price_info}
    except RuntimeError as err:
        if "TCGplayer search API error" in str(err):
            raise
        return None
    except Exception:
        return None


async def fetch_card_metadata(tcgplayer_id: str) -> Optional[dict[str, Any]]:
    result = await fetch_card_info(tcgplayer_id)
    return result["metadata"] if result else None


async def fetch_card_price_info(tcgplayer_id: str) -> Optional[dict[str, Any]]:
    result = await fetch_card_info(tcgplayer_id)
    return result["price_info"] if result else None


async def fetch_card_info_batch(
    tcgplayer_ids: list[str], delay_ms: int = 100
) -> list[Optional[dict[str, Any]]]:
    """Fetch card data for multiple product IDs sequentially with rate limiting."""
    results: list[Optional[dict[str, Any]]] = []
    for i, cid in enumerate(tcgplayer_ids):
        if i > 0 and delay_ms > 0:
            await asyncio.sleep(delay_ms / 1000)
        results.append(await fetch_card_info(cid))
    return results


# ─── Helpers ──────────────────────────────────────────────────


def _build_available_conditions(aggs: dict[str, list[dict]]) -> list[dict[str, Any]]:
    conditions = aggs.get("condition") or []
    printings = aggs.get("printing") or []

    if not conditions or not printings:
        return []

    results: list[dict[str, Any]] = []
    for cond in conditions:
        for print_ in printings:
            results.append(
                {
                    "condition": _parse_condition_from_search(cond["value"]),
                    "finish": _parse_finish_from_search(print_["value"]),
                    "listing_count": cond["count"],
                }
            )
    return results


def _parse_condition_from_search(api_condition: str) -> str:
    return {
        "Near Mint": "NM",
        "Lightly Played": "LP",
        "Moderately Played": "MP",
        "Heavily Played": "HP",
        "Damaged": "DMG",
    }.get(api_condition, api_condition)


def _parse_finish_from_search(printing: str) -> str:
    return {
        "Normal": "Regular",
        "Holofoil": "Holo",
        "Reverse Holofoil": "Reverse-Holo",
        "1st Edition Holofoil": "Holo",
        "1st Edition": "Regular",
        "Unlimited Holofoil": "Holo",
        "Unlimited": "Regular",
    }.get(printing, printing)


def _strip_html(html: str) -> str:
    text = re.sub(r"<[^>]*>", "", html)
    text = text.replace("\r\n", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()
