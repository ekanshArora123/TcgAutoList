"""Cards CRUD — pure DB access for the `cards` table. Ported from cards.ts."""

from __future__ import annotations

import sqlite3
from typing import Any, Optional

_UPDATABLE = (
    "card_name", "set_name", "product_line", "card_type", "visual_layout",
    "rarity", "card_number", "product_type", "era", "set_type",
)

_INSERT_SQL = """
    INSERT INTO cards (id, card_name, set_name, product_line, card_type, visual_layout, rarity, card_number, product_type, era, set_type)
    VALUES (:id, :card_name, :set_name, :product_line, :card_type, :visual_layout, :rarity, :card_number, :product_type, :era, :set_type)
"""

_UPSERT_SQL = """
    INSERT INTO cards (id, card_name, set_name, product_line, card_type, visual_layout, rarity, card_number, product_type, era, set_type)
    VALUES (:id, :card_name, :set_name, :product_line, :card_type, :visual_layout, :rarity, :card_number, :product_type, :era, :set_type)
    ON CONFLICT(id) DO UPDATE SET
        card_name = excluded.card_name,
        set_name = excluded.set_name,
        product_line = excluded.product_line,
        card_type = excluded.card_type,
        visual_layout = excluded.visual_layout,
        rarity = excluded.rarity,
        card_number = excluded.card_number,
        product_type = excluded.product_type,
        era = excluded.era,
        set_type = excluded.set_type
"""

_CARD_FIELDS = (
    "id", "card_name", "set_name", "product_line", "card_type", "visual_layout",
    "rarity", "card_number", "product_type", "era", "set_type",
)


def _normalize_card(params: dict[str, Any]) -> dict[str, Any]:
    """Fill defaults so named-param SQL always has every key."""
    row = {k: params.get(k) for k in _CARD_FIELDS}
    if row.get("product_line") is None:
        row["product_line"] = "Pokemon"
    return row


class CardsHelper:
    def __init__(self, db: sqlite3.Connection):
        self.db = db

    def create(self, input: dict[str, Any]) -> Optional[dict]:
        self.db.execute(_INSERT_SQL, _normalize_card(input))
        return self.get_by_id(input["id"])

    def upsert(self, input: dict[str, Any]) -> Optional[dict]:
        self.db.execute(_UPSERT_SQL, _normalize_card(input))
        return self.get_by_id(input["id"])

    def bulk_upsert(self, cards: list[dict[str, Any]]) -> int:
        count = 0
        self.db.execute("BEGIN")
        try:
            for card in cards:
                self.db.execute(_UPSERT_SQL, _normalize_card(card))
                count += 1
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        return count

    def get_by_id(self, id: str) -> Optional[dict]:
        row = self.db.execute("SELECT * FROM cards WHERE id = ?", (id,)).fetchone()
        return dict(row) if row else None

    def search(self, filters: dict[str, Any]) -> list[dict]:
        conditions: list[str] = []
        params: dict[str, Any] = {}

        if filters.get("query"):
            conditions.append("card_name LIKE :query")
            params["query"] = f"%{filters['query']}%"
        if filters.get("set_name"):
            conditions.append("set_name = :set_name")
            params["set_name"] = filters["set_name"]
        if filters.get("rarity"):
            conditions.append("rarity = :rarity")
            params["rarity"] = filters["rarity"]
        if filters.get("product_line"):
            conditions.append("product_line = :product_line")
            params["product_line"] = filters["product_line"]

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params["limit"] = filters.get("limit", 50)
        params["offset"] = filters.get("offset", 0)

        rows = self.db.execute(
            f"SELECT * FROM cards {where} ORDER BY card_name LIMIT :limit OFFSET :offset",
            params,
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_set_names(self) -> list[str]:
        rows = self.db.execute(
            "SELECT DISTINCT set_name FROM cards WHERE set_name IS NOT NULL ORDER BY set_name"
        ).fetchall()
        return [r["set_name"] for r in rows]

    def get_all_rarities(self) -> list[str]:
        rows = self.db.execute(
            "SELECT DISTINCT rarity FROM cards WHERE rarity IS NOT NULL ORDER BY rarity"
        ).fetchall()
        return [r["rarity"] for r in rows]

    def get_all_eras(self) -> list[str]:
        rows = self.db.execute(
            "SELECT DISTINCT era FROM cards WHERE era IS NOT NULL ORDER BY era"
        ).fetchall()
        return [r["era"] for r in rows]

    def count(self, product_line: Optional[str] = None) -> int:
        if product_line:
            row = self.db.execute(
                "SELECT COUNT(*) as count FROM cards WHERE product_line = ?", (product_line,)
            ).fetchone()
        else:
            row = self.db.execute("SELECT COUNT(*) as count FROM cards").fetchone()
        return row["count"]

    def update(self, input: dict[str, Any]) -> Optional[dict]:
        existing = self.get_by_id(input["id"])
        if not existing:
            return None

        fields: list[str] = []
        params: dict[str, Any] = {"id": input["id"]}
        for field in _UPDATABLE:
            if input.get(field) is not None:
                fields.append(f"{field} = :{field}")
                params[field] = input[field]

        if not fields:
            return existing

        self.db.execute(f"UPDATE cards SET {', '.join(fields)} WHERE id = :id", params)
        return self.get_by_id(input["id"])

    def delete(self, id: str) -> bool:
        cur = self.db.execute("DELETE FROM cards WHERE id = ?", (id,))
        return cur.rowcount > 0
