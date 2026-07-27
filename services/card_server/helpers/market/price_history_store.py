"""Market Price History Store — DB read/write for `market_price_history`.

Holds TCGplayer's weekly "market price" buckets. Independent of the pricing
path. A card's history is refreshed by replacing all of its rows for a source in
one transaction (buckets are stable historical data).
"""

from __future__ import annotations

import sqlite3
from typing import Any

_INSERT_SQL = """
    INSERT INTO market_price_history (
      card_id, condition, finish, specialty_one, source, bucket_date,
      market_price, low_sale_price, high_sale_price, quantity_sold, transaction_count
    ) VALUES (
      :card_id, :condition, :finish, :specialty_one, :source, :bucket_date,
      :market_price, :low_sale_price, :high_sale_price, :quantity_sold, :transaction_count
    )
"""

_FIELDS = (
    "card_id", "condition", "finish", "specialty_one", "source", "bucket_date",
    "market_price", "low_sale_price", "high_sale_price", "quantity_sold", "transaction_count",
)


def _params(row: dict[str, Any], source: str) -> dict[str, Any]:
    out = {k: row.get(k) for k in _FIELDS}
    out["source"] = row.get("source") or source
    # NOT NULL with a default doesn't cover an explicit None (see sales_store).
    out["specialty_one"] = row.get("specialty_one") or "None"
    return out


class MarketPriceStore:
    def __init__(self, db: sqlite3.Connection):
        self.db = db

    def replace_card(self, card_id: str, rows: list[dict[str, Any]], source: str = "tcgplayer") -> int:
        """Replace all market-price rows for (card_id, source). Skips empty pulls."""
        if not rows:
            return 0

        self.db.execute("BEGIN")
        try:
            self.db.execute(
                "DELETE FROM market_price_history WHERE card_id = ? AND source = ?",
                (card_id, source),
            )
            self.db.executemany(_INSERT_SQL, [_params(r, source) for r in rows])
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        return len(rows)

    def get_for_card(self, card_id: str, source: str = "tcgplayer") -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM market_price_history WHERE card_id = ? AND source = ? ORDER BY bucket_date",
            (card_id, source),
        ).fetchall()
        return [dict(r) for r in rows]

    def count(self) -> int:
        return self.db.execute("SELECT COUNT(*) as c FROM market_price_history").fetchone()["c"]

    def count_for_card(self, card_id: str, source: str = "tcgplayer") -> int:
        return self.db.execute(
            "SELECT COUNT(*) as c FROM market_price_history WHERE card_id = ? AND source = ?",
            (card_id, source),
        ).fetchone()["c"]
