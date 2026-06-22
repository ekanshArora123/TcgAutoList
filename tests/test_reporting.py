"""Tests for ReportingService — the dashboard's read/analytics queries.

Seeds a tiny in-memory collection via the CRUD helpers (so the canonical
inventory+price join and latest_calc_date bookkeeping behave like production),
then exercises browse + analytics. No network, no on-disk DB.

Run: pytest -q
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from services.card_server.helpers.crud.cards import CardsHelper
from services.card_server.helpers.crud.inventory import InventoryHelper
from services.card_server.helpers.crud.prices import PricesHelper
from services.card_server.helpers.crud.skus import SkusHelper
from services.card_server.services.reporting_service import ReportingService

_SCHEMA = Path("services/card_server/schema.sql").read_text(encoding="utf-8")
_DATE = "2999-01-01"


@pytest.fixture
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)

    cards = CardsHelper(conn)
    skus = SkusHelper(conn)
    inv = InventoryHelper(conn)
    prices = PricesHelper(conn)

    # Charizard — valuable, high confidence, has image (id is digits).
    cards.upsert({"id": "111", "card_name": "Charizard", "set_name": "Base Set",
                  "rarity": "Rare Holo", "era": "Vintage", "card_type": "Pokemon"})
    sku1 = skus.get_or_create({"card_id": "111", "condition": "NM", "finish": "Holo"})
    inv.create({"sku_id": sku1["sku_id"], "qty": 1, "status": "unlisted"})
    prices.upsert({"sku_id": sku1["sku_id"], "calculation_date": _DATE,
                   "estimated_price": 100.0, "estimated_liquid_value": 80.0,
                   "confidence_percent": 90, "manual_check_necessary": False,
                   "manually_checked": False, "algorithm_version": "v2",
                   "estimated_low_price": 90.0, "estimated_high_price": 110.0,
                   "estimated_low_price_liquid": 70.0, "estimated_high_price_liquid": 90.0})

    # Pikachu — cheap, low confidence, flagged for manual review.
    cards.upsert({"id": "222", "card_name": "Pikachu", "set_name": "Jungle",
                  "rarity": "Common", "era": "Vintage", "card_type": "Pokemon"})
    sku2 = skus.get_or_create({"card_id": "222", "condition": "NM", "finish": "Regular"})
    inv.create({"sku_id": sku2["sku_id"], "qty": 1, "status": "listed"})
    prices.upsert({"sku_id": sku2["sku_id"], "calculation_date": _DATE,
                   "estimated_price": 2.0, "estimated_liquid_value": 1.7,
                   "confidence_percent": 50, "manual_check_necessary": True,
                   "manually_checked": False, "algorithm_version": "v2",
                   "estimated_low_price": 1.5, "estimated_high_price": 3.0,
                   "estimated_low_price_liquid": 1.0, "estimated_high_price_liquid": 2.5})

    return conn


# ─── Browse ──────────────────────────────────────────────────


def test_browse_cards_joins_price_and_paginates(db):
    res = ReportingService(db).browse_cards({})
    assert res["total"] == 2
    assert res["total_pages"] == 1
    by_name = {r["card_name"]: r for r in res["items"]}
    assert by_name["Charizard"]["estimated_price"] == 100.0
    assert by_name["Charizard"]["condition"] == "NM"


def test_browse_cards_sort_and_filter(db):
    svc = ReportingService(db)
    desc = svc.browse_cards({"sort": "estimated_price", "order": "desc"})
    assert desc["items"][0]["card_name"] == "Charizard"

    flagged = svc.browse_cards({"manual_check": "true"})
    assert flagged["total"] == 1
    assert flagged["items"][0]["card_name"] == "Pikachu"

    by_set = svc.browse_cards({"set_name": "Jungle"})
    assert by_set["total"] == 1


def test_browse_collection_image_filter(db):
    svc = ReportingService(db)
    only_with = svc.browse_collection({}, image_card_ids=["111"], has_image="true")
    assert only_with["total"] == 1
    assert only_with["items"][0]["card_name"] == "Charizard"
    assert only_with["items"][0]["has_image"] is True

    only_without = svc.browse_collection({}, image_card_ids=["111"], has_image="false")
    assert only_without["total"] == 1
    assert only_without["items"][0]["card_name"] == "Pikachu"
    assert only_without["items"][0]["has_image"] is False


def test_filter_options(db):
    opts = ReportingService(db).filter_options()
    assert set(opts["sets"]) == {"Base Set", "Jungle"}
    assert set(opts["statuses"]) == {"unlisted", "listed"}
    assert opts["eras"] == ["Vintage"]


# ─── Analytics ───────────────────────────────────────────────


def test_analytics_summary(db):
    s = ReportingService(db).analytics_summary()
    assert s["total_inventory"] == 2
    assert s["unique_cards"] == 2
    assert s["total_value"] == 102.0
    assert s["by_status"] == {"unlisted": 1, "listed": 1}
    assert s["pending_manual_checks"] == 1


def test_price_histogram_buckets_and_overflow(db):
    hist = ReportingService(db).price_histogram([0, 5, 50])
    buckets = {h["range"]: h for h in hist}
    assert buckets["$0-$5"]["count"] == 1          # Pikachu at 2.0
    assert buckets[">$50"]["count"] == 1           # Charizard at 100.0 overflows the cap
    assert buckets[">$50"]["total_value"] == 100.0


def test_price_histogram_rejects_too_few_breaks(db):
    with pytest.raises(ValueError):
        ReportingService(db).price_histogram([5])


def test_top_cards_orders_by_value(db):
    top = ReportingService(db).top_cards(10)
    assert [c["card_name"] for c in top] == ["Charizard", "Pikachu"]


def test_breakdowns(db):
    svc = ReportingService(db)
    eras = {e["era"]: e for e in svc.breakdown("era")}
    assert eras["Vintage"]["total_value"] == 102.0
    assert eras["Vintage"]["quantity"] == 2
    assert eras["Vintage"]["total_qty"] == 2  # era view adds the extra aggregates

    conds = {c["condition"]: c for c in svc.breakdown("condition")}
    assert conds["NM"]["quantity"] == 2

    sets = {s["set_name"]: s for s in svc.breakdown("set")}
    assert sets["Base Set"]["total_value"] == 100.0

    rarities = {r["rarity"]: r for r in svc.breakdown("rarity")}
    assert rarities["Rare Holo"]["total_value"] == 100.0


def test_breakdown_rejects_unknown_dimension(db):
    with pytest.raises(KeyError):
        ReportingService(db).breakdown("color")


# ─── Card detail page ────────────────────────────────────────


def test_card_detail_folds_conditions_for_a_variant(db):
    # Add a second condition (LP, qty 2) of the same Charizard Holo variant.
    skus = SkusHelper(db)
    inv = InventoryHelper(db)
    prices = PricesHelper(db)
    lp = skus.get_or_create({"card_id": "111", "condition": "LP", "finish": "Holo"})
    inv.create({"sku_id": lp["sku_id"], "qty": 2, "status": "unlisted"})
    prices.upsert({"sku_id": lp["sku_id"], "calculation_date": _DATE,
                   "estimated_price": 70.0, "confidence_percent": 85})

    detail = ReportingService(db).card_detail("111", finish="Holo")
    assert detail is not None
    assert detail["card"]["card_name"] == "Charizard"
    by_cond = {c["condition"]: c for c in detail["conditions"]}
    assert by_cond["NM"]["qty"] == 1
    assert by_cond["LP"]["qty"] == 2
    assert by_cond["LP"]["estimated_price"] == 70.0
    assert detail["total_qty"] == 3  # condition is folded together


def test_card_detail_excludes_tagged_inventory(db):
    inv = InventoryHelper(db)
    skus = SkusHelper(db)
    sku = skus.get_or_create({"card_id": "111", "condition": "NM", "finish": "Holo"})
    inv.create({"sku_id": sku["sku_id"], "qty": 5, "tags": "hidden crease", "status": "unlisted"})

    detail = ReportingService(db).card_detail("111", finish="Holo")
    by_cond = {c["condition"]: c for c in detail["conditions"]}
    assert by_cond["NM"]["qty"] == 1  # the tagged qty-5 copy is excluded


def test_card_detail_isolates_by_variant(db):
    # Charizard's only inventory is Holo; the Regular-finish page is empty.
    detail = ReportingService(db).card_detail("111", finish="Regular")
    assert detail is not None
    assert detail["conditions"] == []
    assert detail["total_qty"] == 0


def test_card_detail_has_image_flag_and_unknown_card(db):
    svc = ReportingService(db)
    detail = svc.card_detail("111", finish="Holo", image_card_ids=["111"])
    assert detail["has_image"] is True
    assert svc.card_detail("222", image_card_ids=["111"])["has_image"] is False
    assert svc.card_detail("999") is None
