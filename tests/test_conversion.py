"""Pure-logic tests for the Python conversion.

Covers the deterministic pieces that the deleted TS tests exercised: pricing
algorithm, cross-condition extrapolation, liquid value, tier routing, TCGplayer
formatters, and the Telegram renderer. No network or DB required.

Run: pytest -q
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

from services.card_server.helpers.pricing.algorithm import (
    compute_liquid_value,
    compute_price,
    extrapolate_across_conditions,
    sales_weight,
)
from services.card_server.helpers.pricing.config import DEFAULT_CONFIG
from services.card_server.helpers.pricing.inputs import PricingInputs, from_raw
from services.card_server.helpers.tcgplayer.formatters import (
    format_condition_for_api,
    format_finish_for_api,
    parse_condition_from_api,
)
from dashboard.backend.orchestrator.tier1_pipeline import build_listing_template
from dashboard.backend.orchestrator.tier_router import route_to_tier
from services.telegram.renderer import render_card_summary, render_listing_confirmation


# ─── Pricing algorithm ───────────────────────────────────────


def _days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat()


def _price(listings, solds, condition="NM", finish="Holo"):
    """Price from raw rows, the way the on-demand path does."""
    return compute_price(from_raw(listings, solds, condition, finish))


def _inputs(**kwargs) -> PricingInputs:
    """Hand-built inputs, for asserting the blend maths without constructing
    sale rows that happen to produce the required statistics."""
    return PricingInputs(condition=kwargs.pop("condition", "NM"), **kwargs)


def test_lowest_listing_anchor_with_confirming_solds():
    res = _price(
        [{"listed_price": 10.0, "shipping_price": 1.0}, {"listed_price": 12.0, "shipping_price": 0.0}],
        [
            {"sold_price": 11.0, "sold_date": _days_ago(0)},
            {"sold_price": 10.5, "sold_date": _days_ago(1)},
            {"sold_price": 9.5, "sold_date": _days_ago(2)},
        ],
    )
    # cheapest total = 11.0 (10 + 1 shipping; the $12 listing has $0 shipping)
    assert res.estimated_price == 11.0
    # solds confirm + volume bonus -> capped
    assert res.confidence_percent == 95
    assert res.manual_check_necessary is False


def test_cheap_card_ignores_shipping():
    res = _price([{"listed_price": 2.0, "shipping_price": 5.0}], [], finish="Regular")
    # under $5 -> listed_price only, shipping ignored
    assert res.estimated_price == 2.0


def test_no_data_is_unpriceable_and_flagged():
    res = _price([], [], finish="Regular")
    assert res.estimated_price is None
    assert res.manual_check_necessary is True


def test_high_value_flagged_for_review():
    res = _price([{"listed_price": 80.0, "shipping_price": 0.0}], [])
    assert res.estimated_price == 80.0
    assert res.manual_check_necessary is True  # > $50


def test_liquid_value_tiers():
    assert compute_liquid_value(2.0) == 1.7  # <$5 -> $0 shipping
    assert compute_liquid_value(10.0) == 7.5  # $5-25 -> $1
    assert compute_liquid_value(40.0) == 29.0  # >$25 -> $5


def test_extrapolation_compounds_30_percent():
    assert extrapolate_across_conditions(10.0, "NM", "LP") == 7.0
    assert extrapolate_across_conditions(10.0, "NM", "MP") == 4.9
    assert extrapolate_across_conditions(10.0, "NM", "NM") == 10.0


def test_extrapolation_resolves_alias_spellings():
    # DM is the legacy spelling of DMG and MINT is priced as NM. Both used to
    # fall off the tier ladder and return None instead of a price.
    assert extrapolate_across_conditions(10.0, "NM", "DM") == extrapolate_across_conditions(
        10.0, "NM", "DMG"
    )
    assert extrapolate_across_conditions(10.0, "DM", "HP") is not None
    assert extrapolate_across_conditions(10.0, "MINT", "LP") == 7.0
    assert extrapolate_across_conditions(10.0, " nm ", "LP") == 7.0  # case/whitespace


# ─── Divergence blend ────────────────────────────────────────


def test_worked_example_from_the_spec():
    """Listing $350 against a last-5 mean of $200 is 75% divergence, which puts
    60% of the price on the sold side; with an age-weighted sold mean of $175
    that is 0.60*175 + 0.40*350 = $245."""
    res = compute_price(
        _inputs(
            lowest_listing_price=350.0,
            listing_count=4,
            divergence_sale_price=200.0,
            weighted_sale_price=175.0,
            sale_count=15,
        )
    )
    assert res.estimated_price == 245.0


def test_sales_weight_ramps_with_divergence_then_clamps():
    cfg = DEFAULT_CONFIG
    # At the threshold the sold side takes its minimum share, at the ceiling its
    # maximum, and beyond the ceiling it stays there — the listing always keeps
    # at least 1 - sales_weight_max of the price.
    assert sales_weight(0.30, True, cfg) == cfg.sales_weight_min
    assert round(sales_weight(0.75, True, cfg), 4) == 0.60
    assert sales_weight(0.90, True, cfg) == cfg.sales_weight_max
    assert sales_weight(9.00, True, cfg) == cfg.sales_weight_max
    # Below the market there is no "how far below" reading to ramp on.
    assert sales_weight(0.40, False, cfg) == cfg.sales_weight_listing_below


def test_extreme_divergence_still_leaves_the_listing_a_share():
    # $40 listing against $4 sales: sales take their 70% cap, listing keeps 30%.
    res = compute_price(
        _inputs(
            lowest_listing_price=40.0,
            divergence_sale_price=4.0,
            weighted_sale_price=4.0,
            sale_count=6,
        )
    )
    assert res.estimated_price == 14.8  # 0.7*4 + 0.3*40


def test_listing_below_sales_is_corrected_upward():
    # Previously a below-market listing was copied verbatim as the price.
    res = compute_price(
        _inputs(
            lowest_listing_price=10.0,
            divergence_sale_price=30.0,
            weighted_sale_price=30.0,
            sale_count=6,
        )
    )
    assert res.estimated_price == 24.0  # 0.7*30 + 0.3*10


def test_stale_sales_still_correct_the_price():
    """Sales older than a month used to leave the price untouched, because the
    blend was gated on a 30-day window. They now pull the price with a reduced
    weight instead of being silently ignored."""
    stale = [{"sold_price": 10.0, "sold_date": _days_ago(120 + i)} for i in range(3)]
    res = _price([{"listed_price": 20.0, "shipping_price": 0.0}], stale)
    assert res.estimated_price == 13.0  # 0.7*10 + 0.3*20, not the bare $20


def test_cheap_cards_ignore_diverging_sales_entirely():
    # Under the free-shipping threshold sold prices are shipping noise, so the
    # listing stands however far the sales disagree.
    res = compute_price(
        _inputs(
            lowest_listing_price=2.0,
            divergence_sale_price=30.0,
            weighted_sale_price=30.0,
            sale_count=8,
        )
    )
    assert res.estimated_price == 2.0


def test_price_band_brackets_the_estimate():
    res = compute_price(
        _inputs(
            lowest_listing_price=350.0,
            divergence_sale_price=200.0,
            weighted_sale_price=175.0,
            sale_count=15,
        )
    )
    assert res.estimated_low_price == 175.0
    assert res.estimated_high_price == 350.0
    assert res.estimated_low_price <= res.estimated_price <= res.estimated_high_price


# ─── Age weighting ───────────────────────────────────────────


def test_weighted_average_leans_on_the_recent_sales():
    # 3 sales at 45 days ($200) against 12 at 120 days ($120). A plain mean is
    # $136; the half-life weighting pulls it well above that, toward the recent
    # cluster, without discarding the older tail.
    recent = [{"sold_price": 200.0, "sold_date": _days_ago(45)} for _ in range(3)]
    older = [{"sold_price": 120.0, "sold_date": _days_ago(120)} for _ in range(12)]
    res = compute_price(from_raw([], recent + older, "NM", "Holo"))
    assert 160.0 < res.estimated_price < 175.0


def test_sold_window_caps_at_max_sales_considered():
    # 25 recent sales at $10, plus 50 ancient at $2. Only the 25 most recent
    # enter the average, so the ancient tail can't drag the price toward $2.
    recent = [{"sold_price": 10.0, "sold_date": _days_ago(i)} for i in range(25)]
    ancient = [{"sold_price": 2.0, "sold_date": _days_ago(300 + i)} for i in range(50)]
    res = _price([], recent + ancient)
    assert res.estimated_price == 10.0  # full-history mean would be ~4.67


def test_undated_sales_are_skipped_not_counted():
    # A row with no order date can be neither ordered nor aged, so it is dropped
    # rather than silently weighted as if it were fresh.
    solds = [
        {"sold_price": 10.0, "sold_date": _days_ago(1)},
        {"sold_price": 999.0, "sold_date": ""},
    ]
    res = compute_price(from_raw([], solds, "NM", "Holo"))
    assert res.estimated_price == 10.0


# ─── Formatters ──────────────────────────────────────────────


def test_condition_formatting():
    assert format_condition_for_api("NM") == ["Near Mint"]
    assert format_condition_for_api("LP-NM") == ["Lightly Played", "Near Mint"]
    assert format_condition_for_api("MINT") == ["Near Mint"]


def test_finish_formatting():
    assert format_finish_for_api("Holo", "First Edition", "") == "1st Edition Holofoil"
    assert format_finish_for_api("Holo", "None", "Jungle") == "Unlimited Holofoil"
    assert format_finish_for_api("Reverse-Holo", "None", "") == "Reverse Holofoil"
    assert format_finish_for_api("Regular", "None", "") == "Normal"


def test_parse_condition_roundtrip():
    assert parse_condition_from_api("Near Mint") == "NM"
    assert parse_condition_from_api("Damaged") == "DMG"


# ─── Tier router ─────────────────────────────────────────────


def test_tier1_high_confidence():
    price = {"estimated_price": 10.0, "confidence_percent": 90, "manual_check_necessary": False}
    assert route_to_tier(price, "None")["tier"] == 1


def test_tier2_medium_confidence():
    price = {"estimated_price": 10.0, "confidence_percent": 55, "manual_check_necessary": False}
    assert route_to_tier(price, "None")["tier"] == 2


def test_tier3_low_confidence_and_no_price():
    low = {"estimated_price": 10.0, "confidence_percent": 20, "manual_check_necessary": False}
    assert route_to_tier(low, "None")["tier"] == 3
    assert route_to_tier(None, "None")["tier"] == 3


def test_tier3_specialty_and_very_high_value():
    price = {"estimated_price": 10.0, "confidence_percent": 95, "manual_check_necessary": False}
    assert route_to_tier(price, "PSA 10")["tier"] == 3
    high = {"estimated_price": 250.0, "confidence_percent": 95, "manual_check_necessary": False}
    assert route_to_tier(high, "None")["tier"] == 3


# ─── Listing template + renderer ─────────────────────────────


def _detail():
    return {
        "card_name": "Charizard",
        "set_name": "Base Set",
        "card_number": "4/102",
        "condition": "NM",
        "finish": "Holo",
        "rarity": "Rare Holo",
        "tags": None,
        "status": "unlisted",
    }


def test_build_listing_template():
    lt = build_listing_template(_detail(), {"estimated_price": 100.0}, ["/f.jpg", "/b.jpg"])
    assert lt.price == 100.0
    assert lt.condition == "Near Mint"
    assert "Charizard" in lt.title
    assert len(lt.title) <= 80
    assert lt.photo_paths == ["/f.jpg", "/b.jpg"]


def test_renderer_escapes_and_formats():
    price = {
        "estimated_price": 12.5,
        "estimated_liquid_value": 9.0,
        "confidence_percent": 85,
        "estimated_low_price": 10.0,
        "estimated_high_price": 15.0,
        "manual_check_necessary": False,
    }
    summary = render_card_summary(_detail(), price)
    assert "*Charizard*" in summary
    assert "$12.50" in summary
    confirm = render_listing_confirmation(_detail(), 12.5)
    assert "Listing created" in confirm


# ─── Sold-listings pagination (network-free) ─────────────────


def test_sold_pagination_walks_to_total_results(monkeypatch):
    """The pager must drive off totalResults, not the filtered per-page count.

    Regression guard: previously a page that came back short *after* filtering
    out custom listings (e.g. 24 of 25) ended pagination after page one. Here a
    fake page source reports totalResults=60 while yielding 24/24/10 filtered
    rows; the pager must keep going across offsets and return all ~58.
    """
    from services.card_server.helpers.tcgplayer import fetch_prices

    total = 60

    async def fake_page(tcgplayer_id, condition, finish, offset, auth_cookie=None):
        if offset >= total:
            return [], total
        remaining = total - offset
        n = min(25, remaining)
        # Full pages lose one row to custom-listing filtering (24, not 25).
        rows = [{"sold_price": 1.0}] * (n - 1 if n == 25 else n)
        return rows, total

    monkeypatch.setattr(fetch_prices, "_fetch_sold_listings_page_with_total", fake_page)
    monkeypatch.setattr(fetch_prices, "_get_auth_cookie", lambda: "fake-cookie")

    res = asyncio.run(fetch_prices.fetch_sold_listings("123", max_results=100))
    assert len(res) == 58  # 24 + 24 + 10 across offsets 0/25/50 — not 24

    # max_results still caps the walk.
    capped = asyncio.run(fetch_prices.fetch_sold_listings("123", max_results=30))
    assert len(capped) == 30
