"""Tests for the market-price-history data layer — mapping + store + rollup.

map_price_history is pure (no network); the store and reporting rollup run
against an in-memory DB.

Run: pytest -q
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from services.card_server.helpers.crud.cards import CardsHelper
from services.card_server.helpers.market.price_history_store import MarketPriceStore
from services.card_server.helpers.tcgplayer.fetch_price_history import map_price_history
from services.card_server.services.reporting_service import ReportingService

_SCHEMA = Path("services/card_server/schema.sql").read_text(encoding="utf-8")


@pytest.fixture
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    CardsHelper(conn).upsert({"id": "111", "card_name": "Charizard", "set_name": "Base Set"})
    return conn


# ─── map_price_history (pure) ────────────────────────────────


def test_map_price_history_flattens_and_parses_strings():
    api = [
        {
            "variant": "Holofoil",
            "condition": "Near Mint",
            "buckets": [
                {"marketPrice": "205.16", "quantitySold": "1", "transactionCount": "1",
                 "lowSalePrice": "192.99", "highSalePrice": "199.19", "bucketStartDate": "2026-06-22"},
                {"marketPrice": "205.44", "bucketStartDate": "2026-06-15"},
                {"marketPrice": "x", "bucketStartDate": ""},  # no date -> skipped
            ],
        }
    ]
    rows = map_price_history(api, "111")
    assert len(rows) == 2  # dateless row dropped
    r = rows[0]
    assert r["card_id"] == "111"
    assert r["condition"] == "NM"          # "Near Mint" -> NM
    assert r["finish"] == "Holo"           # "Holofoil" -> Holo
    assert r["market_price"] == 205.16     # parsed from string
    assert r["quantity_sold"] == 1
    assert r["bucket_date"] == "2026-06-22"


def test_map_price_history_handles_empty():
    assert map_price_history(None, "111") == []
    assert map_price_history([], "111") == []


# ─── store + rollup ──────────────────────────────────────────


def _row(date, market_price, condition="NM", finish="Holo") -> dict:
    return {
        "card_id": "111", "condition": condition, "finish": finish, "source": "tcgplayer",
        "bucket_date": date, "market_price": market_price,
        "low_sale_price": None, "high_sale_price": None,
        "quantity_sold": None, "transaction_count": None,
    }


def test_replace_card_and_history_rollup(db):
    store = MarketPriceStore(db)
    store.replace_card("111", [_row("2026-06-15", 200.0), _row("2026-06-22", 205.0),
                               _row("2026-06-22", 50.0, condition="LP")])
    assert store.count_for_card("111") == 3

    # Replace semantics: a fresh pull supplants the old rows.
    store.replace_card("111", [_row("2026-06-22", 210.0)])
    assert store.count_for_card("111") == 1

    hist = ReportingService(db).card_price_history("111", finish="Holo", days=100000)
    assert hist["conditions"] == ["NM"]
    assert hist["points"] == [{"date": "2026-06-22", "condition": "NM", "market_price": 210.0}]


def test_history_filters_finish(db):
    MarketPriceStore(db).replace_card("111", [
        _row("2026-06-22", 200.0, finish="Holo"),
        _row("2026-06-22", 5.0, finish="Regular"),
    ])
    holo = ReportingService(db).card_price_history("111", finish="Holo", days=100000)
    assert [p["market_price"] for p in holo["points"]] == [200.0]
