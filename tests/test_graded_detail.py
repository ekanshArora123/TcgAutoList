"""Tests for the graded card detail page + the slab 'to_crack' tag.

Covers the two-tier read (grade classes grouped by grader_spec_id + the owned
slabs), the single-sku fallback when a sku has no spec, the shared tag-CSV
helper, and the graded add/remove-tag write path.

Run: pytest -q
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from services.card_server.helpers.crud import tags as tags_helper
from services.card_server.helpers.crud.cards import CardsHelper
from services.card_server.helpers.crud.graded_inventory import GradedInventoryHelper
from services.card_server.helpers.crud.graded_skus import GradedSkusHelper
from services.card_server.services.collection_service import CollectionService
from services.card_server.services.reporting_service import ReportingService

_SCHEMA = Path("services/card_server/schema.sql").read_text(encoding="utf-8")


@pytest.fixture
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _sku(db, *, grade, spec="7917434", company="PSA", **extra):
    """Create a graded_sku carrying a grader spec + identity."""
    params = {
        "grading_company": company,
        "grade": grade,
        "grader_spec_id": spec,
        "card_subject": "CHARIZARD",
        "card_set": "BASE SET",
        "card_number": "4/102",
        "card_year": "1999",
        "card_variety": "HOLO",
        "card_language": "English",
        "finish": "Holo",
        **extra,
    }
    return GradedSkusHelper(db).get_or_create(params)


# ─── tag CSV helper (pure) ───────────────────────────────────


def test_tags_helper_add_remove():
    assert tags_helper.add_tag(None, "to_crack") == "to_crack"
    assert tags_helper.add_tag("trade", "to_crack") == "trade,to_crack"
    assert tags_helper.add_tag("to_crack", "to_crack") == "to_crack"  # idempotent
    assert tags_helper.remove_tag("trade,to_crack", "to_crack") == "trade"
    assert tags_helper.remove_tag("to_crack", "to_crack") is None
    assert tags_helper.parse_tags(" a , b ,,c ") == ["a", "b", "c"]


# ─── graded detail: two-tier grouping ────────────────────────


def test_slab_detail_groups_grades_by_spec(db):
    inv = GradedInventoryHelper(db)
    sku10 = _sku(db, grade=10)
    sku9 = _sku(db, grade=9)
    inv.create({"graded_sku_id": sku10["graded_sku_id"], "cert_id": "AAA", "qty": 1})
    inv.create({"graded_sku_id": sku10["graded_sku_id"], "cert_id": "BBB", "qty": 1})
    inv.create({"graded_sku_id": sku9["graded_sku_id"], "cert_id": "CCC", "qty": 1})

    detail = ReportingService(db).graded_slab_detail("BBB")
    assert detail is not None
    # The page focuses on the entered cert.
    assert detail["slab"]["cert_id"] == "BBB"
    assert detail["slab"]["grade"] == 10.0
    assert detail["identity"]["card_name"] == "CHARIZARD"
    assert detail["identity"]["grading_company"] == "PSA"

    # Spec context: both grades present, ordered best -> worst, qty per grade.
    grades = {g["grade"]: g for g in detail["grades"]}
    assert [g["grade"] for g in detail["grades"]] == [10.0, 9.0]
    assert grades[10.0]["qty"] == 2  # two owned certs at grade 10
    assert grades[9.0]["qty"] == 1


def test_slab_detail_separates_different_spec(db):
    """A different card (different spec) is not folded into the same page."""
    inv = GradedInventoryHelper(db)
    charizard = _sku(db, grade=10, spec="7917434")
    pikachu = _sku(db, grade=10, spec="9999999", card_subject="PIKACHU")
    inv.create({"graded_sku_id": charizard["graded_sku_id"], "cert_id": "AAA", "qty": 1})
    inv.create({"graded_sku_id": pikachu["graded_sku_id"], "cert_id": "ZZZ", "qty": 1})

    detail = ReportingService(db).graded_slab_detail("AAA")
    assert detail["slab"]["cert_id"] == "AAA"
    assert len(detail["grades"]) == 1  # pikachu (different spec) not folded in


def test_slab_detail_null_spec_is_single_sku_group(db):
    inv = GradedInventoryHelper(db)
    # No grader_spec_id -> legacy/manual add; page is just this sku.
    sku = GradedSkusHelper(db).get_or_create(
        {"grading_company": "PSA", "grade": 8, "card_subject": "MEW"}
    )
    inv.create({"graded_sku_id": sku["graded_sku_id"], "cert_id": "SOLO", "qty": 1})

    detail = ReportingService(db).graded_slab_detail("SOLO")
    assert len(detail["grades"]) == 1
    assert detail["grades"][0]["grade"] == 8.0
    assert detail["identity"]["card_name"] == "MEW"


def test_slab_detail_unknown_cert_returns_none(db):
    assert ReportingService(db).graded_slab_detail("nope") is None


# ─── to_crack tag write path ─────────────────────────────────


def test_set_graded_slab_tag_toggles_to_crack(db):
    cs = CollectionService(db)
    sku = _sku(db, grade=10)
    slab = GradedInventoryHelper(db).create(
        {"graded_sku_id": sku["graded_sku_id"], "cert_id": "AAA", "qty": 1}
    )
    gid = slab["graded_inventory_id"]

    cs.set_graded_slab_tag(gid, "to_crack", True)
    detail = ReportingService(db).graded_slab_detail("AAA")
    assert detail["slab"]["to_crack"] is True
    assert "to_crack" in detail["slab"]["tags"]

    cs.set_graded_slab_tag(gid, "to_crack", False)
    detail = ReportingService(db).graded_slab_detail("AAA")
    assert detail["slab"]["to_crack"] is False
