"""Inventory CRUD — pure DB access for the `inventory` table. Ported from inventory.ts."""

from __future__ import annotations

import sqlite3
from typing import Any, Optional

from .skus import SkusHelper

# Canonical "owned card with its current price" join. The price row is the one
# whose calculation_date matches the SKU's latest_calc_date, priced against
# pricing_sku_id when set (else the card's own SKU). Single source of truth —
# every inventory+price read (CRUD, reporting, analytics) builds on this FROM.
INV_SKU_CARD_PRICE_FROM = """
    FROM inventory i
    JOIN skus s ON i.sku_id = s.sku_id
    JOIN cards c ON s.card_id = c.id
    LEFT JOIN prices p ON COALESCE(i.pricing_sku_id, i.sku_id) = p.sku_id
      AND p.calculation_date = s.latest_calc_date
"""

_DETAIL_SELECT = """
    SELECT
      i.*,
      s.condition, s.finish, s.card_id, s.specialty_one, s.specialty_two,
      c.card_name, c.set_name, c.rarity, c.card_number,
      p.estimated_price
""" + INV_SKU_CARD_PRICE_FROM

_UPDATABLE = (
    "sku_id", "pricing_sku_id", "qty", "tags", "status",
    "front_photo_path", "back_photo_path", "ebay_listing_id", "listed_at",
)


class InventoryHelper:
    def __init__(self, db: sqlite3.Connection):
        self.db = db
        self.skus = SkusHelper(db)

    def create(self, input: dict[str, Any]) -> Optional[dict]:
        cur = self.db.execute(
            "INSERT INTO inventory (sku_id, pricing_sku_id, qty, tags, status) "
            "VALUES (:sku_id, :pricing_sku_id, :qty, :tags, :status)",
            {
                "sku_id": input["sku_id"],
                "pricing_sku_id": input.get("pricing_sku_id"),
                "qty": input.get("qty", 1) or 1,
                "tags": input.get("tags"),
                "status": input.get("status") or "unlisted",
            },
        )
        self.skus.recalculate_qty(input["sku_id"])
        return self.get_by_id(cur.lastrowid)

    def bulk_create(self, items: list[dict[str, Any]]) -> int:
        affected: set[int] = set()
        count = 0
        self.db.execute("BEGIN")
        try:
            for item in items:
                self.db.execute(
                    "INSERT INTO inventory (sku_id, pricing_sku_id, qty, tags, status) "
                    "VALUES (:sku_id, :pricing_sku_id, :qty, :tags, :status)",
                    {
                        "sku_id": item["sku_id"],
                        "pricing_sku_id": item.get("pricing_sku_id"),
                        "qty": item.get("qty", 1) or 1,
                        "tags": item.get("tags"),
                        "status": item.get("status") or "unlisted",
                    },
                )
                affected.add(item["sku_id"])
                count += 1
            for sku_id in affected:
                self.skus.recalculate_qty(sku_id)
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        return count

    def get_by_id(self, inventory_id: int) -> Optional[dict]:
        row = self.db.execute(
            "SELECT * FROM inventory WHERE inventory_id = ?", (inventory_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_detail_by_id(self, inventory_id: int) -> Optional[dict]:
        row = self.db.execute(
            _DETAIL_SELECT + " WHERE i.inventory_id = ?", (inventory_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_next_unlisted(self) -> Optional[dict]:
        row = self.db.execute(
            _DETAIL_SELECT + " WHERE i.status = 'unlisted' ORDER BY i.created_at ASC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    def search(self, filters: dict[str, Any]) -> list[dict]:
        conditions: list[str] = []
        params: dict[str, Any] = {}

        if filters.get("sku_id") is not None:
            conditions.append("i.sku_id = :sku_id")
            params["sku_id"] = filters["sku_id"]
        if filters.get("status"):
            conditions.append("i.status = :status")
            params["status"] = filters["status"]
        if filters.get("tags_contain"):
            conditions.append("i.tags LIKE :tags_contain")
            params["tags_contain"] = f"%{filters['tags_contain']}%"
        if filters.get("card_id"):
            conditions.append("s.card_id = :card_id")
            params["card_id"] = filters["card_id"]

        needs_join = bool(filters.get("card_id"))
        from_clause = "inventory i JOIN skus s ON i.sku_id = s.sku_id" if needs_join else "inventory i"
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params["limit"] = filters.get("limit", 50)
        params["offset"] = filters.get("offset", 0)

        rows = self.db.execute(
            f"SELECT i.* FROM {from_clause} {where} ORDER BY i.created_at ASC LIMIT :limit OFFSET :offset",
            params,
        ).fetchall()
        return [dict(r) for r in rows]

    def search_with_details(self, filters: dict[str, Any]) -> list[dict]:
        conditions: list[str] = []
        params: dict[str, Any] = {}

        if filters.get("status"):
            conditions.append("i.status = :status")
            params["status"] = filters["status"]
        if filters.get("card_id"):
            conditions.append("s.card_id = :card_id")
            params["card_id"] = filters["card_id"]

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params["limit"] = filters.get("limit", 50)
        params["offset"] = filters.get("offset", 0)

        rows = self.db.execute(
            _DETAIL_SELECT + f" {where} ORDER BY i.created_at ASC LIMIT :limit OFFSET :offset",
            params,
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_statuses(self) -> list[str]:
        rows = self.db.execute(
            "SELECT DISTINCT status FROM inventory WHERE status IS NOT NULL ORDER BY status"
        ).fetchall()
        return [r["status"] for r in rows]

    def get_stats(self) -> dict[str, Any]:
        status_rows = self.db.execute(
            "SELECT status, COUNT(*) as count FROM inventory GROUP BY status"
        ).fetchall()

        by_status: dict[str, int] = {}
        total = 0
        for row in status_rows:
            by_status[row["status"]] = row["count"]
            total += row["count"]

        value_row = self.db.execute(
            "SELECT SUM(p.estimated_price * i.qty) as total_value" + INV_SKU_CARD_PRICE_FROM
        ).fetchone()

        return {
            "total": total,
            "by_status": by_status,
            "total_estimated_value": value_row["total_value"],
        }

    def update(self, input: dict[str, Any]) -> Optional[dict]:
        existing = self.get_by_id(input["inventory_id"])
        if not existing:
            return None

        fields: list[str] = []
        params: dict[str, Any] = {"inventory_id": input["inventory_id"]}
        old_sku_id = existing["sku_id"]

        for field in _UPDATABLE:
            if input.get(field) is not None:
                fields.append(f"{field} = :{field}")
                params[field] = input[field]

        if not fields:
            return existing

        self.db.execute(
            f"UPDATE inventory SET {', '.join(fields)} WHERE inventory_id = :inventory_id", params
        )

        if input.get("sku_id") is not None and input["sku_id"] != old_sku_id:
            self.skus.recalculate_qty(old_sku_id)
            self.skus.recalculate_qty(input["sku_id"])

        return self.get_by_id(input["inventory_id"])

    def mark_as_listed(self, inventory_id: int, ebay_listing_id: str) -> Optional[dict]:
        self.db.execute(
            "UPDATE inventory SET status = 'listed', ebay_listing_id = :ebay_listing_id, "
            "listed_at = datetime('now') WHERE inventory_id = :inventory_id",
            {"inventory_id": inventory_id, "ebay_listing_id": ebay_listing_id},
        )
        return self.get_by_id(inventory_id)

    def mark_as_sold(self, inventory_id: int) -> Optional[dict]:
        self.db.execute("UPDATE inventory SET status = 'sold' WHERE inventory_id = ?", (inventory_id,))
        return self.get_by_id(inventory_id)

    def update_status(self, inventory_id: int, status: str) -> Optional[dict]:
        self.db.execute(
            "UPDATE inventory SET status = :status WHERE inventory_id = :inventory_id",
            {"inventory_id": inventory_id, "status": status},
        )
        return self.get_by_id(inventory_id)

    def set_photos(self, inventory_id: int, front_photo_path: str, back_photo_path: str) -> Optional[dict]:
        self.db.execute(
            "UPDATE inventory SET front_photo_path = :front_photo_path, "
            "back_photo_path = :back_photo_path WHERE inventory_id = :inventory_id",
            {
                "inventory_id": inventory_id,
                "front_photo_path": front_photo_path,
                "back_photo_path": back_photo_path,
            },
        )
        return self.get_by_id(inventory_id)

    def set_pricing_sku(self, inventory_id: int, pricing_sku_id: Optional[int]) -> Optional[dict]:
        self.db.execute(
            "UPDATE inventory SET pricing_sku_id = :pricing_sku_id WHERE inventory_id = :inventory_id",
            {"inventory_id": inventory_id, "pricing_sku_id": pricing_sku_id},
        )
        return self.get_by_id(inventory_id)

    def add_tag(self, inventory_id: int, tag: str) -> Optional[dict]:
        item = self.get_by_id(inventory_id)
        if not item:
            return None

        tags = [t.strip() for t in item["tags"].split(",")] if item.get("tags") else []
        if tag in tags:
            return item

        tags.append(tag)
        self.db.execute(
            "UPDATE inventory SET tags = ? WHERE inventory_id = ?", (",".join(tags), inventory_id)
        )
        return self.get_by_id(inventory_id)

    def remove_tag(self, inventory_id: int, tag: str) -> Optional[dict]:
        item = self.get_by_id(inventory_id)
        if not item:
            return None
        if not item.get("tags"):
            return item

        tags = [t.strip() for t in item["tags"].split(",") if t.strip() != tag]
        new_tags = ",".join(tags) if tags else None
        self.db.execute(
            "UPDATE inventory SET tags = ? WHERE inventory_id = ?", (new_tags, inventory_id)
        )
        return self.get_by_id(inventory_id)

    def delete(self, inventory_id: int) -> bool:
        item = self.get_by_id(inventory_id)
        if not item:
            return False

        cur = self.db.execute("DELETE FROM inventory WHERE inventory_id = ?", (inventory_id,))
        if cur.rowcount > 0:
            self.skus.recalculate_qty(item["sku_id"])
            return True
        return False
