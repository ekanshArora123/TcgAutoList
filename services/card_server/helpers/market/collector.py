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
from ..crud.prices import PricesHelper
from ..crud.skus import SkusHelper
from ..pricing.algorithm import compute_price
from .aggregators import build_snapshots
from .fetchers import fetch_card_info, fetch_market_data_for_card
from .snapshots import SnapshotStore

CollectionReport = dict
CollectorOptions = dict


class MarketCollector:
    def __init__(self, db: sqlite3.Connection):
        self.db = db
        self.snapshot_store = SnapshotStore(db)
        self.cards = CardsHelper(db)
        self.skus = SkusHelper(db)
        self.prices = PricesHelper(db)

    async def collect_owned(self, options: dict[str, Any] | None = None) -> dict[str, Any]:
        """Collect market data for all cards the user owns (has inventory)."""
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
        today = datetime.now().date().isoformat()
        start = time.time()

        already_collected = set(self._get_card_ids_collected_on_date(today))
        remaining = [cid for cid in card_ids if cid not in already_collected]

        if verbose and already_collected:
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
        fetch_results = await fetch_market_data_for_card(card_id)

        if not fetch_results:
            return {"snapshots": 0, "prices": 0}

        snapshots = build_snapshots(fetch_results, date)
        self.snapshot_store.upsert_batch(snapshots)

        prices_written = self._compute_and_store_prices(card_id, fetch_results, date)

        return {"snapshots": len(snapshots), "prices": prices_written}

    def _compute_and_store_prices(
        self, card_id: str, fetch_results: list[dict[str, Any]], date: str
    ) -> int:
        count = 0

        for result in fetch_results:
            sku = self.skus.get_or_create(
                {
                    "card_id": card_id,
                    "condition": result["condition"],
                    "finish": result["finish"],
                    "specialty_one": "None",
                    "specialty_two": "None",
                    "qty": 0,
                }
            )

            has_manual_review_specialty = sku["specialty_two"] != "None"

            price_result = compute_price(
                result["activeListings"],
                result["soldListings"],
                result["condition"],
                result["finish"],
                has_manual_review_specialty,
            )

            self._store_price(sku["sku_id"], date, price_result)
            count += 1

        count += self._price_mint_skus_from_nm(card_id, fetch_results, date)
        return count

    def _price_mint_skus_from_nm(
        self, card_id: str, fetch_results: list[dict[str, Any]], date: str
    ) -> int:
        mint_skus = [s for s in self.skus.search({"card_id": card_id}) if s["condition"] == "MINT"]
        if not mint_skus:
            return 0

        count = 0
        for mint_sku in mint_skus:
            nm_result = next(
                (
                    r
                    for r in fetch_results
                    if r["condition"] == "NM" and r["finish"] == mint_sku["finish"]
                ),
                None,
            )
            if not nm_result:
                continue

            price_result = compute_price(
                nm_result["activeListings"],
                nm_result["soldListings"],
                "MINT",  # algorithm maps this to NM internally
                mint_sku["finish"],
                mint_sku["specialty_two"] != "None",
            )

            self._store_price(mint_sku["sku_id"], date, price_result)
            count += 1

        return count

    def _store_price(self, sku_id: int, date: str, price_result: Any) -> None:
        self.prices.upsert(
            {
                "sku_id": sku_id,
                "calculation_date": date,
                "estimated_price": price_result.estimated_price,
                "estimated_liquid_value": price_result.estimated_liquid_value,
                "confidence_percent": price_result.confidence_percent,
                "manual_check_necessary": price_result.manual_check_necessary,
                "manually_checked": False,
                "algorithm_version": price_result.algorithm_version,
                "estimated_low_price": price_result.estimated_low_price,
                "estimated_high_price": price_result.estimated_high_price,
                "estimated_low_price_liquid": price_result.estimated_low_price_liquid,
                "estimated_high_price_liquid": price_result.estimated_high_price_liquid,
            }
        )

    # ─── Card Selection Queries ───────────────────────────────

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

    def _get_cohort_card_ids(self) -> list[str]:
        rows = self.db.execute(
            """
            SELECT DISTINCT c2.id
            FROM cards c2
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
        ).fetchall()
        return [r["id"] for r in rows]

    def _get_stale_card_ids(self, max_age_days: int) -> list[str]:
        cutoff_str = (datetime.now() - timedelta(days=max_age_days)).date().isoformat()
        rows = self.db.execute(
            """
            SELECT DISTINCT s.card_id
            FROM inventory i
            JOIN skus s ON i.sku_id = s.sku_id
            WHERE i.status NOT IN ('sold')
            AND s.card_id NOT IN (
              SELECT card_id FROM market_snapshots WHERE snapshot_date >= ?
            )
            """,
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
