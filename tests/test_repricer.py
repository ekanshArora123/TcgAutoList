"""Tests for pricing from stored inputs rather than from a live fetch.

The collector no longer prices from the rows it just fetched — it writes them to
`market_snapshots` and asks the `Repricer` to price from there. That split is
what makes `collect --reprice-only` possible, and what these tests pin down:

  * the two pricing-input columns are actually persisted,
  * a price can be produced from the DB alone, with no network,
  * a derived SKU (in-between grade, alias, error variant) is resolved from its
    neighbours' snapshots even when priced one SKU at a time,
  * changing a `PricingConfig` changes the price without refetching anything,
  * a card with nothing stored keeps its previous estimate.

In-memory SQLite built from schema.sql; nothing here touches TCGplayer.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest

from services.card_server.db import _upgrade_market_snapshots
from services.card_server.helpers.market.aggregators import build_snapshot
from services.card_server.helpers.market.snapshots import SnapshotStore
from services.card_server.helpers.pricing.config import DEFAULT_CONFIG
from services.card_server.helpers.pricing.repricer import Repricer

SCHEMA = Path("services/card_server/schema.sql").read_text(encoding="utf-8")
TODAY = date.today().isoformat()


@pytest.fixture()
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # schema.sql leaves the variant-uniqueness index to db.py, and the snapshot
    # upsert's ON CONFLICT needs it — run the real migration.
    _upgrade_market_snapshots(conn)
    conn.execute(
        "INSERT INTO cards (id, card_name, set_name) VALUES ('88075', 'Pikachu', 'Base Set')"
    )
    return conn


def _days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat()


def _store_snapshot(
    db: sqlite3.Connection,
    condition: str,
    *,
    finish: str = "Regular",
    specialty_one: str = "None",
    listings: tuple[float, ...] = (),
    solds: tuple[tuple[float, int], ...] = (),
    snapshot_date: str = TODAY,
) -> None:
    """Aggregate and store one variant, exactly as a collect run would."""
    SnapshotStore(db).upsert(
        build_snapshot(
            {
                "cardId": "88075",
                "condition": condition,
                "finish": finish,
                "specialtyOne": specialty_one,
                "source": "tcgplayer",
                "activeListings": [
                    {"listed_price": p, "shipping_price": 0.0} for p in listings
                ],
                "soldListings": [
                    {"sold_price": p, "sold_date": _days_ago(age)} for p, age in solds
                ],
            },
            snapshot_date,
        )
    )


def _own(db: sqlite3.Connection, condition: str, finish: str = "Regular", **kw) -> int:
    cur = db.execute(
        "INSERT INTO skus (card_id, condition, finish, specialty_one, specialty_two, qty)"
        " VALUES ('88075', ?, ?, ?, ?, 1)",
        (condition, finish, kw.get("specialty_one", "None"), kw.get("specialty_two", "None")),
    )
    sku_id = cur.lastrowid
    db.execute("INSERT INTO inventory (sku_id, qty) VALUES (?, 1)", (sku_id,))
    return sku_id


def _price_of(db: sqlite3.Connection, sku_id: int):
    row = db.execute(
        "SELECT estimated_price FROM prices WHERE sku_id = ?", (sku_id,)
    ).fetchone()
    return row["estimated_price"] if row else None


# ─── Stored inputs ───────────────────────────────────────────


def test_snapshot_persists_the_pricing_input_columns(db):
    """Without these two columns a stored snapshot can't reproduce a price, and
    the whole reprice-from-DB path silently degrades to listings-only."""
    _store_snapshot(db, "NM", listings=(20.0,), solds=((10.0, 1), (10.0, 2), (10.0, 3)))

    row = db.execute("SELECT * FROM market_snapshots").fetchone()
    assert row["divergence_sale_price"] == 10.0
    assert row["weighted_sale_price"] == pytest.approx(10.0, abs=0.05)
    assert row["lowest_listing_price"] == 20.0
    assert row["listing_count"] == 1


def test_weighted_column_leans_on_recent_sales(db):
    # Recent $200 sales against an older $120 tail: the stored weighted mean must
    # sit above the plain mean ($136), which is also stored, un-weighted.
    solds = tuple([(200.0, 45)] * 3 + [(120.0, 120)] * 12)
    _store_snapshot(db, "NM", listings=(400.0,), solds=solds)

    row = db.execute("SELECT * FROM market_snapshots").fetchone()
    assert row["avg_sale_price"] == pytest.approx(136.0, abs=0.5)
    assert 160.0 < row["weighted_sale_price"] < 175.0


# ─── Pricing from the store ──────────────────────────────────


def test_price_card_works_from_stored_data_alone(db):
    sku_id = _own(db, "NM")
    _store_snapshot(db, "NM", listings=(11.0, 14.0), solds=((11.0, 0), (10.5, 1), (9.5, 2)))

    written = Repricer(db).price_card("88075")

    assert written == 1
    assert _price_of(db, sku_id) == 11.0


def test_price_card_blends_a_diverging_listing(db):
    sku_id = _own(db, "NM")
    # $20 listing against a $10 sold signal: 100% divergence clamps the sold side
    # to its 70% ceiling -> 0.7*10 + 0.3*20.
    _store_snapshot(db, "NM", listings=(20.0,), solds=((10.0, 0), (10.0, 1), (10.0, 2)))

    Repricer(db).price_card("88075")

    assert _price_of(db, sku_id) == 13.0


def test_price_card_derives_in_between_from_neighbour_snapshots(db):
    sku_id = _own(db, "MP-LP", "Reverse-Holo")
    _store_snapshot(db, "LP", finish="Reverse-Holo", listings=(1499.99,))
    _store_snapshot(db, "MP", finish="Reverse-Holo", listings=(799.99,))

    Repricer(db).price_card("88075")

    assert _price_of(db, sku_id) == 1149.99


def test_price_sku_loads_only_the_variants_it_needs(db):
    """Pricing one in-between SKU still has to resolve both its neighbours —
    there is no MP-LP snapshot to read, because TCGplayer sells no such grade."""
    sku_id = _own(db, "MP-LP", "Reverse-Holo")
    _store_snapshot(db, "LP", finish="Reverse-Holo", listings=(1499.99,))
    _store_snapshot(db, "MP", finish="Reverse-Holo", listings=(799.99,))
    _store_snapshot(db, "NM", finish="Reverse-Holo", listings=(9999.0,))  # irrelevant

    stored = Repricer(db).price_sku(sku_id)

    assert stored is not None
    assert _price_of(db, sku_id) == 1149.99


def test_price_sku_on_a_plain_primary_uses_its_own_snapshot(db):
    sku_id = _own(db, "NM")
    _store_snapshot(db, "NM", listings=(42.0,))

    Repricer(db).price_sku(sku_id)

    assert _price_of(db, sku_id) == 42.0


def test_price_sku_flags_an_error_variant_off_the_base_price(db):
    sku_id = _own(db, "NM", specialty_two="Miscut")
    _store_snapshot(db, "NM", listings=(20.0,))

    Repricer(db).price_sku(sku_id)

    row = db.execute("SELECT * FROM prices WHERE sku_id = ?", (sku_id,)).fetchone()
    assert row["estimated_price"] == 20.0  # inherits the plain variant's price
    assert row["manual_check_necessary"] == 1
    assert "Error variant" in row["reasoning"]


def test_derived_alias_is_relabelled_not_recomputed(db):
    dmg_id = _own(db, "DM")  # legacy spelling of DMG
    _store_snapshot(db, "DMG", listings=(3.0,))

    Repricer(db).price_card("88075")

    assert _price_of(db, dmg_id) == 3.0
    reasoning = db.execute(
        "SELECT reasoning FROM prices WHERE sku_id = ?", (dmg_id,)
    ).fetchone()["reasoning"]
    assert "no distinct TCGplayer tier" in reasoning


# ─── Configurability ─────────────────────────────────────────


def test_a_different_config_reprices_without_refetching(db):
    """The point of storing inputs: change a constant, regenerate the price."""
    sku_id = _own(db, "NM")
    _store_snapshot(db, "NM", listings=(20.0,), solds=((10.0, 0), (10.0, 1), (10.0, 2)))

    Repricer(db).price_card("88075")
    assert _price_of(db, sku_id) == 13.0

    # A threshold high enough that nothing ever diverges leaves the anchor alone.
    never_diverges = replace(DEFAULT_CONFIG, price_divergence_threshold=99.0)
    Repricer(db, never_diverges).price_card("88075")
    assert _price_of(db, sku_id) == 20.0


def test_half_life_is_baked_into_the_stored_column(db):
    """The half-life is applied when a snapshot is AGGREGATED, not when it is
    repriced — a stored weighted mean is frozen under the half-life that
    produced it. Re-tuning it therefore means re-aggregating (from a collect run
    or from raw `sales`), unlike every other constant, which reprices for free.
    """
    solds = [
        {"sold_price": 200.0, "sold_date": _days_ago(45)} for _ in range(3)
    ] + [{"sold_price": 120.0, "sold_date": _days_ago(120)} for _ in range(12)]
    fetch_result = {
        "cardId": "88075", "condition": "NM", "finish": "Regular",
        "specialtyOne": "None", "source": "tcgplayer",
        "activeListings": [], "soldListings": solds,
    }

    default = build_snapshot(fetch_result, TODAY)
    shorter = build_snapshot(
        fetch_result, TODAY, replace(DEFAULT_CONFIG, sold_weight_half_life_days=15.0)
    )

    # A shorter half-life discounts the older $120 tail harder, pulling the mean
    # further toward the recent $200 cluster.
    assert shorter["weighted_sale_price"] > default["weighted_sale_price"]
    assert default["avg_sale_price"] == shorter["avg_sale_price"]  # unweighted, unaffected


# ─── Missing data ────────────────────────────────────────────


def test_card_with_no_snapshot_keeps_its_previous_price(db):
    sku_id = _own(db, "NM")
    db.execute(
        "INSERT INTO prices (sku_id, calculation_date, estimated_price) VALUES (?, '2025-02-27', 136.58)",
        (sku_id,),
    )

    assert Repricer(db).price_card("88075") == 0
    assert _price_of(db, sku_id) == 136.58


def test_in_between_with_one_missing_neighbour_extrapolates_and_flags(db):
    sku_id = _own(db, "MP-LP", "Reverse-Holo")
    _store_snapshot(db, "LP", finish="Reverse-Holo", listings=(100.0,))  # no MP snapshot

    Repricer(db).price_card("88075")

    row = db.execute("SELECT * FROM prices WHERE sku_id = ?", (sku_id,)).fetchone()
    assert row["estimated_price"] is not None
    assert row["manual_check_necessary"] == 1
    assert "Extrapolated" in row["reasoning"]


def test_pre_migration_snapshot_is_skipped_not_degraded(db):
    """A row from before the pricing-input columns knows sales existed but not
    what they were. Pricing from it would drop the sold signal — silently for a
    variant with listings, catastrophically for one without, which would go to a
    NULL price. Both must leave the previous estimate alone instead."""
    sold_only = _own(db, "NM")
    with_listing = _own(db, "LP")
    for sku_id, price in ((sold_only, 8.0), (with_listing, 20.0)):
        db.execute(
            "INSERT INTO prices (sku_id, calculation_date, estimated_price)"
            " VALUES (?, '2026-07-01', ?)",
            (sku_id, price),
        )
    # Legacy shape: sales were counted, but neither weighted column was written.
    for condition, lowest in (("NM", None), ("LP", 25.0)):
        db.execute(
            "INSERT INTO market_snapshots (card_id, condition, finish, specialty_one,"
            " snapshot_date, source, listing_count, lowest_listing_price,"
            " recent_sales_count, avg_sale_price)"
            " VALUES ('88075', ?, 'Regular', 'None', ?, 'tcgplayer', ?, ?, 9, 10.0)",
            (condition, TODAY, 0 if lowest is None else 1, lowest),
        )

    assert Repricer(db).price_card("88075") == 0
    assert _price_of(db, sold_only) == 8.0
    assert _price_of(db, with_listing) == 20.0


def test_variant_with_genuinely_no_sales_still_prices(db):
    # A NULL weighted column is only suspicious when sales were counted; with
    # zero sales it just means the variant has none, and listings price it.
    sku_id = _own(db, "NM")
    _store_snapshot(db, "NM", listings=(30.0,), solds=())

    Repricer(db).price_card("88075")

    assert _price_of(db, sku_id) == 30.0


def test_derived_skus_never_cross_printings(db):
    """A plain SKU must not inherit the 1st Edition price — separate products."""
    plain_id = _own(db, "MP-LP", "Regular", specialty_one="None")
    _store_snapshot(db, "LP", specialty_one="First Edition", listings=(500.0,))
    _store_snapshot(db, "MP", specialty_one="First Edition", listings=(300.0,))

    Repricer(db).price_card("88075")

    assert _price_of(db, plain_id) is None
