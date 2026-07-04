"""Graded prices CRUD — pure DB access for the `graded_prices` table.

Mirror of prices.py, keyed on graded_sku_id. Graded pricing is manual today
(TCGplayer has no graded data); this stores manual / future estimates and bumps
graded_skus.latest_calc_date so the latest price is easy to find.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Optional

_INSERT_SQL = """
    INSERT INTO graded_prices (graded_sku_id, calculation_date, estimated_price, estimated_liquid_value,
      confidence_percent, manual_check_necessary, manually_checked, algorithm_version,
      estimated_low_price, estimated_high_price, estimated_low_price_liquid, estimated_high_price_liquid)
    VALUES (:graded_sku_id, :calculation_date, :estimated_price, :estimated_liquid_value,
      :confidence_percent, :manual_check_necessary, :manually_checked, :algorithm_version,
      :estimated_low_price, :estimated_high_price, :estimated_low_price_liquid, :estimated_high_price_liquid)
"""

_UPSERT_SQL = _INSERT_SQL + """
    ON CONFLICT(graded_sku_id, calculation_date) DO UPDATE SET
      estimated_price = excluded.estimated_price,
      estimated_liquid_value = excluded.estimated_liquid_value,
      confidence_percent = excluded.confidence_percent,
      manual_check_necessary = excluded.manual_check_necessary,
      manually_checked = excluded.manually_checked,
      algorithm_version = excluded.algorithm_version,
      estimated_low_price = excluded.estimated_low_price,
      estimated_high_price = excluded.estimated_high_price,
      estimated_low_price_liquid = excluded.estimated_low_price_liquid,
      estimated_high_price_liquid = excluded.estimated_high_price_liquid
"""

_UPDATE_CALC_DATE_SQL = """
    UPDATE graded_skus SET latest_calc_date = :date
    WHERE graded_sku_id = :graded_sku_id AND (latest_calc_date IS NULL OR latest_calc_date < :date)
"""

_PRICE_FIELDS = (
    "graded_sku_id", "calculation_date", "estimated_price", "estimated_liquid_value",
    "confidence_percent", "manual_check_necessary", "manually_checked",
    "algorithm_version", "estimated_low_price", "estimated_high_price",
    "estimated_low_price_liquid", "estimated_high_price_liquid",
)


def _to_db_params(input: dict[str, Any]) -> dict[str, Any]:
    row = {k: input.get(k) for k in _PRICE_FIELDS}
    row["manual_check_necessary"] = 1 if input.get("manual_check_necessary") else 0
    row["manually_checked"] = 1 if input.get("manually_checked") else 0
    return row


class GradedPricesHelper:
    def __init__(self, db: sqlite3.Connection):
        self.db = db

    def create(self, input: dict[str, Any]) -> Optional[dict]:
        self.db.execute(_INSERT_SQL, _to_db_params(input))
        self.db.execute(
            _UPDATE_CALC_DATE_SQL,
            {"graded_sku_id": input["graded_sku_id"], "date": input["calculation_date"]},
        )
        return self.get_latest(input["graded_sku_id"])

    def upsert(self, input: dict[str, Any]) -> Optional[dict]:
        self.db.execute(_UPSERT_SQL, _to_db_params(input))
        self.db.execute(
            _UPDATE_CALC_DATE_SQL,
            {"graded_sku_id": input["graded_sku_id"], "date": input["calculation_date"]},
        )
        return self.get_latest(input["graded_sku_id"])

    def get_latest(self, graded_sku_id: int) -> Optional[dict]:
        row = self.db.execute(
            "SELECT * FROM graded_prices WHERE graded_sku_id = ? "
            "ORDER BY calculation_date DESC LIMIT 1",
            (graded_sku_id,),
        ).fetchone()
        return dict(row) if row else None

    def get_latest_for_inventory_item(self, graded_inventory_id: int) -> Optional[dict]:
        row = self.db.execute(
            """
            SELECT p.* FROM graded_prices p
            JOIN graded_inventory i ON i.graded_sku_id = p.graded_sku_id
            JOIN graded_skus g ON i.graded_sku_id = g.graded_sku_id
            WHERE i.graded_inventory_id = ? AND p.calculation_date = g.latest_calc_date
            """,
            (graded_inventory_id,),
        ).fetchone()
        return dict(row) if row else None

    def get_history(self, graded_sku_id: int, limit: int = 50, offset: int = 0) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM graded_prices WHERE graded_sku_id = ? "
            "ORDER BY calculation_date DESC LIMIT ? OFFSET ?",
            (graded_sku_id, limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_pending_manual_checks(self) -> list[dict]:
        rows = self.db.execute(
            """
            SELECT p.*, c.card_name, g.grading_company, g.grade
            FROM graded_prices p
            JOIN graded_skus g ON p.graded_sku_id = g.graded_sku_id
            JOIN cards c ON g.card_id = c.id
            WHERE p.manual_check_necessary = 1 AND p.manually_checked = 0
            ORDER BY p.calculation_date DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]

    def mark_as_checked(self, graded_sku_id: int, calculation_date: str) -> bool:
        cur = self.db.execute(
            "UPDATE graded_prices SET manually_checked = 1 "
            "WHERE graded_sku_id = :graded_sku_id AND calculation_date = :calculation_date",
            {"graded_sku_id": graded_sku_id, "calculation_date": calculation_date},
        )
        return cur.rowcount > 0

    def delete_for_graded_sku(self, graded_sku_id: int) -> int:
        return self.db.execute(
            "DELETE FROM graded_prices WHERE graded_sku_id = ?", (graded_sku_id,)
        ).rowcount
