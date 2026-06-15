"""Live contract tests for the TCGplayer API — the 'externals' suite.

Pricing is built on TCGplayer's *undocumented* internal APIs. Those can change
shape at any time with no change on our side, silently breaking pricing in
production. These tests hit the real APIs against a known-stable product and
assert the responses still carry the fields our fetch/pricing code reads.

They are MARKED `external` and EXCLUDED from the default run (see pytest.ini:
`addopts = -m "not external"`), so per-commit CI and local `pytest -q` never
touch the network. Azure runs them on a schedule via the ExternalChecks stage:

    pytest -m external

A failure here means TCGplayer changed something (or the probe card was
delisted) — investigate before deploying or trusting prices, even if no
application code changed.

Tuning knobs (env vars):
  TCGPLAYER_TEST_PRODUCT_ID  probe card product id (default: Base Set Charizard)
  TCGPLAYER_AUTH_COOKIE      optional; also exercises authed sold pagination

The raw probes intentionally reuse the production headers from the fetch modules
so that if TCGplayer starts blocking those headers, this canary catches it too.
"""

from __future__ import annotations

import asyncio
import os

import httpx
import pytest

from services.card_server.helpers.tcgplayer.fetch_card_info import (
    SEARCH_API_HEADERS,
    fetch_card_info,
)
from services.card_server.helpers.tcgplayer.fetch_prices import (
    MPAPI_HEADERS,
    TCGPLAYER_HEADERS,
)

# Every test in this module is a live external check.
pytestmark = pytest.mark.external

# Base Set Charizard (Holofoil) — an evergreen, always-listed card. If it ever
# gets delisted, point this at any other liquid product via the env var rather
# than editing code.
PRODUCT_ID = os.environ.get("TCGPLAYER_TEST_PRODUCT_ID", "42445")

_TIMEOUT = httpx.Timeout(20.0)


def _post(url: str, headers: dict, payload: dict) -> dict:
    """Synchronous POST returning parsed JSON, with a clear 200 assertion."""
    with httpx.Client(timeout=_TIMEOUT) as client:
        resp = client.post(url, headers=headers, json=payload)
    assert resp.status_code == 200, (
        f"TCGplayer returned {resp.status_code} for {url}\n"
        f"(API may be down, blocking our headers, or the endpoint moved)"
    )
    return resp.json()


# ─── Search / Card Info API ──────────────────────────────────


def test_search_api_envelope_and_fields():
    """The search API still returns the product fields fetch_card_info reads."""
    url = "https://mp-search-api.tcgplayer.com/v1/search/request?q=&isList=false&mpfev=2163"
    payload = {
        "algorithm": "sales_synonym_v2",
        "from": 0,
        "size": 1,
        "filters": {"term": {"productLineName": ["pokemon"], "productId": [int(PRODUCT_ID)]}},
        "listingSearch": {
            "filters": {"term": {}, "range": {"quantity": {"gte": 1}}, "exclude": {"channelExclusion": 0}},
            "context": {"cart": {}},
        },
    }
    data = _post(url, SEARCH_API_HEADERS, payload)

    results = data.get("results")
    assert results and results[0].get("results"), (
        f"No product found for id {PRODUCT_ID}. If TCGplayer delisted it, set "
        f"TCGPLAYER_TEST_PRODUCT_ID to a current liquid product."
    )

    product = results[0]["results"][0]
    # Fields consumed by fetch_card_info() — a rename here breaks metadata/pricing.
    for field in ("productName", "setName", "rarityName", "totalListings"):
        assert field in product, f"search API missing expected field '{field}'"
    assert product["productName"], "productName is empty"
    # At least one price field must be present for a liquid card.
    assert product.get("marketPrice") is not None or product.get("lowestPrice") is not None, (
        "search API returned neither marketPrice nor lowestPrice"
    )
    # Aggregations drive available-conditions parsing.
    assert "aggregations" in results[0], "search API missing 'aggregations'"


def test_fetch_card_info_end_to_end():
    """Through our own mapper: catches silent drift that coerces fields to None."""
    result = asyncio.run(fetch_card_info(PRODUCT_ID))
    assert result is not None, f"fetch_card_info returned None for {PRODUCT_ID}"
    assert result["metadata"]["card_name"], "mapped card_name is empty — productName drifted?"
    price_info = result["price_info"]
    assert price_info["lowest_price"] is not None or price_info["market_price"] is not None, (
        "mapped price_info has no price — marketPrice/lowestPrice drifted?"
    )


# ─── Active Listings API ─────────────────────────────────────


def test_listings_api_envelope_and_fields():
    """The listings API still carries price/shipping/seller fields the algo reads.

    Broad query (no condition/printing filter) so a popular card always returns
    rows regardless of finish-mapping details.
    """
    url = f"https://mp-search-api.tcgplayer.com/v1/product/{PRODUCT_ID}/listings?mpfev=2163"
    payload = {
        "filters": {
            "term": {"sellerStatus": "Live", "channelId": 0, "language": ["English"], "listingType": "standard"},
            "range": {"quantity": {"gte": 1}},
            "exclude": {"channelExclusion": 0},
        },
        "from": 0,
        "size": 10,
        "sort": {"field": "price+shipping", "order": "asc"},
        "context": {"shippingCountry": "US", "cart": {}},
        "aggregations": ["listingType"],
    }
    data = _post(url, TCGPLAYER_HEADERS, payload)

    results = data.get("results")
    assert results and results[0].get("results"), (
        f"No active listings for id {PRODUCT_ID}. Unexpected for a liquid card — "
        f"check the listings API or the probe card."
    )

    row = results[0]["results"][0]
    # Fields consumed by _filter_and_map_listings() + the pricing algorithm.
    for field in ("price", "shippingPrice", "sellerRating", "sellerSales"):
        assert field in row, f"listings API missing expected field '{field}'"
    assert isinstance(row["price"], (int, float)), "listing 'price' is not numeric"


# ─── Sold Listings API ───────────────────────────────────────


def test_sold_api_envelope_and_fields():
    """The sales API still returns the per-sale fields fetch_sold_listings reads.

    Without an auth cookie TCGplayer returns up to ~5 recent sales; with one set
    in the environment, pagination returns more. Either way the shape must hold.
    """
    url = f"https://mpapi.tcgplayer.com/v2/product/{PRODUCT_ID}/latestsales?mpfev=4952"
    payload = {
        "variants": [],
        "listingType": "standard",
        "conditions": [],
        "languages": [1],
        "limit": 25,
        "offset": 0,
    }
    headers = dict(MPAPI_HEADERS)
    cookie = os.environ.get("TCGPLAYER_AUTH_COOKIE")
    if cookie:
        headers["cookie"] = f"TCGAuthTicket_Production={cookie}"

    data = _post(url, headers, payload)

    sales = data.get("data")
    assert isinstance(sales, list), "sales API 'data' is not a list"
    if sales:
        sale = sales[0]
        # Fields consumed by _fetch_sold_listings_page().
        for field in ("condition", "variant", "purchasePrice", "shippingPrice", "orderDate", "customListingId"):
            assert field in sale, f"sales API missing expected field '{field}'"
        assert isinstance(sale["purchasePrice"], (int, float)), "'purchasePrice' is not numeric"
