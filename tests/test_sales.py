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

from services.card_server.helpers.crud.cards import CardsHelper
from services.card_server.helpers.market.sales_store import SalesStore
from services.card_server.helpers.tcgplayer.fetch_sales_history import map_sales

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


def test_map_sales_skips_custom_listings_and_dateless_rows():
    data = [
        _raw("2025-06-01T10:00:00", custom="98765"),  # custom/photo listing
        {"purchasePrice": 5.0, "customListingId": "0"},  # no orderDate
        _raw("2025-06-02T10:00:00"),  # keeper
    ]
    rows, _ = map_sales(data, "111", "", cutoff="2024-01-01")
    assert len(rows) == 1
    assert rows[0]["order_date"] == "2025-06-02T10:00:00"


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
