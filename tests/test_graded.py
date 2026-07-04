"""Tests for the parallel graded-card model: graded_skus / graded_inventory /
graded_prices, the add-graded write path, the graded browse + detail (with the
raw-vs-graded comparison), that the raw path is unaffected, and the migration.

Run: pytest -q
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from services.card_server.helpers.crud.cards import CardsHelper
from services.card_server.helpers.crud.graded_skus import GradedSkusHelper
from services.card_server.helpers.crud.prices import PricesHelper
from services.card_server.services.collection_service import CollectionService
from services.card_server.services.reporting_service import ReportingService

_SCHEMA = Path("services/card_server/schema.sql").read_text(encoding="utf-8")


@pytest.fixture
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    CardsHelper(conn).upsert({"id": "4", "card_name": "Charizard", "set_name": "Base Set",
                              "rarity": "Rare Holo", "era": "Vintage", "card_type": "Pokemon",
                              "card_number": "4/102"})
    return conn


# ─── graded_skus CRUD ────────────────────────────────────────


def test_graded_sku_get_or_create_is_idempotent(db):
    g = GradedSkusHelper(db)
    a = g.get_or_create({"card_id": "4", "finish": "Holo", "grading_company": "PSA", "grade": 10})
    b = g.get_or_create({"card_id": "4", "finish": "Holo", "grading_company": "PSA", "grade": 10})
    assert a["graded_sku_id"] == b["graded_sku_id"]
    c = g.get_or_create({"card_id": "4", "finish": "Holo", "grading_company": "PSA", "grade": 9})
    d = g.get_or_create({"card_id": "4", "finish": "Holo", "grading_company": "CGC", "grade": 10})
    assert len({a["graded_sku_id"], c["graded_sku_id"], d["graded_sku_id"]}) == 3
    assert g.get_all_companies() == ["CGC", "PSA"]


# ─── add_graded_to_inventory + graded reads ──────────────────


def test_add_graded_creates_sku_and_slab(db):
    cs = CollectionService(db)
    item = cs.add_graded_to_inventory(
        {"card_id": "4", "finish": "Holo", "grading_company": "PSA", "grade": 10, "cert_id": "12345678"}
    )
    assert item["graded_inventory_id"] is not None
    assert item["cert_id"] == "12345678"

    detail = cs.get_graded_inventory_detail(item["graded_inventory_id"])
    assert detail["grading_company"] == "PSA"
    assert detail["grade"] == 10.0
    assert detail["card_id"] == "4"
    assert detail["finish"] == "Holo"
    assert GradedSkusHelper(db).get_by_id(item["graded_sku_id"])["qty"] == 1


def test_browse_graded_and_detail_with_raw_comparison(db):
    cs = CollectionService(db)
    # Raw NM Holo with a price (the comparison anchor).
    raw = cs.add_to_inventory({"card_id": "4", "condition": "NM", "finish": "Holo"})
    PricesHelper(db).upsert({"sku_id": raw["sku_id"], "calculation_date": "2999-01-01",
                             "estimated_price": 120.0})
    # PSA 10 slab with its own graded price.
    slab = cs.add_graded_to_inventory({"card_id": "4", "finish": "Holo",
                                       "grading_company": "PSA", "grade": 10})
    cs.graded_prices.upsert({"graded_sku_id": slab["graded_sku_id"],
                             "calculation_date": "2999-01-01", "estimated_price": 900.0})

    rs = ReportingService(db)
    page = rs.browse_graded({})
    assert page["total"] == 1
    assert page["items"][0]["grading_company"] == "PSA"
    assert page["items"][0]["estimated_price"] == 900.0

    detail = rs.graded_card_detail("4", finish="Holo", grading_company="PSA", grade=10)
    assert detail["kind"] == "graded"
    assert detail["estimated_price"] == 900.0
    assert detail["raw_estimated_price"] == 120.0  # raw sibling via shared card_id
    assert rs.graded_filter_options()["grading_companies"] == ["PSA"]


def test_raw_path_is_unaffected_by_graded(db):
    # Adding a graded slab must not appear in the raw collection views.
    cs = CollectionService(db)
    cs.add_to_inventory({"card_id": "4", "condition": "NM", "finish": "Holo"})
    cs.add_graded_to_inventory({"card_id": "4", "finish": "Holo", "grading_company": "PSA", "grade": 10})
    rs = ReportingService(db)
    assert rs.browse_collection({})["total"] == 1  # raw only
    assert rs.browse_cards({})["total"] == 1
    assert cs.inventory.get_stats()["total"] == 1  # raw inventory unchanged


# ─── migration: relocate legacy graded SKUs (+ their inventory) ──


def test_migration_relocates_graded_and_leaves_raw(tmp_path):
    from services.card_server import migrate_graded
    from services.card_server.db import init_database

    p = str(tmp_path / "legacy.db")
    db = init_database(p)  # creates raw + graded tables
    db.execute("INSERT INTO cards(id,card_name,set_name) VALUES('4','Charizard','Base')")
    # raw (keep), graded PSA9 WITH a physical inventory row + price (move), error MisCut (keep)
    db.execute("INSERT INTO skus(card_id,condition,finish,specialty_one,specialty_two) VALUES('4','NM','Holo','None','None')")
    db.execute("INSERT INTO skus(card_id,condition,finish,specialty_one,specialty_two) VALUES('4','NM','Holo','None','PSA9')")
    db.execute("INSERT INTO skus(card_id,condition,finish,specialty_one,specialty_two) VALUES('4','NM','Holo','None','MisCut')")
    db.execute("INSERT INTO inventory(sku_id,qty,status) VALUES(1,1,'unlisted')")
    db.execute("INSERT INTO inventory(sku_id,qty,status,front_photo_path) VALUES(2,1,'listed','/f.jpg')")
    db.execute("INSERT INTO inventory(sku_id,qty,status) VALUES(3,1,'unlisted')")
    db.execute("INSERT INTO prices(sku_id,calculation_date,estimated_price) VALUES(2,'2999-01-01',250.0)")
    db.execute("UPDATE skus SET latest_calc_date='2999-01-01' WHERE sku_id=2")

    res = migrate_graded.run(db_path=p)
    assert res["moved_skus"] == 1 and res["moved_inventory"] == 1

    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    gs = conn.execute("SELECT * FROM graded_skus").fetchall()
    assert len(gs) == 1 and gs[0]["grading_company"] == "PSA" and gs[0]["grade"] == 9.0
    gi = conn.execute("SELECT * FROM graded_inventory").fetchall()
    assert len(gi) == 1 and gi[0]["status"] == "listed" and gi[0]["front_photo_path"] == "/f.jpg"
    assert conn.execute("SELECT estimated_price FROM graded_prices").fetchone()["estimated_price"] == 250.0
    # raw inventory keeps the raw + error rows; the graded one was moved out
    raw_inv = {r["sku_id"] for r in conn.execute("SELECT sku_id FROM inventory")}
    assert raw_inv == {1, 3}
    left = {r["specialty_two"] for r in conn.execute("SELECT specialty_two FROM skus")}
    assert "MisCut" in left and "PSA9" not in left
    conn.close()

    # Idempotent
    assert migrate_graded.run(db_path=p)["moved_skus"] == 0
