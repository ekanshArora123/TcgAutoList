"""Prices CRUD — pure DB access for the `prices` table. Ported from prices.ts."""

from __future__ import annotations

import sqlite3
from typing import Any, Optional

_INSERT_SQL = """
    INSERT INTO prices (sku_id, calculation_date, estimated_price, estimated_liquid_value,
      confidence_percent, manual_check_necessary, manually_checked, algorithm_version,
      estimated_low_price, estimated_high_price, estimated_low_price_liquid, estimated_high_price_liquid,
      reasoning)
    VALUES (:sku_id, :calculation_date, :estimated_price, :estimated_liquid_value,
      :confidence_percent, :manual_check_necessary, :manually_checked, :algorithm_version,
      :estimated_low_price, :estimated_high_price, :estimated_low_price_liquid, :estimated_high_price_liquid,
      :reasoning)
"""

_UPSERT_SQL = _INSERT_SQL + """
    ON CONFLICT(sku_id, calculation_date) DO UPDATE SET
      estimated_price = excluded.estimated_price,
      estimated_liquid_value = excluded.estimated_liquid_value,
      confidence_percent = excluded.confidence_percent,
      manual_check_necessary = excluded.manual_check_necessary,
      manually_checked = excluded.manually_checked,
      algorithm_version = excluded.algorithm_version,
      estimated_low_price = excluded.estimated_low_price,
      estimated_high_price = excluded.estimated_high_price,
      estimated_low_price_liquid = excluded.estimated_low_price_liquid,
      estimated_high_price_liquid = excluded.estimated_high_price_liquid,
      reasoning = excluded.reasoning
"""

_UPDATE_CALC_DATE_SQL = """
    UPDATE skus SET latest_calc_date = :date
    WHERE sku_id = :sku_id AND (latest_calc_date IS NULL OR latest_calc_date < :date)
"""

_PRICE_FIELDS = (
    "sku_id", "calculation_date", "estimated_price", "estimated_liquid_value",
    "confidence_percent", "manual_check_necessary", "manually_checked",
    "algorithm_version", "estimated_low_price", "estimated_high_price",
    "estimated_low_price_liquid", "estimated_high_price_liquid", "reasoning",
)


def _to_db_params(input: dict[str, Any]) -> dict[str, Any]:
    row = {k: input.get(k) for k in _PRICE_FIELDS}
    row["manual_check_necessary"] = 1 if input.get("manual_check_necessary") else 0
    row["manually_checked"] = 1 if input.get("manually_checked") else 0
    return row


class PricesHelper:
    def __init__(self, db: sqlite3.Connection):
        self.db = db

    def create(self, input: dict[str, Any]) -> Optional[dict]:
        self.db.execute(_INSERT_SQL, _to_db_params(input))
        self.db.execute(
            _UPDATE_CALC_DATE_SQL, {"sku_id": input["sku_id"], "date": input["calculation_date"]}
        )
        return self.get_latest(input["sku_id"])

    def upsert(self, input: dict[str, Any]) -> Optional[dict]:
        self.db.execute(_UPSERT_SQL, _to_db_params(input))
        self.db.execute(
            _UPDATE_CALC_DATE_SQL, {"sku_id": input["sku_id"], "date": input["calculation_date"]}
        )
        return self.get_latest(input["sku_id"])

    def bulk_upsert(self, prices: list[dict[str, Any]]) -> int:
        affected: dict[int, str] = {}
        count = 0
        self.db.execute("BEGIN")
        try:
            for price in prices:
                self.db.execute(_UPSERT_SQL, _to_db_params(price))
                existing = affected.get(price["sku_id"])
                if existing is None or price["calculation_date"] > existing:
                    affected[price["sku_id"]] = price["calculation_date"]
                count += 1
            for sku_id, date in affected.items():
                self.db.execute(_UPDATE_CALC_DATE_SQL, {"sku_id": sku_id, "date": date})
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        return count

    def get_latest(self, sku_id: int) -> Optional[dict]:
        row = self.db.execute(
            "SELECT * FROM prices WHERE sku_id = ? ORDER BY calculation_date DESC LIMIT 1", (sku_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_latest_for_inventory_item(self, inventory_id: int) -> Optional[dict]:
        row = self.db.execute(
            """
            SELECT p.* FROM prices p
            JOIN inventory i ON COALESCE(i.pricing_sku_id, i.sku_id) = p.sku_id
            JOIN skus s ON COALESCE(i.pricing_sku_id, i.sku_id) = s.sku_id
            WHERE i.inventory_id = ? AND p.calculation_date = s.latest_calc_date
            """,
            (inventory_id,),
        ).fetchone()
        return dict(row) if row else None

    def get_history(self, sku_id: int, limit: int = 50, offset: int = 0) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM prices WHERE sku_id = ? ORDER BY calculation_date DESC LIMIT ? OFFSET ?",
            (sku_id, limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]

    def search(self, filters: dict[str, Any]) -> list[dict]:
        conditions: list[str] = []
        params: dict[str, Any] = {}

        if filters.get("sku_id") is not None:
            conditions.append("p.sku_id = :sku_id")
            params["sku_id"] = filters["sku_id"]
        if filters.get("card_id"):
            conditions.append("s.card_id = :card_id")
            params["card_id"] = filters["card_id"]
        if filters.get("date_from"):
            conditions.append("p.calculation_date >= :date_from")
            params["date_from"] = filters["date_from"]
        if filters.get("date_to"):
            conditions.append("p.calculation_date <= :date_to")
            params["date_to"] = filters["date_to"]
        if filters.get("manual_check_only"):
            conditions.append("p.manual_check_necessary = 1 AND p.manually_checked = 0")

        needs_join = bool(filters.get("card_id"))
        from_clause = "prices p JOIN skus s ON p.sku_id = s.sku_id" if needs_join else "prices p"
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params["limit"] = filters.get("limit", 50)
        params["offset"] = filters.get("offset", 0)

        rows = self.db.execute(
            f"SELECT p.* FROM {from_clause} {where} ORDER BY p.calculation_date DESC LIMIT :limit OFFSET :offset",
            params,
        ).fetchall()
        return [dict(r) for r in rows]

    def get_pending_manual_checks(self) -> list[dict]:
        rows = self.db.execute(
            """
            SELECT p.*, c.card_name, s.condition
            FROM prices p
            JOIN skus s ON p.sku_id = s.sku_id
            JOIN cards c ON s.card_id = c.id
            WHERE p.manual_check_necessary = 1 AND p.manually_checked = 0
            ORDER BY p.calculation_date DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]

    def get_prices_by_card_across_conditions(self, card_id: str) -> list[dict]:
        rows = self.db.execute(
            """
            SELECT s.condition, s.finish, p.estimated_price, p.estimated_liquid_value
            FROM skus s
            JOIN prices p ON s.sku_id = p.sku_id AND p.calculation_date = s.latest_calc_date
            WHERE s.card_id = ?
            ORDER BY p.estimated_price DESC
            """,
            (card_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def mark_as_checked(self, sku_id: int, calculation_date: str) -> bool:
        cur = self.db.execute(
            "UPDATE prices SET manually_checked = 1 WHERE sku_id = :sku_id AND calculation_date = :calculation_date",
            {"sku_id": sku_id, "calculation_date": calculation_date},
        )
        return cur.rowcount > 0

    def delete_for_sku(self, sku_id: int) -> int:
        return self.db.execute("DELETE FROM prices WHERE sku_id = ?", (sku_id,)).rowcount

    def delete_older_than(self, date: str) -> int:
        return self.db.execute("DELETE FROM prices WHERE calculation_date < ?", (date,)).rowcount
