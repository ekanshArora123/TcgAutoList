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

    async def collect_price_history(
        self, card_ids: list[str] | None = None, options: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Fast bucket sweep: fetch ONLY infinite-api price history (no raw sales).

        The infinite endpoint is a separate, generous rate bucket, so this sweeps
        the whole collection in minutes. It also lands the per-card market value
        that the raw drip then orders by (high-value first).
        """
        ids = card_ids if card_ids is not None else self._get_owned_card_ids()
        opts = {"ratePerSec": 4.0, "withSales": False, "withPriceHistory": True}
        opts.update(options or {})
        return await self.collect_cards(ids, opts)

    async def collect_crawl(
        self, max_age_days: int = 7, options: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Persistent raw-sales drip over ALL pending cards, highest-value first.

        For the one-time multi-day backfill: adaptive pacing + persist (never
        give up on throttling), raw sales only (price history comes from the
        bucket sweep), ordered by market value so useful data lands first.
        Resumable — re-running skips cards already fetched within max_age_days.
        """
        opts = {
            "ratePerSec": 0.16,      # measured sustainable rate
            "adaptive": True,
            "persist": True,
            "withPriceHistory": False,
            "jitter": 0.1,           # tiny — the band is intentionally narrow
            # Hold the rate in a tight 0.15–0.167 band: never sprint to the
            # ~0.33 that trips the limit, never crawl below the sustainable rate.
            "minInterval": 6.0,      # fastest gap  -> 0.167 req/s ceiling
            "maxInterval": 6.6,      # slowest gap  -> ~0.15 req/s floor
            # On a block, pause long enough for the bucket to actually refill,
            # escalating if it persists: 5 min, then 20 min, then 1 hour.
            "blockPauseScheduleS": [300, 1200, 3600],
            # ~a day of solid blocking (24 consecutive 1h pauses) => stop and
            # surface it; almost certainly an expired cookie, not throttling.
            "catastrophicBlocks": 24,
        }
        opts.update(options or {})
        return await self.collect_cards(
            self._get_crawl_card_ids(max_age_days), opts
        )

    async def collect_cards(
        self, card_ids: list[str], options: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        options = options or {}
        verbose = options.get("verbose", True)
        max_days = options.get("maxDays", DEFAULT_MAX_DAYS)
        rate_per_sec = options.get("ratePerSec", 1.5)
        jitter = options.get("jitter", 0.4)
        adaptive = options.get("adaptive", False)
        # Adaptive band (only used when adaptive): minInterval caps top speed,
        # maxInterval caps how slow on_block can drag the steady rate.
        min_interval = options.get("minInterval", 3.0)
        max_interval = options.get("maxInterval", 60.0)
        # persist: drip mode — never give up on blocks (back off + adapt + keep
        # going), for an unattended multi-day crawl. Off = batch mode that stops
        # cleanly after a few blocks so a caller can resume later.
        persist = options.get("persist", False)
        with_sales = options.get("withSales", True)
        with_price_history = options.get("withPriceHistory", True)
        # Block handling: an escalating pause ladder (seconds) indexed by the
        # consecutive-block count. The refill bucket needs minutes, not seconds.
        block_pause_schedule_s = options.get("blockPauseScheduleS", [30, 90, 300])
        max_consecutive_blocks = options.get("maxConsecutiveBlocks", 3)
        # Safety valve even in persist mode: a wall this tall means something is
        # broken (expired cookie, hard IP ban), not transient throttling.
        catastrophic_blocks = options.get("catastrophicBlocks", 250)
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

        limiter = RateLimiter(
            rate_per_sec=rate_per_sec, jitter=jitter, adaptive=adaptive,
            min_interval=min_interval, max_interval=max_interval,
        )
        client = make_client()

        consecutive_blocks = 0  # resets after any clean card
        i = 0
        try:
            while i < len(card_ids):
                card_id = card_ids[i]
                try:
                    await self._ensure_card_exists(card_id)
                    if with_sales:
                        # TODO(sales-idempotency): this always walks the full
                        # `max_days` window and hands it to replace_card, so a
                        # refresh re-fetches a year of immutable sales it already
                        # has — the bulk of this collector's rate-limit exposure.
                        # Fetch only sales newer than the card's newest stored
                        # order_date and append instead. See sales_store.replace_card.
                        sales = await fetch_sales_history(
                            card_id, max_days=max_days, client=client, limiter=limiter
                        )
                        report["sales_written"] += self.store.replace_card(card_id, sales)
                    if with_price_history:
                        price_rows = await fetch_price_history(
                            card_id, client=client, limiter=limiter
                        )
                        report["price_points_written"] += self.price_store.replace_card(
                            card_id, price_rows
                        )

                    report["cards_processed"] += 1
                    if verbose:
                        card = self.cards.get_by_id(card_id)
                        name = card["card_name"] if card else card_id
                        rate = limiter.current_rate
                        rate_s = f" @ {rate}/s" if adaptive and rate else ""
                        print(
                            f"[{i + 1}/{len(card_ids)}] {name}: "
                            f"{report['cards_processed']} done{rate_s}"
                        )
                    limiter.on_success()
                    consecutive_blocks = 0  # a clean card clears the escalation
                    i += 1
                except RateLimited as err:
                    report["blocks"] += 1
                    consecutive_blocks += 1
                    limiter.on_block()
                    # Stop conditions: batch mode after a few blocks; persist mode
                    # only at the catastrophic wall.
                    limit = catastrophic_blocks if persist else max_consecutive_blocks
                    if consecutive_blocks > limit:
                        report["stopped_early"] = True
                        if verbose:
                            print(
                                f"\nStopping: {consecutive_blocks - 1} consecutive rate-limit "
                                f"blocks at card {i + 1}/{len(card_ids)}. "
                                f"{report['cards_processed']} cards this run; resumable on re-run."
                            )
                        break
                    pause_s = self._block_pause_s(consecutive_blocks, block_pause_schedule_s)
                    if verbose:
                        scope = f"{consecutive_blocks}" if persist else f"{consecutive_blocks}/{max_consecutive_blocks}"
                        print(
                            f"  rate limited (HTTP {err.status}) at card {i + 1}/{len(card_ids)}; "
                            f"backing off {pause_s:.0f}s (block {scope}, now @ {limiter.current_rate}/s)..."
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
    def _block_pause_s(consecutive_blocks: int, schedule_s: list[float]) -> float:
        """Seconds to pause after a block, from an escalating ladder.

        Indexed by the consecutive-block count (1-based), holding at the last
        entry. e.g. ``[300, 1200, 3600]`` => 5 min, then 20 min, then 1 hour for
        every further consecutive block. TCGplayer's ``Retry-After`` is ignored
        on purpose: it comes back as a few seconds while the real cooldown is
        minutes, so honouring it just 429s again immediately.
        """
        idx = min(max(consecutive_blocks, 1) - 1, len(schedule_s) - 1)
        return schedule_s[idx]

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

    def _get_crawl_card_ids(self, max_age_days: int) -> list[str]:
        """All pending owned cards ordered by market value DESC (highest first).

        Value = peak market price seen in market_price_history (populated by the
        bucket sweep). Cards with no value data yet sort last (SQLite orders
        NULLs last under DESC). 'Pending' = never raw-fetched or stale, same rule
        as the batch selector.
        """
        cutoff = (datetime.now() - timedelta(days=max_age_days)).isoformat()
        rows = self.db.execute(
            """
            SELECT s.card_id,
                   MAX(sl.fetched_at) AS last_fetched,
                   (SELECT MAX(market_price) FROM market_price_history m
                    WHERE m.card_id = s.card_id) AS value
            FROM inventory i
            JOIN skus s ON i.sku_id = s.sku_id
            LEFT JOIN sales sl ON sl.card_id = s.card_id
            WHERE i.status NOT IN ('sold')
            GROUP BY s.card_id
            HAVING last_fetched IS NULL OR last_fetched < ?
            ORDER BY value DESC, s.card_id
            """,
            (cutoff,),
        ).fetchall()
        return [r["card_id"] for r in rows]

    def _get_cards_missing_price_history(self) -> list[str]:
        """Owned cards with no market_price_history rows yet (phase-1 sweep set).

        Makes the bucket sweep resumable: a restart only fetches cards still
        missing their value data.
        """
        rows = self.db.execute(
            """
            SELECT DISTINCT s.card_id
            FROM inventory i
            JOIN skus s ON i.sku_id = s.sku_id
            WHERE i.status NOT IN ('sold')
            AND s.card_id NOT IN (SELECT DISTINCT card_id FROM market_price_history)
            """
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
