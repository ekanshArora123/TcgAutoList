"""Tests for the raw sales data layer — fetch mapping + SalesStore.

Covers the pure page-mapping (field mapping, custom-listing filtering, the
newest-first cutoff stop) and the replace-per-card storage semantics. No
network: map_sales is pure, and the store runs against an in-memory DB.

Run: pytest -q
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from services.card_server.db import _upgrade_graph_specialty
from services.card_server.helpers.crud.cards import CardsHelper
from services.card_server.helpers.market.sales_store import SalesStore
from services.card_server.helpers.tcgplayer.fetch_sales_history import map_sales
from services.card_server.services.reporting_service import ReportingService

_SCHEMA = Path("services/card_server/schema.sql").read_text(encoding="utf-8")


@pytest.fixture
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    CardsHelper(conn).upsert({"id": "111", "card_name": "Charizard", "set_name": "Base Set"})
    return conn


# ─── map_sales (pure) ────────────────────────────────────────


def _raw(order_date: str, *, price=10.0, ship=1.0, qty=1, condition="Near Mint",
         variant="Holofoil", custom="0") -> dict:
    return {
        "orderDate": order_date, "purchasePrice": price, "shippingPrice": ship,
        "quantity": qty, "condition": condition, "variant": variant, "customListingId": custom,
    }


def test_map_sales_maps_fields_and_normalizes_codes():
    rows, reached = map_sales([_raw("2025-06-01T10:00:00")], "111", "", cutoff="2024-01-01")
    assert reached is False
    assert len(rows) == 1
    r = rows[0]
    assert r["card_id"] == "111"
    assert r["condition"] == "NM"          # "Near Mint" -> NM
    assert r["finish"] == "Holo"           # "Holofoil" -> Holo
    assert r["purchase_price"] == 10.0     # kept separate from shipping
    assert r["shipping_price"] == 1.0
    assert r["quantity"] == 1
    assert r["source"] == "tcgplayer"
    assert r["has_image"] == 0             # standard listing
    assert r["specialty_one"] == "None"    # plain printing


def test_map_sales_keeps_the_printing_apart():
    """`variant` carries the printing, which yields both finish and specialty.

    Dropping the specialty pooled $27 1st Edition sales with $2 Unlimited ones
    in a single graph series for every WOTC card.
    """
    rows, _ = map_sales(
        [_raw("2025-06-01T10:00:00", variant="1st Edition Holofoil"),
         _raw("2025-06-01T11:00:00", variant="Unlimited Holofoil")],
        "111", "", cutoff="2024-01-01",
    )
    assert [r["specialty_one"] for r in rows] == ["First Edition", "None"]
    assert all(r["finish"] == "Holo" for r in rows)  # same finish, different printing


def test_map_sales_flags_photo_listings_and_skips_dateless_rows():
    data = [
        _raw("2025-06-01T10:00:00", custom="98765"),  # photo/custom listing -> flagged, not dropped
        {"purchasePrice": 5.0, "customListingId": "0"},  # no orderDate -> skipped
        _raw("2025-06-02T10:00:00"),  # standard keeper
    ]
    rows, _ = map_sales(data, "111", "", cutoff="2024-01-01")
    assert len(rows) == 2
    by_date = {r["order_date"]: r for r in rows}
    assert by_date["2025-06-01T10:00:00"]["has_image"] == 1
    assert by_date["2025-06-02T10:00:00"]["has_image"] == 0


def test_map_sales_flags_cutoff_and_drops_old_rows():
    # Newest-first page that crosses the cutoff partway through.
    data = [_raw("2025-06-02T00:00:00"), _raw("2023-01-01T00:00:00")]
    rows, reached = map_sales(data, "111", "", cutoff="2024-01-01T00:00:00")
    assert reached is True
    assert [r["order_date"] for r in rows] == ["2025-06-02T00:00:00"]


# ─── SalesStore ──────────────────────────────────────────────


def _sale(order_date: str, condition="NM", price=10.0) -> dict:
    return {
        "card_id": "111", "condition": condition, "finish": "Holo",
        "source": "tcgplayer", "order_date": order_date,
        "purchase_price": price, "shipping_price": 1.0, "quantity": 1,
    }


def test_replace_card_inserts_then_fully_replaces(db):
    store = SalesStore(db)
    n = store.replace_card("111", [_sale("2025-01-01"), _sale("2025-01-02")])
    assert n == 2
    assert store.count_for_card("111") == 2

    # A fresh pull replaces (not appends) the prior rows.
    store.replace_card("111", [_sale("2025-02-01")])
    assert store.count_for_card("111") == 1
    assert store.get_for_card("111")[0]["order_date"] == "2025-02-01"


def test_replace_card_skips_empty_pull(db):
    store = SalesStore(db)
    store.replace_card("111", [_sale("2025-01-01")])
    # An empty pull must not wipe previously-collected history.
    assert store.replace_card("111", []) == 0
    assert store.count_for_card("111") == 1


# ─── card_sales_history rollup ───────────────────────────────


def _hsale(order_date, condition, price, ship=1.0, qty=1, finish="Holo", has_image=0) -> dict:
    return {
        "card_id": "111", "condition": condition, "finish": finish, "source": "tcgplayer",
        "order_date": order_date, "purchase_price": price, "shipping_price": ship,
        "quantity": qty, "has_image": has_image,
    }


def test_card_sales_history_daily_rollup(db):
    SalesStore(db).replace_card("111", [
        _hsale("2025-06-01T10:00:00", "NM", 10.0, qty=1),
        _hsale("2025-06-01T12:00:00", "NM", 12.0, qty=2),  # same day, same condition
        _hsale("2025-06-02T09:00:00", "LP", 8.0, qty=1),
    ])
    hist = ReportingService(db).card_sales_history("111", finish="Holo", days=100000)

    assert hist["conditions"] == ["LP", "NM"]
    pts = {(p["date"], p["condition"]): p for p in hist["points"]}
    nm = pts[("2025-06-01", "NM")]
    assert nm["median_price"] == 12.0  # median of (10+1) and (12+1), incl. shipping
    assert nm["volume"] == 3           # 1 + 2 quantities
    assert pts[("2025-06-02", "LP")]["median_price"] == 9.0


def test_card_sales_history_uses_median_not_mean(db):
    # Three NM sales (11, 12, 100) -> median 12, not the mean of ~41.
    SalesStore(db).replace_card("111", [
        _hsale("2025-06-01T10:00:00", "NM", 10.0, ship=1.0),   # 11
        _hsale("2025-06-01T11:00:00", "NM", 11.0, ship=1.0),   # 12
        _hsale("2025-06-01T12:00:00", "NM", 99.0, ship=1.0),   # 100 (outlier)
    ])
    nm = ReportingService(db).card_sales_history("111", finish="Holo", days=100000)["points"][0]
    assert nm["median_price"] == 12.0
    assert nm["min_price"] == 11.0
    assert nm["max_price"] == 100.0


def test_card_sales_history_filters_finish(db):
    SalesStore(db).replace_card("111", [
        _hsale("2025-06-01T10:00:00", "NM", 10.0, finish="Holo"),
        _hsale("2025-06-01T10:00:00", "NM", 5.0, finish="Regular"),
    ])
    holo = ReportingService(db).card_sales_history("111", finish="Holo", days=100000)
    assert holo["conditions"] == ["NM"]
    assert all(p["median_price"] == 11.0 for p in holo["points"])  # only the Holo sale


def test_card_sales_points_expands_by_quantity(db):
    SalesStore(db).replace_card("111", [
        _hsale("2025-06-01T10:00:00", "NM", 10.0, ship=1.0, qty=3),  # -> 3 points at 11.0
        _hsale("2025-06-02T10:00:00", "LP", 7.0, ship=1.0, qty=1),   # -> 1 point at 8.0
    ])
    res = ReportingService(db).card_sales_points("111", finish="Holo", days=100000)
    assert sorted(res["conditions"]) == ["LP", "NM"]
    nm = [p for p in res["points"] if p["condition"] == "NM"]
    assert len(nm) == 3 and all(p["price"] == 11.0 and p["order_date"] == "2025-06-01T10:00:00" for p in nm)
    lp = [p for p in res["points"] if p["condition"] == "LP"]
    assert len(lp) == 1 and lp[0]["price"] == 8.0 and lp[0]["order_date"] == "2025-06-02T10:00:00"


def test_card_sales_history_excludes_photo_listings_by_default(db):
    SalesStore(db).replace_card("111", [
        _hsale("2025-06-01T10:00:00", "NM", 10.0),                # standard
        _hsale("2025-06-01T11:00:00", "NM", 50.0, has_image=1),   # photo listing
    ])
    svc = ReportingService(db)

    default = svc.card_sales_history("111", finish="Holo", days=100000)
    assert default["include_images"] is False
    nm = {(p["date"], p["condition"]): p for p in default["points"]}[("2025-06-01", "NM")]
    assert nm["volume"] == 1            # photo sale excluded
    assert nm["median_price"] == 11.0  # only the standard sale (10 + 1)

    withimg = svc.card_sales_history("111", finish="Holo", days=100000, include_images=True)
    assert withimg["include_images"] is True
    nm2 = {(p["date"], p["condition"]): p for p in withimg["points"]}[("2025-06-01", "NM")]
    assert nm2["volume"] == 2           # both counted
    assert nm2["median_price"] == 31.0  # median of (10+1) and (50+1)


def test_card_sales_history_separates_printings(db):
    # Same card_id, same finish, same day — different printing. The 1st Edition
    # sale must not land in the plain variant's series (or vice versa).
    SalesStore(db).replace_card("111", [
        {**_hsale("2025-06-01T10:00:00", "NM", 2.0), "specialty_one": "None"},
        {**_hsale("2025-06-01T11:00:00", "NM", 27.0), "specialty_one": "First Edition"},
    ])
    svc = ReportingService(db)

    plain = svc.card_sales_history("111", finish="Holo", days=100000)
    assert [p["median_price"] for p in plain["points"]] == [3.0]  # 2 + 1 shipping

    first_ed = svc.card_sales_history(
        "111", finish="Holo", days=100000, specialty_one="First Edition"
    )
    assert [p["median_price"] for p in first_ed["points"]] == [28.0]
    assert first_ed["specialty_one"] == "First Edition"


def test_card_sales_points_separates_printings(db):
    SalesStore(db).replace_card("111", [
        {**_hsale("2025-06-01T10:00:00", "NM", 2.0), "specialty_one": "None"},
        {**_hsale("2025-06-01T11:00:00", "NM", 27.0), "specialty_one": "First Edition"},
    ])
    res = ReportingService(db).card_sales_points(
        "111", finish="Holo", days=100000, specialty_one="First Edition"
    )
    assert [p["price"] for p in res["points"]] == [28.0]


def test_card_sales_history_respects_window(db):
    # A sale far in the past is excluded by a short window.
    SalesStore(db).replace_card("111", [_hsale("2000-01-01T10:00:00", "NM", 10.0)])
    hist = ReportingService(db).card_sales_history("111", finish="Holo", days=30)
    assert hist["points"] == []
    assert hist["conditions"] == []


# ─── specialty_one migration ─────────────────────────────────


def test_upgrade_adds_specialty_to_legacy_graph_tables():
    """The live DB predates the column, so the upgrade has to add it in place.

    Existing rows land on 'None' (they self-heal on the next collect_sales run,
    which replaces a card's rows wholesale), and the stale variant index is
    rebuilt to include the new column.
    """
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT, card_id TEXT NOT NULL,
            condition TEXT NOT NULL, finish TEXT NOT NULL DEFAULT 'Regular',
            source TEXT NOT NULL DEFAULT 'tcgplayer', order_date TEXT NOT NULL,
            purchase_price REAL NOT NULL, shipping_price REAL DEFAULT 0,
            quantity INTEGER DEFAULT 1, has_image INTEGER NOT NULL DEFAULT 0,
            fetched_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX idx_sales_variant ON sales(card_id, condition, finish, source);
        CREATE TABLE market_price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT, card_id TEXT NOT NULL,
            condition TEXT NOT NULL, finish TEXT NOT NULL DEFAULT 'Regular',
            source TEXT NOT NULL DEFAULT 'tcgplayer', bucket_date TEXT NOT NULL,
            market_price REAL, low_sale_price REAL, high_sale_price REAL,
            quantity_sold INTEGER, transaction_count INTEGER,
            fetched_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX idx_mph_variant ON market_price_history(card_id, condition, finish, source);
        INSERT INTO sales (card_id, condition, order_date, purchase_price)
          VALUES ('111', 'NM', '2025-06-01T10:00:00', 10.0);
        """
    )

    _upgrade_graph_specialty(conn)

    assert conn.execute("SELECT specialty_one FROM sales").fetchone()["specialty_one"] == "None"
    for table, index in (("sales", "idx_sales_variant"), ("market_price_history", "idx_mph_variant")):
        assert "specialty_one" in {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        indexed = [r["name"] for r in conn.execute(f"PRAGMA index_info({index})")]
        assert "specialty_one" in indexed

    _upgrade_graph_specialty(conn)  # idempotent — a second run is a no-op
    assert conn.execute("SELECT COUNT(*) c FROM sales").fetchone()["c"] == 1
