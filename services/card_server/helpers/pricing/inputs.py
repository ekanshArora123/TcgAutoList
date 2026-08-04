"""Pricing Inputs — the sufficient statistics a price is computed from.

The algorithm does not read raw listings or raw sales. It reads the handful of
scalars below, which means a price can be recomputed at any time from stored
data with no network call — that is what makes offline re-tuning and repricing
possible.

Two builders produce the same shape:

  * `from_raw`      — from freshly fetched listing/sold rows (the collector's
                      fetch, or an on-demand price).
  * `from_snapshot` — from a stored `market_snapshots` row, which is where the
                      collector persists exactly these numbers.

`sale_statistics` is shared by both: the collector calls it while aggregating a
snapshot, so the stored columns and a live computation can never drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Optional

from .config import DEFAULT_CONFIG, PricingConfig


@dataclass(frozen=True)
class PricingInputs:
    """Everything `compute_price` needs about one card variant."""

    condition: str
    finish: str = "Regular"
    specialty_one: str = "None"

    # Listing side. `listing_count` is stored and reported but does NOT affect
    # the computed price today (see PricingConfig).
    lowest_listing_price: Optional[float] = None
    listing_count: int = 0

    # Sold side. `divergence_sale_price` is the plain mean of the most recent
    # few sales (detection); `weighted_sale_price` is the age-weighted mean over
    # a deeper window (the value actually blended in).
    divergence_sale_price: Optional[float] = None
    weighted_sale_price: Optional[float] = None
    sale_count: int = 0
    min_sale_price: Optional[float] = None
    max_sale_price: Optional[float] = None
    newest_sale_date: Optional[str] = None

    @property
    def has_sales(self) -> bool:
        return self.sale_count > 0 and self.weighted_sale_price is not None


def _round(n: float) -> float:
    return round(n * 100) / 100


def _parse_sale_date(value: Any) -> Optional[date]:
    """Date part of a sold_date/order_date, which TCGplayer returns as an ISO
    timestamp. Returns None for missing or unparseable values so a malformed row
    is skipped rather than aborting the whole card."""
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _effective_listing_price(listing: dict[str, Any], config: PricingConfig) -> float:
    """A listing's true cost to a buyer.

    Under the free-shipping threshold buyers bundle orders, so shipping is not
    really paid and the listed price stands alone; at or above it, shipping is
    part of what the card costs. Mirrors `market/aggregators._effective_price`.
    """
    listed = listing.get("listed_price") or 0
    if listed < config.cheap_card_threshold:
        return listed
    return listed + (listing.get("shipping_price") or 0)


def lowest_listing_price(
    listings: list[dict[str, Any]], config: PricingConfig = DEFAULT_CONFIG
) -> Optional[float]:
    """Cheapest effective listing price, or None when there are no listings."""
    prices = [_effective_listing_price(l, config) for l in listings]
    return _round(min(prices)) if prices else None


def sale_statistics(
    solds: list[dict[str, Any]],
    config: PricingConfig = DEFAULT_CONFIG,
    today: Optional[date] = None,
    *,
    price_key: str = "sold_price",
    date_key: str = "sold_date",
) -> dict[str, Any]:
    """Reduce raw sold rows to the four numbers the algorithm and the snapshot
    both store.

    Sales with no usable date are dropped: every downstream use is ordered or
    age-weighted, and an undatable sale can participate in neither.

    The weighted mean uses `0.5 ** (age / half_life)` over the most recent
    `max_sales_considered` sales. Future-dated rows (clock skew) are clamped to
    age 0 rather than being given a weight above 1.
    """
    today = today or datetime.now().date()

    dated: list[tuple[date, float]] = []
    for sale in solds:
        sold_on = _parse_sale_date(sale.get(date_key))
        if sold_on is None:
            continue
        dated.append((sold_on, float(sale.get(price_key) or 0)))

    if not dated:
        return dict(EMPTY_SALE_STATISTICS)

    dated.sort(key=lambda row: row[0], reverse=True)
    considered = dated[: config.max_sales_considered]

    recent = considered[: config.divergence_sale_count]
    divergence = _round(sum(p for _, p in recent) / len(recent))

    weight_sum = 0.0
    weighted_sum = 0.0
    for sold_on, price in considered:
        age = max((today - sold_on).days, 0)
        weight = 0.5 ** (age / config.sold_weight_half_life_days)
        weight_sum += weight
        weighted_sum += weight * price

    prices = sorted(p for _, p in considered)
    mid = len(prices) // 2
    median = (prices[mid - 1] + prices[mid]) / 2 if len(prices) % 2 == 0 else prices[mid]

    return {
        "divergence_sale_price": divergence,
        "weighted_sale_price": _round(weighted_sum / weight_sum) if weight_sum > 0 else None,
        "sale_count": len(considered),
        "avg_sale_price": _round(sum(prices) / len(prices)),
        "median_sale_price": _round(median),
        "min_sale_price": _round(prices[0]),
        "max_sale_price": _round(prices[-1]),
        "newest_sale_date": considered[0][0].isoformat(),
        "oldest_sale_date": considered[-1][0].isoformat(),
    }


# The shape `sale_statistics` returns when a variant has no usable sales.
EMPTY_SALE_STATISTICS: dict[str, Any] = {
    "divergence_sale_price": None,
    "weighted_sale_price": None,
    "sale_count": 0,
    "avg_sale_price": None,
    "median_sale_price": None,
    "min_sale_price": None,
    "max_sale_price": None,
    "newest_sale_date": None,
    "oldest_sale_date": None,
}

# The subset of `sale_statistics` that PricingInputs carries.
_INPUT_SALE_FIELDS = (
    "divergence_sale_price",
    "weighted_sale_price",
    "sale_count",
    "min_sale_price",
    "max_sale_price",
    "newest_sale_date",
)


def from_raw(
    active_listings: list[dict[str, Any]],
    sold_listings: list[dict[str, Any]],
    condition: str,
    finish: str = "Regular",
    specialty_one: str = "None",
    config: PricingConfig = DEFAULT_CONFIG,
    today: Optional[date] = None,
) -> PricingInputs:
    """Build inputs straight from fetched rows, without going via the DB."""
    stats = sale_statistics(sold_listings, config, today)
    return PricingInputs(
        condition=condition,
        finish=finish,
        specialty_one=specialty_one,
        lowest_listing_price=lowest_listing_price(active_listings, config),
        listing_count=len(active_listings),
        **{k: stats[k] for k in _INPUT_SALE_FIELDS},
    )


def from_snapshot(row: dict[str, Any]) -> PricingInputs:
    """Build inputs from a stored `market_snapshots` row.

    Rows written before the weighted columns existed carry NULLs there; such a
    variant reads as having no usable sold signal (`has_sales` False) and prices
    off its listings alone until the next collection refreshes it.
    """
    return PricingInputs(
        condition=row["condition"],
        finish=row.get("finish") or "Regular",
        specialty_one=row.get("specialty_one") or "None",
        lowest_listing_price=row.get("lowest_listing_price"),
        listing_count=row.get("listing_count") or 0,
        divergence_sale_price=row.get("divergence_sale_price"),
        weighted_sale_price=row.get("weighted_sale_price"),
        sale_count=row.get("recent_sales_count") or 0,
        min_sale_price=row.get("min_sale_price"),
        max_sale_price=row.get("max_sale_price"),
        newest_sale_date=row.get("newest_sale_date"),
    )
