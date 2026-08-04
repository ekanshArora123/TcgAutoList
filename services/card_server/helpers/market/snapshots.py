"""Market Snapshots — DB read/write for the market_snapshots table.

Handles inserting snapshots and querying historical data. Ported from snapshots.ts.
MarketSnapshotRow / SnapshotQuery are plain dicts.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Optional

MarketSnapshotRow = dict
SnapshotQuery = dict

_INSERT_SQL = """
    INSERT INTO market_snapshots (
      card_id, condition, finish, specialty_one, snapshot_date, source,
      listing_count, lowest_listing_price, median_listing_price,
      mean_listing_price, p25_listing_price, p75_listing_price,
      recent_sales_count, avg_sale_price, median_sale_price,
      min_sale_price, max_sale_price, newest_sale_date, oldest_sale_date,
      divergence_sale_price, weighted_sale_price
    ) VALUES (
      :card_id, :condition, :finish, :specialty_one, :snapshot_date, :source,
      :listing_count, :lowest_listing_price, :median_listing_price,
      :mean_listing_price, :p25_listing_price, :p75_listing_price,
      :recent_sales_count, :avg_sale_price, :median_sale_price,
      :min_sale_price, :max_sale_price, :newest_sale_date, :oldest_sale_date,
      :divergence_sale_price, :weighted_sale_price
    ) ON CONFLICT(card_id, condition, finish, specialty_one, snapshot_date, source)
    DO UPDATE SET
      listing_count = excluded.listing_count,
      lowest_listing_price = excluded.lowest_listing_price,
      median_listing_price = excluded.median_listing_price,
      mean_listing_price = excluded.mean_listing_price,
      p25_listing_price = excluded.p25_listing_price,
      p75_listing_price = excluded.p75_listing_price,
      recent_sales_count = excluded.recent_sales_count,
      avg_sale_price = excluded.avg_sale_price,
      median_sale_price = excluded.median_sale_price,
      min_sale_price = excluded.min_sale_price,
      max_sale_price = excluded.max_sale_price,
      newest_sale_date = excluded.newest_sale_date,
      oldest_sale_date = excluded.oldest_sale_date,
      divergence_sale_price = excluded.divergence_sale_price,
      weighted_sale_price = excluded.weighted_sale_price
"""

_SNAPSHOT_FIELDS = (
    "card_id", "condition", "finish", "specialty_one", "snapshot_date", "source",
    "listing_count", "lowest_listing_price", "median_listing_price",
    "mean_listing_price", "p25_listing_price", "p75_listing_price",
    "recent_sales_count", "avg_sale_price", "median_sale_price",
    "min_sale_price", "max_sale_price", "newest_sale_date", "oldest_sale_date",
    # Pricing inputs: the divergence signal and the age-weighted sold mean the
    # algorithm blends. Stored so a price can be recomputed without refetching.
    "divergence_sale_price", "weighted_sale_price",
)


def _params(snapshot: dict[str, Any]) -> dict[str, Any]:
    params = {k: snapshot.get(k) for k in _SNAPSHOT_FIELDS}
    # specialty_one is NOT NULL; older callers omit it entirely.
    params["specialty_one"] = params["specialty_one"] or "None"
    return params


class SnapshotStore:
    def __init__(self, db: sqlite3.Connection):
        self.db = db

    def upsert(self, snapshot: dict[str, Any]) -> None:
        self.db.execute(_INSERT_SQL, _params(snapshot))

    def upsert_batch(self, snapshots: list[dict[str, Any]]) -> int:
        self.db.execute("BEGIN")
        try:
            for row in snapshots:
                self.db.execute(_INSERT_SQL, _params(row))
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        return len(snapshots)

    def get_latest(
        self, card_id: str, condition: str, finish: str, specialty_one: str = "None"
    ) -> Optional[dict]:
        row = self.db.execute(
            "SELECT * FROM market_snapshots WHERE card_id = ? AND condition = ? AND finish = ? "
            "AND specialty_one = ? ORDER BY snapshot_date DESC LIMIT 1",
            (card_id, condition, finish, specialty_one),
        ).fetchone()
        return dict(row) if row else None

    def get_history(
        self,
        card_id: str,
        condition: str,
        finish: str,
        limit: int = 90,
        offset: int = 0,
        specialty_one: str = "None",
    ) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM market_snapshots WHERE card_id = ? AND condition = ? AND finish = ? "
            "AND specialty_one = ? ORDER BY snapshot_date DESC LIMIT ? OFFSET ?",
            (card_id, condition, finish, specialty_one, limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]

    def search(self, query: dict[str, Any]) -> list[dict]:
        conditions: list[str] = []
        params: list[Any] = []

        if query.get("card_id"):
            conditions.append("card_id = ?")
            params.append(query["card_id"])
        if query.get("condition"):
            conditions.append("condition = ?")
            params.append(query["condition"])
        if query.get("finish"):
            conditions.append("finish = ?")
            params.append(query["finish"])
        if query.get("specialty_one"):
            conditions.append("specialty_one = ?")
            params.append(query["specialty_one"])
        if query.get("source"):
            conditions.append("source = ?")
            params.append(query["source"])
        if query.get("date_from"):
            conditions.append("snapshot_date >= ?")
            params.append(query["date_from"])
        if query.get("date_to"):
            conditions.append("snapshot_date <= ?")
            params.append(query["date_to"])

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(query.get("limit", 100))
        params.append(query.get("offset", 0))

        rows = self.db.execute(
            f"SELECT * FROM market_snapshots {where} ORDER BY snapshot_date DESC LIMIT ? OFFSET ?",
            params,
        ).fetchall()
        return [dict(r) for r in rows]

    def get_tracked_card_ids(self) -> list[str]:
        rows = self.db.execute("SELECT DISTINCT card_id FROM market_snapshots").fetchall()
        return [r["card_id"] for r in rows]

    def get_last_collection_date(self) -> Optional[str]:
        row = self.db.execute("SELECT MAX(snapshot_date) as max_date FROM market_snapshots").fetchone()
        return row["max_date"] if row else None

    def count(self) -> int:
        row = self.db.execute("SELECT COUNT(*) as cnt FROM market_snapshots").fetchone()
        return row["cnt"]

    def prune_older_than(self, before_date: str) -> int:
        return self.db.execute(
            "DELETE FROM market_snapshots WHERE snapshot_date < ?", (before_date,)
        ).rowcount
