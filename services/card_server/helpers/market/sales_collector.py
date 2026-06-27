"""Sales History Collector — Comprehensive raw sales gathering.

Walks the full TCGplayer sales history (~1 year) for each card and stores the
raw rows in the `sales` table. Deliberately independent of MarketCollector and
the pricing path: it never computes prices or writes prices/market_snapshots.

Robustness model (a full backfill is ~14 requests/card across thousands of
cards, so it must stay under TCGplayer's rate limiting):

  * One keep-alive client + one shared ``RateLimiter`` for the whole run, so the
    run is a single polite, jittered stream instead of per-request bursts.
  * On a 403/429 the collector backs off (honouring ``Retry-After`` when given),
    but only a few times — after ``max_consecutive_blocks`` it **stops the run
    cleanly** rather than freezing for an hour. Already-collected cards are kept.
  * Resumable by design: ``collect_batch`` picks the least-recently-fetched
    cards and skips ones refreshed within ``max_age_days``, so re-running (or a
    scheduled loop) drains the backlog and then keeps it fresh.

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
from ..tcgplayer.transport import RateLimited, RateLimiter, make_client
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

    async def collect_batch(
        self,
        limit: int = 150,
        max_age_days: int = 7,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Collect up to `limit` owned cards, least-recently-fetched first.

        Cards refreshed within `max_age_days` are skipped, so successive batches
        chip through the backlog without redoing work — the unit a scheduled
        loop runs.
        """
        return await self.collect_cards(
            self._get_batch_card_ids(limit, max_age_days), options or {}
        )

    async def collect_cards(
        self, card_ids: list[str], options: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        options = options or {}
        verbose = options.get("verbose", True)
        max_days = options.get("maxDays", DEFAULT_MAX_DAYS)
        rate_per_sec = options.get("ratePerSec", 1.5)
        jitter = options.get("jitter", 0.4)
        # Block handling: back off a few times, then stop the run cleanly.
        base_pause_ms = options.get("blockBasePauseMs", 30_000)
        max_pause_ms = options.get("blockMaxPauseMs", 300_000)
        max_consecutive_blocks = options.get("maxConsecutiveBlocks", 3)
        start = time.time()

        report: dict[str, Any] = {
            "cards_processed": 0,
            "sales_written": 0,
            "price_points_written": 0,
            "errors": [],
            "blocks": 0,
            "stopped_early": False,
            "duration_ms": 0,
        }

        limiter = RateLimiter(rate_per_sec=rate_per_sec, jitter=jitter)
        client = make_client()

        consecutive_blocks = 0  # resets after any clean card; trips the stop
        i = 0
        try:
            while i < len(card_ids):
                card_id = card_ids[i]
                try:
                    await self._ensure_card_exists(card_id)
                    sales = await fetch_sales_history(
                        card_id, max_days=max_days, client=client, limiter=limiter
                    )
                    written = self.store.replace_card(card_id, sales)
                    report["sales_written"] += written

                    price_rows = await fetch_price_history(
                        card_id, client=client, limiter=limiter
                    )
                    price_written = self.price_store.replace_card(card_id, price_rows)
                    report["price_points_written"] += price_written

                    report["cards_processed"] += 1
                    if verbose:
                        card = self.cards.get_by_id(card_id)
                        name = card["card_name"] if card else card_id
                        print(
                            f"[{i + 1}/{len(card_ids)}] {name}: "
                            f"{written} sales, {price_written} price points"
                        )
                    consecutive_blocks = 0  # a clean card clears the escalation
                    i += 1
                except RateLimited as err:
                    report["blocks"] += 1
                    consecutive_blocks += 1
                    if consecutive_blocks > max_consecutive_blocks:
                        report["stopped_early"] = True
                        if verbose:
                            print(
                                f"\nStopping: {consecutive_blocks - 1} consecutive rate-limit "
                                f"blocks at card {i + 1}/{len(card_ids)}. "
                                f"{report['cards_processed']} cards collected this run; "
                                f"re-run to resume from where it left off."
                            )
                        break
                    pause_s = self._block_pause_s(
                        err, consecutive_blocks, base_pause_ms, max_pause_ms
                    )
                    if verbose:
                        print(
                            f"\nRate limited (HTTP {err.status}) at card {i + 1}/{len(card_ids)}. "
                            f"Pausing {pause_s:.0f}s "
                            f"(block {consecutive_blocks}/{max_consecutive_blocks})..."
                        )
                    await limiter.penalize(pause_s)
                    await asyncio.sleep(pause_s)
                    # Don't increment i — retry the same card.
                except Exception as err:  # noqa: BLE001
                    report["errors"].append({"card_id": card_id, "error": str(err)})
                    if verbose:
                        print(f"[{i + 1}/{len(card_ids)}] ERROR {card_id}: {err}")
                    consecutive_blocks = 0
                    i += 1
        finally:
            await client.aclose()

        report["duration_ms"] = int((time.time() - start) * 1000)

        if verbose:
            print(
                f"\nSales collection complete: {report['cards_processed']} cards, "
                f"{report['sales_written']} sales, {report['price_points_written']} price points, "
                f"{len(report['errors'])} errors, {report['blocks']} rate-limit blocks, "
                f"{report['duration_ms'] / 1000:.1f}s"
                + (" [stopped early]" if report["stopped_early"] else "")
            )

        return report

    @staticmethod
    def _block_pause_s(
        err: RateLimited, consecutive_blocks: int, base_pause_ms: int, max_pause_ms: int
    ) -> float:
        """Seconds to wait after a block, capped at ``max_pause_ms``.

        Backoff escalates exponentially with consecutive blocks. TCGplayer's
        ``Retry-After`` is honoured only as a *floor raiser*: in practice it
        comes back as a few seconds while the real cooldown is longer, so a
        literal short wait just 429s again immediately. We therefore wait the
        larger of the server's hint and our own escalating floor.
        """
        exp_s = min(base_pause_ms * (2 ** (consecutive_blocks - 1)), max_pause_ms) / 1000
        if err.retry_after and err.retry_after > 0:
            return min(max(err.retry_after, exp_s), max_pause_ms / 1000)
        return exp_s

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

    def _get_batch_card_ids(self, limit: int, max_age_days: int) -> list[str]:
        """Up to `limit` owned cards needing a refresh, oldest-fetched first.

        Never-fetched cards sort ahead of stale ones; cards fetched within
        `max_age_days` are excluded. This ordering makes successive batches
        deterministic and resumable.
        """
        cutoff = (datetime.now() - timedelta(days=max_age_days)).isoformat()
        rows = self.db.execute(
            """
            SELECT s.card_id, MAX(sl.fetched_at) AS last_fetched
            FROM inventory i
            JOIN skus s ON i.sku_id = s.sku_id
            LEFT JOIN sales sl ON sl.card_id = s.card_id
            WHERE i.status NOT IN ('sold')
            GROUP BY s.card_id
            HAVING last_fetched IS NULL OR last_fetched < ?
            ORDER BY (last_fetched IS NOT NULL), last_fetched ASC
            LIMIT ?
            """,
            (cutoff, limit),
        ).fetchall()
        return [r["card_id"] for r in rows]

    def count_pending(self, max_age_days: int = 7) -> int:
        """How many owned cards still need a refresh (drives the loop runner)."""
        cutoff = (datetime.now() - timedelta(days=max_age_days)).isoformat()
        row = self.db.execute(
            """
            SELECT COUNT(*) AS c FROM (
              SELECT s.card_id, MAX(sl.fetched_at) AS last_fetched
              FROM inventory i
              JOIN skus s ON i.sku_id = s.sku_id
              LEFT JOIN sales sl ON sl.card_id = s.card_id
              WHERE i.status NOT IN ('sold')
              GROUP BY s.card_id
              HAVING last_fetched IS NULL OR last_fetched < ?
            )
            """,
            (cutoff,),
        ).fetchone()
        return row["c"]

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
