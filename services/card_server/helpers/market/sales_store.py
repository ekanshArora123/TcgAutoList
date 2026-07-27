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
      card_id, condition, finish, specialty_one, source,
      order_date, purchase_price, shipping_price, quantity, has_image
    ) VALUES (
      :card_id, :condition, :finish, :specialty_one, :source,
      :order_date, :purchase_price, :shipping_price, :quantity, :has_image
    )
"""

_FIELDS = (
    "card_id", "condition", "finish", "specialty_one", "source",
    "order_date", "purchase_price", "shipping_price", "quantity", "has_image",
)


def _params(sale: dict[str, Any], source: str) -> dict[str, Any]:
    row = {k: sale.get(k) for k in _FIELDS}
    row["source"] = sale.get("source") or source
    # NOT NULL with a default only helps when the column is omitted; an explicit
    # None still fails, and callers predating the column pass none at all.
    row["specialty_one"] = sale.get("specialty_one") or "None"
    row["quantity"] = sale.get("quantity") or 1
    row["shipping_price"] = sale.get("shipping_price") or 0
    row["has_image"] = 1 if sale.get("has_image") else 0
    return row


class SalesStore:
    def __init__(self, db: sqlite3.Connection):
        self.db = db

    def replace_card(self, card_id: str, sales: list[dict[str, Any]], source: str = "tcgplayer") -> int:
        """Replace all stored sales for (card_id, source) with a fresh pull.

        Skipped entirely when `sales` is empty so an API hiccup (or a card with
        no recent sales) can't wipe previously-collected history. Atomic.

        TODO(sales-idempotency): this wholesale replace re-downloads the full
        ~1yr window on every refresh (~14 rate-limited requests/card), which is
        the main driver of collect_sales' rate-limiting pain. Past sales are
        immutable, so a refresh only needs sales newer than the newest stored
        `order_date`: add an incremental `append_new_since(card_id, source)` that
        fetches just the new tail and appends it (with a small overlap window +
        natural-key dedup, since TCGplayer sales have no stable id). Keep this
        full replace as the backfill/repair path. See collect_sales.py.
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
