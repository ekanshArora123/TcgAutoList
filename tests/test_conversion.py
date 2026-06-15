"""Pure-logic tests for the Python conversion.

Covers the deterministic pieces that the deleted TS tests exercised: pricing
algorithm, cross-condition extrapolation, liquid value, tier routing, TCGplayer
formatters, and the Telegram renderer. No network or DB required.

Run: pytest -q
"""

from __future__ import annotations

import asyncio

from services.card_server.helpers.pricing.algorithm import (
    compute_liquid_value,
    compute_price,
    extrapolate_across_conditions,
)
from services.card_server.helpers.tcgplayer.formatters import (
    format_condition_for_api,
    format_finish_for_api,
    parse_condition_from_api,
)
from dashboard.backend.orchestrator.tier1_pipeline import build_listing_template
from dashboard.backend.orchestrator.tier_router import route_to_tier
from services.telegram.renderer import render_card_summary, render_listing_confirmation


# ─── Pricing algorithm ───────────────────────────────────────


def test_lowest_listing_anchor_with_confirming_solds():
    res = compute_price(
        [{"listed_price": 10.0, "shipping_price": 1.0}, {"listed_price": 12.0, "shipping_price": 0.0}],
        [
            {"sold_price": 11.0, "sold_date": "2999-01-03"},
            {"sold_price": 10.5, "sold_date": "2999-01-02"},
            {"sold_price": 9.5, "sold_date": "2999-01-01"},
        ],
        "NM",
        "Holo",
        False,
    )
    # cheapest total = 11.0 (10 + 1 shipping; the $12 listing has $0 shipping)
    assert res.estimated_price == 11.0
    # solds confirm + volume bonus -> capped
    assert res.confidence_percent == 95
    assert res.manual_check_necessary is False


def test_cheap_card_ignores_shipping():
    res = compute_price(
        [{"listed_price": 2.0, "shipping_price": 5.0}],
        [],
        "NM",
        "Regular",
        False,
    )
    # under $5 -> listed_price only, shipping ignored
    assert res.estimated_price == 2.0


def test_no_data_is_unpriceable_and_flagged():
    res = compute_price([], [], "NM", "Regular", False)
    assert res.estimated_price is None
    assert res.manual_check_necessary is True


def test_high_value_flagged_for_review():
    res = compute_price(
        [{"listed_price": 80.0, "shipping_price": 0.0}],
        [],
        "NM",
        "Holo",
        False,
    )
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
