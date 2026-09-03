"""Market Data Collector — Orchestrates periodic market data collection.

Responsibilities: select cards to collect, fetch market data, aggregate into
snapshots, store snapshots, run the pricing algorithm and store results.

Idempotent per date (upserts on the unique constraint). Ported from collector.ts.

CollectionReport / CollectorOptions are plain dicts.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from datetime import datetime, timedelta
from typing import Any

from ..crud.cards import CardsHelper
from ..crud.skus import SkusHelper
from ..pricing.conditions import expand_to_primaries, normalize_finish
from ..pricing.config import DEFAULT_CONFIG, PricingConfig
from ..pricing.repricer import Repricer
from .aggregators import build_snapshots
from .fetchers import fetch_card_info, fetch_market_data_for_card
from .snapshots import SnapshotStore

CollectionReport = dict
CollectorOptions = dict

# Every selection mode hands its cards back most-expensive-first. A run is often
# cut short — a rate-limit wall it never gets past, a Ctrl-C, a machine that goes
# to sleep — and whatever it did manage to collect should be the cards where a
# stale price costs the most. Ordering here (rather than in `collect_cards`)
# keeps an explicit `--cards` list in the order the caller gave it.
#
# A card's value is the highest estimate across its SKUs on that SKU's most
# recent calculation date — the card's best-known worth today, condition-agnostic
# so a DMG copy of a $500 card still sorts as an expensive card. Cards never
# priced (new adds, --cohort neighbours) have nothing to sort on and go last.
_CARD_VALUE_CTE = """
    WITH card_value AS (
        SELECT s.card_id AS card_id, MAX(p.estimated_price) AS value
        FROM prices p
        JOIN skus s ON s.sku_id = p.sku_id
        WHERE p.calculation_date = (
            SELECT MAX(p2.calculation_date) FROM prices p2 WHERE p2.sku_id = p.sku_id
        )
        GROUP BY s.card_id
    )
"""
# `value IS NULL` first so unpriced cards sort after every priced one (DESC alone
# would put SQLite's NULLs at the top).
_BY_VALUE_DESC = "ORDER BY v.value IS NULL, v.value DESC"


class MarketCollector:
    """Collects market data and stores it; pricing is delegated, not duplicated.

    A card visit does two separable things: write this card's market state to
    `market_snapshots`, then ask the `Repricer` to turn that stored state into
    prices. The collector never runs the pricing algorithm itself, so a reprice
    triggered from anywhere else produces byte-identical results.
    """

    def __init__(
        self,
        db: sqlite3.Connection,
        variant_delay_ms: int = 0,
        config: PricingConfig = DEFAULT_CONFIG,
    ):
        self.db = db
        self.snapshot_store = SnapshotStore(db)
        self.cards = CardsHelper(db)
        self.skus = SkusHelper(db)
        self.repricer = Repricer(db, config)
        self.config = config
        # Pacing BETWEEN the listing calls of a single card. A card now costs one
        # listing request per owned variant (~1.5 on average) instead of one flat,
        # so the intra-card gap matters to the per-IP budget as much as the
        # between-card `delayMs` does.
        self.variant_delay_ms = variant_delay_ms

    async def collect_owned(self, options: dict[str, Any] | None = None) -> dict[str, Any]:
        """Collect market data for all cards the user owns, priciest first."""
        return await self.collect_cards(self._get_owned_card_ids(), options or {})

    async def collect_cards(
        self, card_ids: list[str], options: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Collect market data for a specific list of card IDs."""
        options = options or {}
        delay_ms = options.get("delayMs", 500)
        verbose = options.get("verbose", True)
        rate_limit_pause_ms = options.get("rateLimitPauseMs", 120_000)
        max_retries = options.get("maxRetries", 50)
        force = options.get("force", False)
        today = datetime.now().date().isoformat()
        start = time.time()

        # Same-day idempotency, unless forced. `force` exists for the case where
        # today's rows were written by an older/buggier collector and need to be
        # overwritten rather than skipped — the snapshot and price upserts are
        # keyed by date, so a forced re-run replaces them in place.
        already_collected = set() if force else set(self._get_card_ids_collected_on_date(today))
        remaining = [cid for cid in card_ids if cid not in already_collected]

        if verbose and force:
            print(f"--force: ignoring same-day skip, re-collecting all {len(remaining)} cards.\n")
        elif verbose and already_collected:
            skipped = len(card_ids) - len(remaining)
            print(f"Skipping {skipped} cards already collected today. {len(remaining)} remaining.\n")

        report: dict[str, Any] = {
            "date": today,
            "cards_processed": 0,
            "snapshots_written": 0,
            "prices_written": 0,
            "errors": [],
            "duration_ms": 0,
        }

        retry_count = 0
        i = 0

        while i < len(remaining):
            card_id = remaining[i]

            if i > 0 and delay_ms > 0:
                await asyncio.sleep(delay_ms / 1000)

            try:
                await self._ensure_card_exists(card_id)
                result = await self._collect_single_card(card_id, today)
                report["snapshots_written"] += result["snapshots"]
                report["prices_written"] += result["prices"]
                report["cards_processed"] += 1

                if verbose:
                    card = self.cards.get_by_id(card_id)
                    name = card["card_name"] if card else card_id
                    print(
                        f"[{i + 1}/{len(remaining)}] {name}: "
                        f"{result['snapshots']} snapshots, {result['prices']} prices"
                    )
                i += 1
            except Exception as err:
                message = str(err)
                if self._is_rate_limit_error(message) and retry_count < max_retries:
                    retry_count += 1
                    pause_sec = rate_limit_pause_ms / 1000
                    if verbose:
                        print(
                            f"\nRate limited at card {i + 1}/{len(remaining)}. "
                            f"Pausing {pause_sec}s before retry (attempt {retry_count}/{max_retries})..."
                        )
                    await asyncio.sleep(rate_limit_pause_ms / 1000)
                    # Don't increment i — retry the same card
                else:
                    report["errors"].append({"card_id": card_id, "error": message})
                    if verbose:
                        print(f"[{i + 1}/{len(remaining)}] ERROR {card_id}: {message}")
                    i += 1

        report["duration_ms"] = int((time.time() - start) * 1000)

        if verbose:
            print(
                f"\nCollection complete: {report['cards_processed']} cards, "
                f"{report['snapshots_written']} snapshots, {report['prices_written']} prices, "
                f"{len(report['errors'])} errors, {retry_count} rate-limit pauses, "
                f"{report['duration_ms'] / 1000:.1f}s"
            )

        return report

    def _is_rate_limit_error(self, message: str) -> bool:
        return "403" in message or "429" in message or "rate limit" in message

    async def collect_cohort(self, options: dict[str, Any] | None = None) -> dict[str, Any]:
        """Collect market data for cards in the same set as owned cards."""
        return await self.collect_cards(self._get_cohort_card_ids(), options or {})

    async def collect_stale(
        self, max_age_days: int = 7, options: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Collect cards whose latest snapshot is older than max_age_days."""
        return await self.collect_cards(self._get_stale_card_ids(max_age_days), options or {})

    # ─── Single Card Collection ───────────────────────────────

    async def _collect_single_card(self, card_id: str, date: str) -> dict[str, int]:
        card = self.cards.get_by_id(card_id)
        fetch_results = await fetch_market_data_for_card(
            card_id,
            variants=self._plan_variants(card_id) or None,
            set_name=card["set_name"] if card else None,
            delay_ms=self.variant_delay_ms,
        )

        if not fetch_results:
            return {"snapshots": 0, "prices": 0}

        # Store the market state first, then price FROM the store — not from the
        # in-memory fetch. Slightly more work, but it means the collector and a
        # standalone reprice run the exact same code over the exact same inputs.
        snapshots = build_snapshots(fetch_results, date, self.config)
        self.snapshot_store.upsert_batch(snapshots)

        prices_written = self.repricer.price_card(card_id, date)

        return {"snapshots": len(snapshots), "prices": prices_written}

    def _plan_variants(self, card_id: str) -> list[tuple[str, str, str]]:
        """The (condition, finish, specialty_one) listings to fetch for this card.

        Derived from the SKUs actually held, expanded through
        `expand_to_primaries` so an in-between grade pulls BOTH its neighbors —
        an MP-LP card plans an MP fetch and an LP fetch. The result is a set, so
        an MP-LP and a plain MP on the same card share one MP call rather than
        duplicating it, and both neighbors are always fetched in the same card
        visit (they can never end up at different ages).

        Empty for cards with no inventory (--cohort), where the caller falls back
        to whatever the sold data reveals.
        """
        rows = self.db.execute(
            """
            SELECT DISTINCT s.condition, s.finish, s.specialty_one
            FROM inventory i
            JOIN skus s ON i.sku_id = s.sku_id
            WHERE s.card_id = ? AND i.status NOT IN ('sold')
            """,
            (card_id,),
        ).fetchall()

        variants: set[tuple[str, str, str]] = set()
        for row in rows:
            finish = normalize_finish(row["finish"])
            for condition in expand_to_primaries(row["condition"]):
                variants.add((condition, finish, row["specialty_one"] or "None"))
        return sorted(variants)

    # ─── Card Selection Queries ───────────────────────────────

    def select_cards(self, mode: str, stale_days: int = 7) -> list[str]:
        """Card IDs for a selection mode, most expensive first (see
        `_CARD_VALUE_CTE`), so a reprice-only run can target the same set in the
        same order a collecting run would without duplicating these queries."""
        if mode == "cohort":
            return self._get_cohort_card_ids()
        if mode == "stale":
            return self._get_stale_card_ids(stale_days)
        return self._get_owned_card_ids()

    def _get_owned_card_ids(self) -> list[str]:
        rows = self.db.execute(
            _CARD_VALUE_CTE
            + """
            SELECT DISTINCT s.card_id, v.value
            FROM inventory i
            JOIN skus s ON i.sku_id = s.sku_id
            LEFT JOIN card_value v ON v.card_id = s.card_id
            WHERE i.status NOT IN ('sold')
            """
            + _BY_VALUE_DESC
        ).fetchall()
        return [r["card_id"] for r in rows]

    def _get_cohort_card_ids(self) -> list[str]:
        rows = self.db.execute(
            _CARD_VALUE_CTE
            + """
            SELECT DISTINCT c2.id, v.value
            FROM cards c2
            LEFT JOIN card_value v ON v.card_id = c2.id
            WHERE c2.set_name IN (
              SELECT DISTINCT c.set_name
              FROM inventory i
              JOIN skus s ON i.sku_id = s.sku_id
              JOIN cards c ON s.card_id = c.id
              WHERE i.status NOT IN ('sold') AND c.set_name IS NOT NULL
            )
            AND c2.id NOT IN (
              SELECT DISTINCT s.card_id
              FROM inventory i
              JOIN skus s ON i.sku_id = s.sku_id
              WHERE i.status NOT IN ('sold')
            )
            """
            + _BY_VALUE_DESC
        ).fetchall()
        return [r["id"] for r in rows]

    def _get_stale_card_ids(self, max_age_days: int) -> list[str]:
        cutoff_str = (datetime.now() - timedelta(days=max_age_days)).date().isoformat()
        rows = self.db.execute(
            _CARD_VALUE_CTE
            + """
            SELECT DISTINCT s.card_id, v.value
            FROM inventory i
            JOIN skus s ON i.sku_id = s.sku_id
            LEFT JOIN card_value v ON v.card_id = s.card_id
            WHERE i.status NOT IN ('sold')
            AND s.card_id NOT IN (
              SELECT card_id FROM market_snapshots WHERE snapshot_date >= ?
            )
            """
            + _BY_VALUE_DESC,
            (cutoff_str,),
        ).fetchall()
        return [r["card_id"] for r in rows]

    def _get_card_ids_collected_on_date(self, date: str) -> list[str]:
        rows = self.db.execute(
            "SELECT DISTINCT card_id FROM market_snapshots WHERE snapshot_date = ?", (date,)
        ).fetchall()
        return [r["card_id"] for r in rows]

    # ─── Card Metadata ────────────────────────────────────────

    async def _ensure_card_exists(self, card_id: str) -> None:
        existing = self.cards.get_by_id(card_id)
        if existing:
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

    # ─── Accessors ────────────────────────────────────────────

    def get_snapshot_store(self) -> SnapshotStore:
        return self.snapshot_store
