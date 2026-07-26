"""Tests for full variant coverage in the market collector.

Regression tests for three collector bugs that together left ~93% of the
collection without a lowest-listing anchor:

  1. Listings were fetched with no condition/finish, so the TCGplayer endpoint
     defaulted to Near Mint + Normal and every other variant was recorded with
     zero listings and priced from solds alone.
  2. In-between grades (MP-LP, LP-NM, HP-MP, DM-HP) and the DM alias were never
     priced by the collector at all — their SKUs kept a years-old estimate.
  3. `set_name` was not threaded to the fetcher, so WOTC-era sets were queried
     with printing "Normal" instead of "Unlimited" and matched no listings.

DB-backed tests use an in-memory SQLite built from schema.sql; the network is
stubbed, so nothing here touches TCGplayer.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from services.card_server.helpers.market import collector as collector_module
from services.card_server.helpers.market.collector import MarketCollector
from services.card_server.helpers.market.fetchers import fetch_market_data_for_card
from services.card_server.helpers.pricing.algorithm import (
    PricingResult,
    compute_price,
    interpolate_in_between,
    relabel_alias,
)
from services.card_server.helpers.pricing.conditions import (
    expand_to_primaries,
    is_primary,
    normalize_condition,
    normalize_finish,
    primary_neighbors,
)
from services.card_server.helpers.tcgplayer.formatters import format_finish_for_api

SCHEMA = Path("services/card_server/schema.sql").read_text(encoding="utf-8")


# ─── Condition vocabulary ────────────────────────────────────


def test_in_between_conditions_expand_to_both_neighbors():
    assert expand_to_primaries("MP-LP") == ("LP", "MP")
    assert expand_to_primaries("LP-NM") == ("NM", "LP")
    assert expand_to_primaries("HP-MP") == ("MP", "HP")
    # DM-HP was missing from IN_BETWEEN_CONDITIONS entirely (68 owned rows).
    assert expand_to_primaries("DM-HP") == ("HP", "DMG")


def test_primaries_expand_to_themselves():
    for condition in ("NM", "LP", "MP", "HP", "DMG"):
        assert expand_to_primaries(condition) == (condition,)


def test_aliases_resolve_to_primaries():
    assert normalize_condition("DM") == "DMG"
    assert normalize_condition("MINT") == "NM"
    assert expand_to_primaries("DM") == ("DMG",)
    assert expand_to_primaries("MINT") == ("NM",)
    assert is_primary("DM") and is_primary("MINT")


def test_normalize_handles_stored_whitespace():
    # Both of these exist in the live DB from early imports.
    assert normalize_finish(" Holo ") == "Holo"
    assert normalize_finish("") == "Regular"
    assert normalize_condition("  mp-lp  ") == "MP-LP"


def test_unrecognized_condition_expands_to_nothing():
    assert expand_to_primaries("BOGUS") == ()
    assert primary_neighbors("NM") is None


# ─── In-between interpolation ────────────────────────────────


def _priced(price: float, confidence: float = 80, manual: bool = False) -> PricingResult:
    return PricingResult(
        estimated_price=price,
        estimated_liquid_value=price,
        estimated_low_price=price * 0.9,
        estimated_high_price=price * 1.1,
        estimated_low_price_liquid=None,
        estimated_high_price_liquid=None,
        confidence_percent=confidence,
        manual_check_necessary=manual,
        algorithm_version="test",
        reasoning="",
    )


def test_interpolates_midpoint_of_both_neighbors():
    res = interpolate_in_between("MP-LP", _priced(1499.99), "LP", _priced(799.99), "MP")
    assert res.estimated_price == 1149.99


def test_interpolation_takes_lower_confidence_and_ors_review_flags():
    res = interpolate_in_between(
        "MP-LP", _priced(10.0, confidence=85), "LP", _priced(8.0, confidence=55), "MP"
    )
    assert res.confidence_percent == 55
    # Cheap card, neither neighbor flagged -> interpolation adds no flag of its own.
    assert res.manual_check_necessary is False

    flagged = interpolate_in_between(
        "MP-LP", _priced(10.0, manual=True), "LP", _priced(8.0), "MP"
    )
    assert flagged.manual_check_necessary is True


def test_single_neighbor_extrapolates_and_always_flags_review():
    res = interpolate_in_between("MP-LP", _priced(10.0), "LP", None, "MP")
    # Extrapolation, not interpolation -> always manual review per the pricing doc.
    assert res.manual_check_necessary is True
    assert res.estimated_price is not None


def test_no_neighbors_returns_none_rather_than_fabricating():
    assert interpolate_in_between("MP-LP", None, "LP", None, "MP") is None
    # A neighbor that exists but produced no price is equally unusable.
    unpriced = _priced(1.0)
    unpriced.estimated_price = None
    assert interpolate_in_between("MP-LP", unpriced, "LP", None, "MP") is None


def test_derived_high_value_price_still_flags_review():
    # $60 midpoint is over HIGH_VALUE_THRESHOLD ($50) even though neither
    # neighbor was individually flagged.
    res = interpolate_in_between("MP-LP", _priced(70.0), "LP", _priced(50.0), "MP")
    assert res.estimated_price == 60.0
    assert res.manual_check_necessary is True


def test_alias_relabel_preserves_estimate():
    source = _priced(12.34, confidence=85)
    res = relabel_alias(source, "MINT", "NM")
    assert res.estimated_price == 12.34
    assert res.confidence_percent == 85
    assert "MINT" in res.reasoning


# ─── WOTC printing (bug 3) ───────────────────────────────────


def test_wotc_sets_use_unlimited_printing():
    assert format_finish_for_api("Regular", "None", "Jungle") == "Unlimited"
    assert format_finish_for_api("Holo", "None", "Jungle") == "Unlimited Holofoil"
    # The regression: an empty set_name silently produced "Normal".
    assert format_finish_for_api("Regular", "None", "") == "Normal"


# ─── Fetcher fan-out (bugs 1 + 3) ────────────────────────────


def _stub_fetchers(monkeypatch, calls):
    async def fake_listings(card_id, condition=None, finish=None, set_name=None,
                            specialty_one=None, offset=0):
        calls.append({"condition": condition, "finish": finish, "set_name": set_name,
                      "specialty_one": specialty_one})
        return [{"listed_price": 100.0, "shipping_price": 0.0}]

    async def fake_solds(card_id, condition=None, finish=None, max_results=25):
        return [{"condition": "NM", "finish": "Regular", "sold_price": 9.0,
                 "sold_date": "2026-07-20"}]

    import services.card_server.helpers.market.fetchers as f
    monkeypatch.setattr(f, "fetch_active_listings", fake_listings)
    monkeypatch.setattr(f, "fetch_sold_listings", fake_solds)


def test_fetch_requests_every_requested_variant(monkeypatch):
    calls: list[dict] = []
    _stub_fetchers(monkeypatch, calls)

    results = asyncio.run(
        fetch_market_data_for_card(
            "88075",
            variants=[("LP", "Reverse-Holo", "None"), ("MP", "Reverse-Holo", "None")],
            set_name="Legendary Collection",
        )
    )

    assert [(c["condition"], c["finish"]) for c in calls] == [
        ("LP", "Reverse-Holo"),
        ("MP", "Reverse-Holo"),
    ]
    # set_name must reach the formatter, or WOTC printings break (bug 3).
    assert all(c["set_name"] == "Legendary Collection" for c in calls)

    listed = {(r["condition"], r["finish"]) for r in results if r["activeListings"]}
    assert listed == {("LP", "Reverse-Holo"), ("MP", "Reverse-Holo")}


def test_sold_only_variants_are_still_reported(monkeypatch):
    calls: list[dict] = []
    _stub_fetchers(monkeypatch, calls)

    results = asyncio.run(
        fetch_market_data_for_card("88075", variants=[("MP", "Reverse-Holo", "None")])
    )
    # The NM/Regular sale has no listing fetch but must not be dropped.
    nm = next(r for r in results if (r["condition"], r["finish"]) == ("NM", "Regular"))
    assert nm["soldListings"] and nm["activeListings"] == []


# ─── Collector integration ───────────────────────────────────


@pytest.fixture()
def db() -> sqlite3.Connection:
    # isolation_level=None matches db.py — the CRUD helpers issue their own BEGIN,
    # which errors if the connection has an implicit transaction already open.
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO cards (id, card_name, set_name) VALUES ('88075', 'Pikachu', 'Legendary Collection')"
    )
    return conn


def _own(db: sqlite3.Connection, condition: str, finish: str, specialty_one: str = "None") -> int:
    cur = db.execute(
        "INSERT INTO skus (card_id, condition, finish, specialty_one, specialty_two, qty)"
        " VALUES ('88075', ?, ?, ?, 'None', 1)",
        (condition, finish, specialty_one),
    )
    sku_id = cur.lastrowid
    db.execute("INSERT INTO inventory (sku_id, qty) VALUES (?, 1)", (sku_id,))
    return sku_id


def test_plan_variants_expands_in_between_and_dedupes(db):
    _own(db, "MP-LP", "Reverse-Holo")
    _own(db, "MP", "Reverse-Holo")  # shares the MP call with MP-LP
    _own(db, "NM", " Holo ")  # stored whitespace must normalize

    variants = MarketCollector(db)._plan_variants("88075")

    assert variants == [
        ("LP", "Reverse-Holo", "None"),
        ("MP", "Reverse-Holo", "None"),
        ("NM", "Holo", "None"),
    ]


def test_collector_prices_in_between_sku_from_both_neighbors(db, monkeypatch):
    sku_id = _own(db, "MP-LP", "Reverse-Holo")

    async def fake_fetch(card_id, variants=None, set_name=None, delay_ms=0):
        prices = {"LP": 1499.99, "MP": 799.99}
        return [
            {"cardId": card_id, "condition": c, "finish": f, "specialtyOne": s,
             "source": "tcgplayer",
             "activeListings": [{"listed_price": prices[c], "shipping_price": 0.0}],
             "soldListings": []}
            for c, f, s in variants
        ]

    monkeypatch.setattr(collector_module, "fetch_market_data_for_card", fake_fetch)

    report = asyncio.run(
        MarketCollector(db).collect_cards(["88075"], {"delayMs": 0, "verbose": False})
    )
    assert report["errors"] == []

    row = db.execute(
        "SELECT estimated_price FROM prices WHERE sku_id = ?", (sku_id,)
    ).fetchone()
    # Was never priced at all before this fix.
    assert row["estimated_price"] == 1149.99


def test_collector_prices_alias_conditions(db, monkeypatch):
    mint_id = _own(db, "MINT", "Regular")
    dm_id = _own(db, "DM", "Regular")

    async def fake_fetch(card_id, variants=None, set_name=None, delay_ms=0):
        prices = {"NM": 20.0, "DMG": 3.0}
        return [
            {"cardId": card_id, "condition": c, "finish": f, "specialtyOne": s,
             "source": "tcgplayer",
             "activeListings": [{"listed_price": prices[c], "shipping_price": 0.0}],
             "soldListings": []}
            for c, f, s in variants
        ]

    monkeypatch.setattr(collector_module, "fetch_market_data_for_card", fake_fetch)
    asyncio.run(MarketCollector(db).collect_cards(["88075"], {"delayMs": 0, "verbose": False}))

    assert db.execute("SELECT estimated_price FROM prices WHERE sku_id = ?",
                      (mint_id,)).fetchone()["estimated_price"] == 20.0
    # DM is the legacy spelling of DMG and was previously never priced.
    assert db.execute("SELECT estimated_price FROM prices WHERE sku_id = ?",
                      (dm_id,)).fetchone()["estimated_price"] == 3.0


def test_in_between_sku_left_alone_when_no_neighbor_data(db, monkeypatch):
    sku_id = _own(db, "MP-LP", "Reverse-Holo")
    db.execute(
        "INSERT INTO prices (sku_id, calculation_date, estimated_price) VALUES (?, '2025-02-27', 136.58)",
        (sku_id,),
    )

    async def fake_fetch(card_id, variants=None, set_name=None, delay_ms=0):
        return [
            {"cardId": card_id, "condition": c, "finish": f, "specialtyOne": s,
             "source": "tcgplayer", "activeListings": [], "soldListings": []}
            for c, f, s in variants
        ]

    monkeypatch.setattr(collector_module, "fetch_market_data_for_card", fake_fetch)
    asyncio.run(MarketCollector(db).collect_cards(["88075"], {"delayMs": 0, "verbose": False}))

    # No fabricated estimate — the old row is the only one.
    rows = db.execute(
        "SELECT calculation_date FROM prices WHERE sku_id = ? AND estimated_price IS NOT NULL",
        (sku_id,),
    ).fetchall()
    assert [r["calculation_date"] for r in rows] == ["2025-02-27"]


def test_force_overrides_same_day_skip(db, monkeypatch):
    _own(db, "NM", "Regular")
    db.execute(
        "INSERT INTO market_snapshots (card_id, condition, finish, snapshot_date, source)"
        " VALUES ('88075', 'NM', 'Regular', date('now'), 'tcgplayer')"
    )
    seen: list[str] = []

    async def fake_fetch(card_id, variants=None, set_name=None, delay_ms=0):
        seen.append(card_id)
        return []

    monkeypatch.setattr(collector_module, "fetch_market_data_for_card", fake_fetch)
    c = MarketCollector(db)

    asyncio.run(c.collect_cards(["88075"], {"delayMs": 0, "verbose": False}))
    assert seen == []  # already collected today

    asyncio.run(c.collect_cards(["88075"], {"delayMs": 0, "verbose": False, "force": True}))
    assert seen == ["88075"]
