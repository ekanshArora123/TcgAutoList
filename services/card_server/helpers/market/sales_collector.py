"""Sales History Collector — Comprehensive raw sales gathering.

Walks the full TCGplayer sales history (~1 year) for each card and stores the
raw rows in the `sales` table. Deliberately independent of MarketCollector and
the pricing path: it never computes prices or writes prices/market_snapshots.

Idempotent: each card's rows are replaced on every run.

CollectionReport / CollectorOptions are plain dicts.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from datetime import datetime, timedelta
from typing import Any

from ..crud.cards import CardsHelper
from ..tcgplayer.fetch_card_info import fetch_card_info
from ..tcgplayer.fetch_price_history import fetch_price_history
from ..tcgplayer.fetch_sales_history import DEFAULT_MAX_DAYS, fetch_sales_history
from .price_history_store import MarketPriceStore
from .sales_store import SalesStore


class SalesCollector:
    """Gathers per-card graph data: raw sales history + market-price history.

    Both feed the per-card sales graph only — never the pricing algorithm.
    """

    def __init__(self, db: sqlite3.Connection):
        self.db = db
        self.store = SalesStore(db)
        self.price_store = MarketPriceStore(db)
        self.cards = CardsHelper(db)

    async def collect_owned(self, options: dict[str, Any] | None = None) -> dict[str, Any]:
        """Collect sales history for all owned (in-inventory) cards."""
        return await self.collect_cards(self._get_owned_card_ids(), options or {})

    async def collect_stale(
        self, max_age_days: int = 7, options: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Collect owned cards whose sales were last fetched > max_age_days ago."""
        return await self.collect_cards(self._get_stale_card_ids(max_age_days), options or {})

    async def collect_cards(
        self, card_ids: list[str], options: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        options = options or {}
        delay_ms = options.get("delayMs", 500)
        verbose = options.get("verbose", True)
        rate_limit_pause_ms = options.get("rateLimitPauseMs", 120_000)
        max_retries = options.get("maxRetries", 50)
        max_days = options.get("maxDays", DEFAULT_MAX_DAYS)
        start = time.time()

        report: dict[str, Any] = {
            "cards_processed": 0,
            "sales_written": 0,
            "price_points_written": 0,
            "errors": [],
            "duration_ms": 0,
        }

        retry_count = 0
        i = 0
        while i < len(card_ids):
            card_id = card_ids[i]

            if i > 0 and delay_ms > 0:
                await asyncio.sleep(delay_ms / 1000)

            try:
                await self._ensure_card_exists(card_id)
                sales = await fetch_sales_history(card_id, max_days=max_days)
                written = self.store.replace_card(card_id, sales)
                report["sales_written"] += written

                price_rows = await fetch_price_history(card_id)
                price_written = self.price_store.replace_card(card_id, price_rows)
                report["price_points_written"] += price_written

                report["cards_processed"] += 1

                if verbose:
                    card = self.cards.get_by_id(card_id)
                    name = card["card_name"] if card else card_id
                    print(f"[{i + 1}/{len(card_ids)}] {name}: {written} sales, {price_written} price points")
                i += 1
            except Exception as err:  # noqa: BLE001
                message = str(err)
                if self._is_rate_limit_error(message) and retry_count < max_retries:
                    retry_count += 1
                    if verbose:
                        print(
                            f"\nRate limited at card {i + 1}/{len(card_ids)}. "
                            f"Pausing {rate_limit_pause_ms / 1000}s "
                            f"(attempt {retry_count}/{max_retries})..."
                        )
                    await asyncio.sleep(rate_limit_pause_ms / 1000)
                    # Don't increment i — retry the same card.
                else:
                    report["errors"].append({"card_id": card_id, "error": message})
                    if verbose:
                        print(f"[{i + 1}/{len(card_ids)}] ERROR {card_id}: {message}")
                    i += 1

        report["duration_ms"] = int((time.time() - start) * 1000)

        if verbose:
            print(
                f"\nSales collection complete: {report['cards_processed']} cards, "
                f"{report['sales_written']} sales, {report['price_points_written']} price points, "
                f"{len(report['errors'])} errors, {retry_count} rate-limit pauses, "
                f"{report['duration_ms'] / 1000:.1f}s"
            )

        return report

    def _is_rate_limit_error(self, message: str) -> bool:
        return "403" in message or "429" in message or "rate limit" in message

    # ─── Card selection ───────────────────────────────────────

    def _get_owned_card_ids(self) -> list[str]:
        rows = self.db.execute(
            """
            SELECT DISTINCT s.card_id
            FROM inventory i
            JOIN skus s ON i.sku_id = s.sku_id
            WHERE i.status NOT IN ('sold')
            """
        ).fetchall()
        return [r["card_id"] for r in rows]

    def _get_stale_card_ids(self, max_age_days: int) -> list[str]:
        cutoff = (datetime.now() - timedelta(days=max_age_days)).isoformat()
        rows = self.db.execute(
            """
            SELECT DISTINCT s.card_id
            FROM inventory i
            JOIN skus s ON i.sku_id = s.sku_id
            WHERE i.status NOT IN ('sold')
            AND s.card_id NOT IN (
              SELECT card_id FROM sales WHERE fetched_at >= ?
            )
            """,
            (cutoff,),
        ).fetchall()
        return [r["card_id"] for r in rows]

    # ─── Card metadata ────────────────────────────────────────

    async def _ensure_card_exists(self, card_id: str) -> None:
        if self.cards.get_by_id(card_id):
            return

        fetched = await fetch_card_info(card_id)
        if not fetched:
            raise RuntimeError(f"Card {card_id} not found on TCGplayer")

        meta = fetched["metadata"]
        self.cards.upsert(
            {
                "id": meta["tcgplayer_id"],
                "card_name": meta["card_name"],
                "set_name": meta["set_name"],
                "product_line": meta["product_line"],
                "card_type": meta["card_type"],
                "visual_layout": None,
                "rarity": meta["rarity"],
                "card_number": meta["card_number"],
                "product_type": None,
                "era": None,
                "set_type": None,
            }
        )
