"""Tests for editing owned quantities by condition.

Covers the reusable primitive (InventoryHelper.set_untagged_qty) and the service
seam (CollectionService.set_condition_qty / set_condition_quantities): absolute
set semantics, adding an unowned condition, row deletion at 0, and preservation
of tagged / listing-committed copies.

Run: pytest -q
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from services.card_server.helpers.crud.cards import CardsHelper
from services.card_server.helpers.crud.inventory import InventoryHelper
from services.card_server.helpers.crud.skus import SkusHelper
from services.card_server.services.collection_service import CollectionService

_SCHEMA = Path("services/card_server/schema.sql").read_text(encoding="utf-8")


@pytest.fixture
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    CardsHelper(conn).upsert({"id": "111", "card_name": "Charizard", "set_name": "Base Set"})
    return conn


def _owned(db: sqlite3.Connection, sku_id: int) -> int:
    """Total UNTAGGED qty for a sku — what the detail rollup displays."""
    row = db.execute(
        "SELECT COALESCE(SUM(qty), 0) n FROM inventory "
        "WHERE sku_id = ? AND (tags IS NULL OR TRIM(tags) = '')",
        (sku_id,),
    ).fetchone()
    return row["n"]


def _sku(db: sqlite3.Connection, condition: str) -> int:
    return SkusHelper(db).get_or_create({"card_id": "111", "condition": condition})["sku_id"]


# ─── primitive: set_untagged_qty ─────────────────────────────


def test_decrement_single_row(db):
    inv = InventoryHelper(db)
    sku_id = _sku(db, "NM")
    inv.create({"sku_id": sku_id, "qty": 5, "status": "unlisted"})

    assert inv.set_untagged_qty(sku_id, 2) == 2
    assert _owned(db, sku_id) == 2
    # sku aggregate stays in sync
    assert SkusHelper(db).get_by_id(sku_id)["qty"] == 2


def test_set_to_zero_deletes_row(db):
    inv = InventoryHelper(db)
    sku_id = _sku(db, "NM")
    inv.create({"sku_id": sku_id, "qty": 3, "status": "unlisted"})

    assert inv.set_untagged_qty(sku_id, 0) == 0
    assert _owned(db, sku_id) == 0
    remaining = db.execute("SELECT COUNT(*) c FROM inventory WHERE sku_id = ?", (sku_id,)).fetchone()
    assert remaining["c"] == 0


def test_increment_grows_existing_plain_row(db):
    inv = InventoryHelper(db)
    sku_id = _sku(db, "NM")
    row = inv.create({"sku_id": sku_id, "qty": 1, "status": "unlisted"})

    assert inv.set_untagged_qty(sku_id, 4) == 4
    # Grew the existing row rather than spawning a second one.
    rows = db.execute("SELECT * FROM inventory WHERE sku_id = ?", (sku_id,)).fetchall()
    assert len(rows) == 1
    assert rows[0]["inventory_id"] == row["inventory_id"]
    assert rows[0]["qty"] == 4


def test_increment_from_zero_creates_row(db):
    inv = InventoryHelper(db)
    sku_id = _sku(db, "LP")  # no inventory yet

    assert inv.set_untagged_qty(sku_id, 3) == 3
    rows = db.execute("SELECT * FROM inventory WHERE sku_id = ?", (sku_id,)).fetchall()
    assert len(rows) == 1
    assert rows[0]["qty"] == 3
    assert rows[0]["status"] == "unlisted"


def test_tagged_rows_untouched(db):
    inv = InventoryHelper(db)
    sku_id = _sku(db, "NM")
    inv.create({"sku_id": sku_id, "qty": 2, "status": "unlisted"})
    inv.create({"sku_id": sku_id, "qty": 1, "tags": "hidden crease"})

    # Zero out the untagged pool; the tagged copy survives.
    assert inv.set_untagged_qty(sku_id, 0) == 0
    assert _owned(db, sku_id) == 0
    tagged = db.execute(
        "SELECT COUNT(*) c FROM inventory WHERE sku_id = ? AND tags = 'hidden crease'", (sku_id,)
    ).fetchone()
    assert tagged["c"] == 1


def test_decrement_prefers_uncommitted_rows(db):
    inv = InventoryHelper(db)
    sku_id = _sku(db, "NM")
    listed = inv.create({"sku_id": sku_id, "qty": 1, "status": "listed"})
    inv.create({"sku_id": sku_id, "qty": 3, "status": "unlisted"})

    # Owned = 4; drop to 1 → the plain 3 should be shaved, the listed copy kept.
    assert inv.set_untagged_qty(sku_id, 1) == 1
    survivor = db.execute("SELECT * FROM inventory WHERE sku_id = ?", (sku_id,)).fetchall()
    assert len(survivor) == 1
    assert survivor[0]["inventory_id"] == listed["inventory_id"]
    assert survivor[0]["status"] == "listed"


def test_noop_when_already_at_target(db):
    inv = InventoryHelper(db)
    sku_id = _sku(db, "NM")
    row = inv.create({"sku_id": sku_id, "qty": 2, "status": "unlisted"})

    assert inv.set_untagged_qty(sku_id, 2) == 2
    rows = db.execute("SELECT * FROM inventory WHERE sku_id = ?", (sku_id,)).fetchall()
    assert len(rows) == 1 and rows[0]["inventory_id"] == row["inventory_id"]


# ─── service: set_condition_qty / batch ──────────────────────


def test_service_adds_unowned_condition(db):
    svc = CollectionService(db)
    # LP isn't owned and its sku may not exist yet — service get_or_creates it.
    assert svc.set_condition_qty("111", "LP", 2) == 2
    sku_id = _sku(db, "LP")
    assert _owned(db, sku_id) == 2


def test_service_unknown_card_returns_none(db):
    svc = CollectionService(db)
    assert svc.set_condition_qty("does-not-exist", "NM", 1) is None
    assert svc.set_condition_quantities("does-not-exist", {"NM": 1}) is None


def test_service_batch_sets_multiple_conditions(db):
    svc = CollectionService(db)
    InventoryHelper(db).create({"sku_id": _sku(db, "NM"), "qty": 6, "status": "unlisted"})

    result = svc.set_condition_quantities("111", {"NM": 2, "LP": 3})
    assert result == {"NM": 2, "LP": 3}
    assert _owned(db, _sku(db, "NM")) == 2
    assert _owned(db, _sku(db, "LP")) == 3
