"""Market Data Aggregators — Transform raw listing/sold arrays into aggregate stats.

Pure functions (no side effects, no DB access). Ported from aggregators.ts.

ListingAggregates / SalesAggregates / MarketSnapshot are plain dicts.
"""

from __future__ import annotations

import math
from typing import Any, Optional

from ..pricing.config import CHEAP_CARD_THRESHOLD, DEFAULT_CONFIG, PricingConfig
from ..pricing.inputs import sale_statistics

# Type aliases — these are plain dicts at runtime.
ListingAggregates = dict
SalesAggregates = dict
MarketSnapshot = dict


# ─── Listing Aggregation ────────────────────────────────────


def aggregate_listings(listings: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute aggregate stats from active listings.

    Cards under $5 use listed_price only; cards >= $5 use listed + shipping.
    """
    if not listings:
        return {
            "listing_count": 0,
            "lowest_listing_price": None,
            "median_listing_price": None,
            "mean_listing_price": None,
            "p25_listing_price": None,
            "p75_listing_price": None,
        }

    prices = sorted(_effective_price(l) for l in listings)

    return {
        "listing_count": len(prices),
        "lowest_listing_price": _round(prices[0]),
        "median_listing_price": _round(_median(prices)),
        "mean_listing_price": _round(_mean(prices)),
        "p25_listing_price": _round(_percentile(prices, 25)),
        "p75_listing_price": _round(_percentile(prices, 75)),
    }


def _effective_price(listing: dict[str, Any]) -> float:
    return (
        listing["listed_price"]
        if listing["listed_price"] < CHEAP_CARD_THRESHOLD
        else listing["listed_price"] + listing["shipping_price"]
    )


# ─── Sales Aggregation ──────────────────────────────────────


def aggregate_sales(
    solds: list[dict[str, Any]], config: PricingConfig = DEFAULT_CONFIG
) -> dict[str, Any]:
    """Compute aggregate stats from sold listings.

    Delegates to `pricing.inputs.sale_statistics` so the stored columns are, by
    construction, the exact numbers the algorithm would compute from the same
    rows — a snapshot and a live pricing run can never disagree.

    Two of the columns exist purely for pricing: `divergence_sale_price` (the
    plain mean of the most recent few sales, which the divergence test reads)
    and `weighted_sale_price` (the age-weighted mean that gets blended in). The
    rest are analytics. Rows with no usable sale date are excluded, and the set
    is capped at `max_sales_considered`.
    """
    stats = sale_statistics(solds, config)
    return {
        "recent_sales_count": stats["sale_count"],
        "avg_sale_price": stats["avg_sale_price"],
        "median_sale_price": stats["median_sale_price"],
        "min_sale_price": stats["min_sale_price"],
        "max_sale_price": stats["max_sale_price"],
        "newest_sale_date": stats["newest_sale_date"],
        "oldest_sale_date": stats["oldest_sale_date"],
        "divergence_sale_price": stats["divergence_sale_price"],
        "weighted_sale_price": stats["weighted_sale_price"],
    }


# ─── Combined Snapshot ──────────────────────────────────────


def build_snapshot(
    fetch_result: dict[str, Any], snapshot_date: str, config: PricingConfig = DEFAULT_CONFIG
) -> dict[str, Any]:
    """Build a complete MarketSnapshot dict from a MarketFetchResult dict."""
    listings = aggregate_listings(fetch_result["activeListings"])
    sales = aggregate_sales(fetch_result["soldListings"], config)

    return {
        "card_id": fetch_result["cardId"],
        "condition": fetch_result["condition"],
        "finish": fetch_result["finish"],
        # 1st Edition and Unlimited are separate products at separate prices;
        # without this they collide on one row per day and the last write wins.
        "specialty_one": fetch_result.get("specialtyOne") or "None",
        "snapshot_date": snapshot_date,
        "source": fetch_result["source"],
        **listings,
        **sales,
    }


def build_snapshots(
    fetch_results: list[dict[str, Any]],
    snapshot_date: str,
    config: PricingConfig = DEFAULT_CONFIG,
) -> list[dict[str, Any]]:
    return [build_snapshot(r, snapshot_date, config) for r in fetch_results]


# ─── Stats Helpers ──────────────────────────────────────────


def _mean(sorted_vals: list[float]) -> float:
    return sum(sorted_vals) / len(sorted_vals)


def _median(sorted_vals: list[float]) -> float:
    mid = len(sorted_vals) // 2
    if len(sorted_vals) % 2 == 0:
        return (sorted_vals[mid - 1] + sorted_vals[mid]) / 2
    return sorted_vals[mid]


def _percentile(sorted_vals: list[float], p: float) -> float:
    """p-th percentile using nearest-rank method. Input must be sorted ascending."""
    index = math.ceil((p / 100) * len(sorted_vals)) - 1
    return sorted_vals[max(0, index)]


def _round(n: float) -> float:
    return round(n * 100) / 100
