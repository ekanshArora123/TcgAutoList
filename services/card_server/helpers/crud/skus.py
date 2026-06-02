"""SKUs CRUD — pure DB access for the `skus` table. Ported from skus.ts."""

from __future__ import annotations

import sqlite3
from typing import Any, Optional

_INSERT_SQL = """
    INSERT INTO skus (card_id, condition, finish, specialty_one, specialty_two, qty)
    VALUES (:card_id, :condition, :finish, :specialty_one, :specialty_two, :qty)
"""

_GET_BY_COMPOSITE_SQL = """
    SELECT * FROM skus
    WHERE card_id = :card_id AND condition = :condition AND finish = :finish
      AND specialty_one = :specialty_one AND specialty_two = :specialty_two
"""

_UPDATABLE = ("condition", "finish", "specialty_one", "specialty_two", "qty", "latest_calc_date")

_SKU_DEFAULTS = {
    "finish": "Regular",
    "specialty_one": "None",
    "specialty_two": "None",
    "qty": 0,
}


def _normalize_sku(params: dict[str, Any]) -> dict[str, Any]:
    row = {
        "card_id": params["card_id"],
        "condition": params["condition"],
        "finish": params.get("finish") or "Regular",
        "specialty_one": params.get("specialty_one") or "None",
        "specialty_two": params.get("specialty_two") or "None",
        "qty": params.get("qty", 0) or 0,
    }
    return row


class SkusHelper:
    def __init__(self, db: sqlite3.Connection):
        self.db = db

    def create(self, input: dict[str, Any]) -> Optional[dict]:
        cur = self.db.execute(_INSERT_SQL, _normalize_sku(input))
        return self.get_by_id(cur.lastrowid)

    def get_or_create(self, input: dict[str, Any]) -> dict:
        norm = _normalize_sku(input)
        existing = self.db.execute(_GET_BY_COMPOSITE_SQL, norm).fetchone()
        if existing:
            return dict(existing)
        return self.create(input)

    def bulk_import(self, skus: list[dict[str, Any]]) -> int:
        created = 0
        self.db.execute("BEGIN")
        try:
            for sku in skus:
                norm = _normalize_sku(sku)
                existing = self.db.execute(_GET_BY_COMPOSITE_SQL, norm).fetchone()
                if not existing:
                    self.db.execute(_INSERT_SQL, norm)
                    created += 1
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        return created

    def get_by_id(self, sku_id: int) -> Optional[dict]:
        row = self.db.execute("SELECT * FROM skus WHERE sku_id = ?", (sku_id,)).fetchone()
        return dict(row) if row else None

    def find_by_composite_key(
        self,
        card_id: str,
        condition: str,
        finish: str,
        specialty_one: str,
        specialty_two: str,
    ) -> Optional[dict]:
        row = self.db.execute(
            _GET_BY_COMPOSITE_SQL,
            {
                "card_id": card_id,
                "condition": condition,
                "finish": finish,
                "specialty_one": specialty_one,
                "specialty_two": specialty_two,
            },
        ).fetchone()
        return dict(row) if row else None

    def get_by_card_id(self, card_id: str) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM skus WHERE card_id = ? ORDER BY condition, finish", (card_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def search(self, filters: dict[str, Any]) -> list[dict]:
        conditions: list[str] = []
        params: dict[str, Any] = {}

        if filters.get("card_id"):
            conditions.append("card_id = :card_id")
            params["card_id"] = filters["card_id"]
        if filters.get("condition"):
            conditions.append("condition = :condition")
            params["condition"] = filters["condition"]
        if filters.get("finish"):
            conditions.append("finish = :finish")
            params["finish"] = filters["finish"]
        if filters.get("specialty_one"):
            conditions.append("specialty_one = :specialty_one")
            params["specialty_one"] = filters["specialty_one"]

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params["limit"] = filters.get("limit", 50)
        params["offset"] = filters.get("offset", 0)

        rows = self.db.execute(
            f"SELECT * FROM skus {where} ORDER BY card_id, condition LIMIT :limit OFFSET :offset",
            params,
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_conditions(self) -> list[str]:
        rows = self.db.execute("SELECT DISTINCT condition FROM skus ORDER BY condition").fetchall()
        return [r["condition"] for r in rows]

    def get_all_specialties(self) -> list[str]:
        rows = self.db.execute(
            "SELECT DISTINCT specialty_one FROM skus WHERE specialty_one != 'None' ORDER BY specialty_one"
        ).fetchall()
        return [r["specialty_one"] for r in rows]

    def update(self, input: dict[str, Any]) -> Optional[dict]:
        existing = self.get_by_id(input["sku_id"])
        if not existing:
            return None

        fields: list[str] = []
        params: dict[str, Any] = {"sku_id": input["sku_id"]}
        for field in _UPDATABLE:
            if input.get(field) is not None:
                fields.append(f"{field} = :{field}")
                params[field] = input[field]

        if not fields:
            return existing

        self.db.execute(f"UPDATE skus SET {', '.join(fields)} WHERE sku_id = :sku_id", params)
        return self.get_by_id(input["sku_id"])

    def recalculate_qty(self, sku_id: int) -> None:
        self.db.execute(
            "UPDATE skus SET qty = (SELECT COALESCE(SUM(qty), 0) FROM inventory WHERE sku_id = :sku_id) WHERE sku_id = :sku_id",
            {"sku_id": sku_id},
        )

    def delete(self, sku_id: int) -> bool:
        cur = self.db.execute("DELETE FROM skus WHERE sku_id = ?", (sku_id,))
        return cur.rowcount > 0
