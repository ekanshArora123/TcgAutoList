"""Graded SKUs CRUD — pure DB access for the `graded_skus` table.

The graded analog of skus.py: a graded SKU is differentiated by grading_company
+ grade (instead of condition) and shares the raw card's card_id. Parallel to
the raw chain — the raw `skus` table/helper is untouched.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Optional

# All grader-identity fields are generic (any company maps onto them). card_id
# is the OPTIONAL, deferred TCGplayer link.
_COLUMNS = (
    "grading_company", "grade", "grade_label", "grader_spec_id",
    "card_year", "card_set", "card_category", "card_number", "card_subject",
    "card_variety", "card_language", "population", "population_higher", "pop_fetched_at",
    "card_id", "finish", "specialty_one", "qty",
)

_INSERT_SQL = (
    "INSERT INTO graded_skus (" + ", ".join(_COLUMNS) + ") "
    "VALUES (" + ", ".join(f":{c}" for c in _COLUMNS) + ")"
)

# Cert-sourced identity: (company, grader spec, grade).
_SPEC_LOOKUP_SQL = """
    SELECT * FROM graded_skus
    WHERE grading_company = :grading_company AND grader_spec_id = :grader_spec_id AND grade = :grade
"""

# Legacy/manual identity (no grader spec): the card-based composite key.
_LEGACY_LOOKUP_SQL = """
    SELECT * FROM graded_skus
    WHERE grader_spec_id IS NULL AND card_id IS :card_id AND finish = :finish
      AND specialty_one = :specialty_one AND grading_company = :grading_company AND grade = :grade
"""

# Everything except identity autoincrement + created_at may be updated (e.g. a pop
# refresh, or the graded->raw converter setting card_id later).
_UPDATABLE = tuple(c for c in _COLUMNS if c != "qty") + ("qty", "latest_calc_date")


def _normalize(params: dict[str, Any]) -> dict[str, Any]:
    row = {c: params.get(c) for c in _COLUMNS}
    row["grading_company"] = params["grading_company"]
    row["grade"] = float(params["grade"])
    row["finish"] = params.get("finish") or "Regular"
    row["specialty_one"] = params.get("specialty_one") or "None"
    row["qty"] = params.get("qty", 0) or 0
    return row


class GradedSkusHelper:
    def __init__(self, db: sqlite3.Connection):
        self.db = db

    def create(self, input: dict[str, Any]) -> Optional[dict]:
        cur = self.db.execute(_INSERT_SQL, _normalize(input))
        return self.get_by_id(cur.lastrowid)

    def get_or_create(self, input: dict[str, Any]) -> dict:
        norm = _normalize(input)
        # Cert-sourced cards dedup on the grader spec; manual/legacy on the card key.
        if norm.get("grader_spec_id"):
            existing = self.db.execute(_SPEC_LOOKUP_SQL, norm).fetchone()
        else:
            existing = self.db.execute(_LEGACY_LOOKUP_SQL, norm).fetchone()
        if existing:
            return dict(existing)
        return self.create(input)

    def get_by_id(self, graded_sku_id: int) -> Optional[dict]:
        row = self.db.execute(
            "SELECT * FROM graded_skus WHERE graded_sku_id = ?", (graded_sku_id,)
        ).fetchone()
        return dict(row) if row else None

    def find_by_spec(self, grading_company: str, grader_spec_id: str, grade: float) -> Optional[dict]:
        row = self.db.execute(
            _SPEC_LOOKUP_SQL,
            {"grading_company": grading_company, "grader_spec_id": grader_spec_id, "grade": float(grade)},
        ).fetchone()
        return dict(row) if row else None

    def link_card(self, graded_sku_id: int, card_id: Optional[str]) -> Optional[dict]:
        """Set/clear the optional TCGplayer card_id link. Isolated seam — the
        future graded->raw converter is the intended caller (and replaces any
        interim manual entry)."""
        self.db.execute(
            "UPDATE graded_skus SET card_id = :card_id WHERE graded_sku_id = :graded_sku_id",
            {"card_id": card_id, "graded_sku_id": graded_sku_id},
        )
        return self.get_by_id(graded_sku_id)

    def get_by_card_id(self, card_id: str) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM graded_skus WHERE card_id = ? ORDER BY grading_company, grade DESC",
            (card_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def search(self, filters: dict[str, Any]) -> list[dict]:
        conditions: list[str] = []
        params: dict[str, Any] = {}

        if filters.get("card_id"):
            conditions.append("card_id = :card_id")
            params["card_id"] = filters["card_id"]
        if filters.get("finish"):
            conditions.append("finish = :finish")
            params["finish"] = filters["finish"]
        if filters.get("grading_company"):
            conditions.append("grading_company = :grading_company")
            params["grading_company"] = filters["grading_company"]
        if filters.get("grade") is not None:
            conditions.append("grade = :grade")
            params["grade"] = float(filters["grade"])

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params["limit"] = filters.get("limit", 50)
        params["offset"] = filters.get("offset", 0)

        rows = self.db.execute(
            f"SELECT * FROM graded_skus {where} ORDER BY card_id, grading_company, grade DESC "
            "LIMIT :limit OFFSET :offset",
            params,
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_companies(self) -> list[str]:
        rows = self.db.execute(
            "SELECT DISTINCT grading_company FROM graded_skus ORDER BY grading_company"
        ).fetchall()
        return [r["grading_company"] for r in rows]

    def update(self, input: dict[str, Any]) -> Optional[dict]:
        existing = self.get_by_id(input["graded_sku_id"])
        if not existing:
            return None

        fields: list[str] = []
        params: dict[str, Any] = {"graded_sku_id": input["graded_sku_id"]}
        for field in _UPDATABLE:
            if input.get(field) is not None:
                fields.append(f"{field} = :{field}")
                params[field] = input[field]

        if not fields:
            return existing

        self.db.execute(
            f"UPDATE graded_skus SET {', '.join(fields)} WHERE graded_sku_id = :graded_sku_id",
            params,
        )
        return self.get_by_id(input["graded_sku_id"])

    def recalculate_qty(self, graded_sku_id: int) -> None:
        self.db.execute(
            "UPDATE graded_skus SET qty = "
            "(SELECT COALESCE(SUM(qty), 0) FROM graded_inventory WHERE graded_sku_id = :graded_sku_id) "
            "WHERE graded_sku_id = :graded_sku_id",
            {"graded_sku_id": graded_sku_id},
        )

    def delete(self, graded_sku_id: int) -> bool:
        cur = self.db.execute(
            "DELETE FROM graded_skus WHERE graded_sku_id = ?", (graded_sku_id,)
        )
        return cur.rowcount > 0
