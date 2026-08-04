"""Tests for backfilling snapshot sale statistics from the raw `sales` table.

Snapshots collected before the pricing-input columns existed carry NULL there,
and the repricer skips such rows rather than price them without a sold signal.
The backfill recomputes those statistics from sales `collect_sales` already
stored, so a migrated DB can reprice without a full re-collection.

In-memory SQLite from schema.sql; no network.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest

from services.card_server.backfill_sales_stats import backfill, iter_variant_sales
from services.card_server.db import _upgrade_market_snapshots
from services.card_server.helpers.pricing.config import DEFAULT_CONFIG
from services.card_server.helpers.pricing.repricer import Repricer

SCHEMA = Path("services/card_server/schema.sql").read_text(encoding="utf-8")
TODAY = date.today().isoformat()


@pytest.fixture()
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _upgrade_market_snapshots(conn)
    conn.execute("INSERT INTO cards (id, card_name, set_name) VALUES ('88075','Pikachu','Base Set')")
    return conn


def _days_ago(n: int) -> str:
    """An ISO timestamp, the shape TCGplayer's orderDate actually arrives in."""
    return (date.today() - timedelta(days=n)).isoformat() + "T04:05:08.303+00:00"


def _sale(db, condition="NM", *, purchase, shipping=0.0, age=1, has_image=0, finish="Regular"):
    db.execute(
        "INSERT INTO sales (card_id, condition, finish, specialty_one, source, order_date,"
        " purchase_price, shipping_price, quantity, has_image)"
        " VALUES ('88075', ?, ?, 'None', 'tcgplayer', ?, ?, ?, 1, ?)",
        (condition, finish, _days_ago(age), purchase, shipping, has_image),
    )


def _legacy_snapshot(db, condition="NM", *, snapshot_date=TODAY, lowest=None, count=0, finish="Regular"):
    """A snapshot as written before the pricing-input columns existed."""
    db.execute(
        "INSERT INTO market_snapshots (card_id, condition, finish, specialty_one, snapshot_date,"
        " source, listing_count, lowest_listing_price, recent_sales_count)"
        " VALUES ('88075', ?, ?, 'None', ?, 'tcgplayer', ?, ?, ?)",
        (condition, finish, snapshot_date, 0 if lowest is None else 3, lowest, count),
    )


def _snapshot(db, condition="NM", snapshot_date=TODAY):
    return db.execute(
        "SELECT * FROM market_snapshots WHERE condition = ? AND snapshot_date = ?",
        (condition, snapshot_date),
    ).fetchone()


# ─── Reading the raw table ───────────────────────────────────


def test_sale_price_sums_purchase_and_shipping(db):
    # The pricing fetch stores one summed sold_price, so a statistic derived
    # here has to be built the same way or it isn't comparable.
    _sale(db, purchase=17.0, shipping=0.99)

    [(_, sales)] = list(iter_variant_sales(db, DEFAULT_CONFIG))

    assert sales[0]["sold_price"] == pytest.approx(17.99)


def test_photo_listings_excluded_by_default(db):
    _sale(db, purchase=10.0, age=1)
    _sale(db, purchase=999.0, age=0, has_image=1)

    [(_, default)] = list(iter_variant_sales(db, DEFAULT_CONFIG))
    [(_, with_photos)] = list(iter_variant_sales(db, DEFAULT_CONFIG, include_photos=True))

    assert [s["sold_price"] for s in default] == [10.0]
    assert len(with_photos) == 2


def test_reader_caps_and_orders_newest_first(db):
    for age in range(40):
        _sale(db, purchase=float(age), age=age)

    [(_, sales)] = list(iter_variant_sales(db, DEFAULT_CONFIG))

    assert len(sales) == DEFAULT_CONFIG.max_sales_considered
    assert sales[0]["sold_price"] == 0.0  # the newest
    assert sales[-1]["sold_price"] == 24.0


def test_variants_are_grouped_separately(db):
    _sale(db, "NM", purchase=10.0)
    _sale(db, "LP", purchase=5.0)
    _sale(db, "NM", purchase=11.0, finish="Holo")

    groups = {key: len(sales) for key, sales in iter_variant_sales(db, DEFAULT_CONFIG)}

    assert len(groups) == 3


# ─── Writing ─────────────────────────────────────────────────


def test_backfill_fills_the_pricing_input_columns(db):
    _legacy_snapshot(db, lowest=20.0, count=0)
    for age in (1, 2, 3):
        _sale(db, purchase=10.0, age=age)

    stats = backfill(db, DEFAULT_CONFIG)

    row = _snapshot(db)
    assert stats["updated"] == 1
    assert row["divergence_sale_price"] == 10.0
    assert row["weighted_sale_price"] == pytest.approx(10.0, abs=0.1)
    assert row["recent_sales_count"] == 3


def test_backfill_leaves_listing_columns_alone(db):
    # Only a real collect run observes listings; the backfill must never invent
    # or clear them.
    _legacy_snapshot(db, lowest=20.0)
    _sale(db, purchase=10.0)

    backfill(db, DEFAULT_CONFIG)

    row = _snapshot(db)
    assert row["lowest_listing_price"] == 20.0
    assert row["listing_count"] == 3


def test_backfill_targets_only_the_latest_snapshot(db):
    older = (date.today() - timedelta(days=30)).isoformat()
    _legacy_snapshot(db, snapshot_date=older, lowest=99.0)
    _legacy_snapshot(db, snapshot_date=TODAY, lowest=20.0)
    _sale(db, purchase=10.0)

    backfill(db, DEFAULT_CONFIG)

    assert _snapshot(db, snapshot_date=TODAY)["weighted_sale_price"] is not None
    assert _snapshot(db, snapshot_date=older)["weighted_sale_price"] is None


def test_variant_without_a_snapshot_is_skipped_not_created(db):
    """A fabricated row would assert we checked the market that day and saw no
    listings — we never checked. Those variants need a collect run."""
    _sale(db, purchase=10.0)

    stats = backfill(db, DEFAULT_CONFIG)

    assert stats["no_snapshot"] == 1
    assert stats["updated"] == 0
    assert db.execute("SELECT COUNT(*) c FROM market_snapshots").fetchone()["c"] == 0


def test_backfill_is_idempotent(db):
    _legacy_snapshot(db, lowest=20.0)
    _sale(db, purchase=10.0)

    backfill(db, DEFAULT_CONFIG)
    first = dict(_snapshot(db))
    backfill(db, DEFAULT_CONFIG)

    assert dict(_snapshot(db)) == first


def test_dry_run_writes_nothing(db):
    _legacy_snapshot(db, lowest=20.0)
    _sale(db, purchase=10.0)

    stats = backfill(db, DEFAULT_CONFIG, dry_run=True)

    assert stats["updated"] == 1
    assert _snapshot(db)["weighted_sale_price"] is None


def test_half_life_option_changes_the_stored_weighting(db):
    _legacy_snapshot(db, lowest=400.0)
    for _ in range(3):
        _sale(db, purchase=200.0, age=45)
    for _ in range(12):
        _sale(db, purchase=120.0, age=120)

    backfill(db, DEFAULT_CONFIG)
    default = _snapshot(db)["weighted_sale_price"]
    backfill(db, replace(DEFAULT_CONFIG, sold_weight_half_life_days=15.0))
    shorter = _snapshot(db)["weighted_sale_price"]

    assert shorter > default  # discounts the older $120 tail harder


# ─── End to end ──────────────────────────────────────────────


def test_backfill_makes_a_skipped_variant_priceable(db):
    """The whole point: before the backfill the repricer refuses the row (sales
    counted, statistics missing); after it, the variant prices normally."""
    cur = db.execute(
        "INSERT INTO skus (card_id, condition, finish, specialty_one, specialty_two, qty)"
        " VALUES ('88075','NM','Regular','None','None',1)"
    )
    sku_id = cur.lastrowid
    db.execute("INSERT INTO inventory (sku_id, qty) VALUES (?, 1)", (sku_id,))
    _legacy_snapshot(db, lowest=20.0, count=9)  # sales seen, statistics absent
    for age in (1, 2, 3):
        _sale(db, purchase=10.0, age=age)

    assert Repricer(db).price_card("88075") == 0  # skipped, nothing written

    backfill(db, DEFAULT_CONFIG)
    Repricer(db).price_card("88075")

    price = db.execute(
        "SELECT estimated_price FROM prices WHERE sku_id = ?", (sku_id,)
    ).fetchone()["estimated_price"]
    # $20 listing against a $10 sold signal -> blended, not the bare anchor.
    assert price == 13.0


def test_sold_only_variant_prices_after_backfill(db):
    # The 18k-row case: sales but no listing. Before the backfill this repriced
    # to NULL and got flagged unpriceable; now it prices off the sold mean.
    cur = db.execute(
        "INSERT INTO skus (card_id, condition, finish, specialty_one, specialty_two, qty)"
        " VALUES ('88075','NM','Regular','None','None',1)"
    )
    sku_id = cur.lastrowid
    db.execute("INSERT INTO inventory (sku_id, qty) VALUES (?, 1)", (sku_id,))
    _legacy_snapshot(db, lowest=None, count=5)
    for age in (1, 2, 3, 4):
        _sale(db, purchase=12.0, age=age)

    backfill(db, DEFAULT_CONFIG)
    Repricer(db).price_card("88075")

    row = db.execute("SELECT * FROM prices WHERE sku_id = ?", (sku_id,)).fetchone()
    assert row["estimated_price"] == pytest.approx(12.0, abs=0.1)
    assert row["manual_check_necessary"] == 0
