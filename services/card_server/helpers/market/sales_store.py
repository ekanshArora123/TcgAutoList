"""Sales Store — DB read/write for the raw `sales` table.

Holds individual sold-listing rows fetched comprehensively from TCGplayer.
Independent of the pricing path (prices / market_snapshots). A card's history is
refreshed by replacing all of its rows for a source in one transaction, since
TCGplayer sales have no stable id and past sales are immutable.
"""

from __future__ import annotations

import sqlite3
from typing import Any

_INSERT_SQL = """
    INSERT INTO sales (
      card_id, condition, finish, source,
      order_date, purchase_price, shipping_price, quantity
    ) VALUES (
      :card_id, :condition, :finish, :source,
      :order_date, :purchase_price, :shipping_price, :quantity
    )
"""

_FIELDS = (
    "card_id", "condition", "finish", "source",
    "order_date", "purchase_price", "shipping_price", "quantity",
)


def _params(sale: dict[str, Any], source: str) -> dict[str, Any]:
    row = {k: sale.get(k) for k in _FIELDS}
    row["source"] = sale.get("source") or source
    row["quantity"] = sale.get("quantity") or 1
    row["shipping_price"] = sale.get("shipping_price") or 0
    return row


class SalesStore:
    def __init__(self, db: sqlite3.Connection):
        self.db = db

    def replace_card(self, card_id: str, sales: list[dict[str, Any]], source: str = "tcgplayer") -> int:
        """Replace all stored sales for (card_id, source) with a fresh pull.

        Skipped entirely when `sales` is empty so an API hiccup (or a card with
        no recent sales) can't wipe previously-collected history. Atomic.
        """
        if not sales:
            return 0

        self.db.execute("BEGIN")
        try:
            self.db.execute(
                "DELETE FROM sales WHERE card_id = ? AND source = ?", (card_id, source)
            )
            self.db.executemany(_INSERT_SQL, [_params(s, source) for s in sales])
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        return len(sales)

    def get_for_card(self, card_id: str, source: str = "tcgplayer") -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM sales WHERE card_id = ? AND source = ? ORDER BY order_date",
            (card_id, source),
        ).fetchall()
        return [dict(r) for r in rows]

    def count(self) -> int:
        return self.db.execute("SELECT COUNT(*) as c FROM sales").fetchone()["c"]

    def count_for_card(self, card_id: str, source: str = "tcgplayer") -> int:
        return self.db.execute(
            "SELECT COUNT(*) as c FROM sales WHERE card_id = ? AND source = ?",
            (card_id, source),
        ).fetchone()["c"]
