"""Graded inventory CRUD — pure DB access for the `graded_inventory` table.

The graded analog of inventory.py: physical slabs you own, each referencing a
graded_sku and carrying its cert id. Parallel to the raw chain — the raw
`inventory` table/helper is untouched. Its own canonical "owned slab with its
current price" join, mirroring INV_SKU_CARD_PRICE_FROM.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Optional

from .graded_skus import GradedSkusHelper

# Canonical "owned graded slab with its current price" join (graded analog of
# INV_SKU_CARD_PRICE_FROM). The price is the graded_prices row whose date matches
# the graded SKU's latest_calc_date.
GRADED_INV_FROM = """
    FROM graded_inventory i
    JOIN graded_skus g ON i.graded_sku_id = g.graded_sku_id
    JOIN cards c ON g.card_id = c.id
    LEFT JOIN graded_prices p ON i.graded_sku_id = p.graded_sku_id
      AND p.calculation_date = g.latest_calc_date
"""

_DETAIL_SELECT = """
    SELECT
      i.*,
      g.card_id, g.finish, g.specialty_one, g.grading_company, g.grade,
      c.card_name, c.set_name, c.rarity, c.card_number,
      p.estimated_price
""" + GRADED_INV_FROM

_UPDATABLE = (
    "graded_sku_id", "cert_id", "qty", "tags", "status",
    "front_photo_path", "back_photo_path", "ebay_listing_id", "listed_at",
)

_INSERT_COLS = "graded_sku_id, cert_id, qty, tags, status"
_INSERT_VALS = ":graded_sku_id, :cert_id, :qty, :tags, :status"


def _insert_params(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "graded_sku_id": item["graded_sku_id"],
        "cert_id": item.get("cert_id"),
        "qty": item.get("qty", 1) or 1,
        "tags": item.get("tags"),
        "status": item.get("status") or "unlisted",
    }


class GradedInventoryHelper:
    def __init__(self, db: sqlite3.Connection):
        self.db = db
        self.graded_skus = GradedSkusHelper(db)

    def create(self, input: dict[str, Any]) -> Optional[dict]:
        cur = self.db.execute(
            f"INSERT INTO graded_inventory ({_INSERT_COLS}) VALUES ({_INSERT_VALS})",
            _insert_params(input),
        )
        self.graded_skus.recalculate_qty(input["graded_sku_id"])
        return self.get_by_id(cur.lastrowid)

    def bulk_create(self, items: list[dict[str, Any]]) -> int:
        affected: set[int] = set()
        count = 0
        self.db.execute("BEGIN")
        try:
            for item in items:
                self.db.execute(
                    f"INSERT INTO graded_inventory ({_INSERT_COLS}) VALUES ({_INSERT_VALS})",
                    _insert_params(item),
                )
                affected.add(item["graded_sku_id"])
                count += 1
            for gid in affected:
                self.graded_skus.recalculate_qty(gid)
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        return count

    def get_by_id(self, graded_inventory_id: int) -> Optional[dict]:
        row = self.db.execute(
            "SELECT * FROM graded_inventory WHERE graded_inventory_id = ?",
            (graded_inventory_id,),
        ).fetchone()
        return dict(row) if row else None

    def get_detail_by_id(self, graded_inventory_id: int) -> Optional[dict]:
        row = self.db.execute(
            _DETAIL_SELECT + " WHERE i.graded_inventory_id = ?", (graded_inventory_id,)
        ).fetchone()
        return dict(row) if row else None

    def search_with_details(self, filters: dict[str, Any]) -> list[dict]:
        conditions: list[str] = []
        params: dict[str, Any] = {}

        if filters.get("status"):
            conditions.append("i.status = :status")
            params["status"] = filters["status"]
        if filters.get("card_id"):
            conditions.append("g.card_id = :card_id")
            params["card_id"] = filters["card_id"]
        if filters.get("grading_company"):
            conditions.append("g.grading_company = :grading_company")
            params["grading_company"] = filters["grading_company"]

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params["limit"] = filters.get("limit", 50)
        params["offset"] = filters.get("offset", 0)

        rows = self.db.execute(
            _DETAIL_SELECT + f" {where} ORDER BY i.created_at ASC LIMIT :limit OFFSET :offset",
            params,
        ).fetchall()
        return [dict(r) for r in rows]

    def get_stats(self) -> dict[str, Any]:
        total = self.db.execute("SELECT COUNT(*) c FROM graded_inventory").fetchone()["c"]
        value_row = self.db.execute(
            "SELECT SUM(p.estimated_price * i.qty) as total_value" + GRADED_INV_FROM
        ).fetchone()
        return {"total": total, "total_estimated_value": value_row["total_value"]}

    def update(self, input: dict[str, Any]) -> Optional[dict]:
        existing = self.get_by_id(input["graded_inventory_id"])
        if not existing:
            return None

        fields: list[str] = []
        params: dict[str, Any] = {"graded_inventory_id": input["graded_inventory_id"]}
        for field in _UPDATABLE:
            if input.get(field) is not None:
                fields.append(f"{field} = :{field}")
                params[field] = input[field]

        if not fields:
            return existing

        self.db.execute(
            f"UPDATE graded_inventory SET {', '.join(fields)} "
            "WHERE graded_inventory_id = :graded_inventory_id",
            params,
        )
        self.graded_skus.recalculate_qty(existing["graded_sku_id"])
        return self.get_by_id(input["graded_inventory_id"])

    def delete(self, graded_inventory_id: int) -> bool:
        item = self.get_by_id(graded_inventory_id)
        if not item:
            return False
        cur = self.db.execute(
            "DELETE FROM graded_inventory WHERE graded_inventory_id = ?", (graded_inventory_id,)
        )
        if cur.rowcount > 0:
            self.graded_skus.recalculate_qty(item["graded_sku_id"])
            return True
        return False
