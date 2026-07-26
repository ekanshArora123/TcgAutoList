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
from ..pricing.algorithm import compute_price, interpolate_in_between, relabel_alias
from ..pricing.conditions import (
    expand_to_primaries,
    is_primary,
    normalize_condition,
    normalize_finish,
    primary_neighbors,
)
from .aggregators import build_snapshots
from .fetchers import fetch_card_info, fetch_market_data_for_card
from .snapshots import SnapshotStore

CollectionReport = dict
CollectorOptions = dict


class MarketCollector:
    def __init__(self, db: sqlite3.Connection, variant_delay_ms: int = 0):
        self.db = db
        self.snapshot_store = SnapshotStore(db)
        self.cards = CardsHelper(db)
        self.skus = SkusHelper(db)
        self.prices = PricesHelper(db)
        # Pacing BETWEEN the listing calls of a single card. A card now costs one
        # listing request per owned variant (~1.5 on average) instead of one flat,
        # so the intra-card gap matters to the per-IP budget as much as the
        # between-card `delayMs` does.
        self.variant_delay_ms = variant_delay_ms

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

        snapshots = build_snapshots(fetch_results, date)
        self.snapshot_store.upsert_batch(snapshots)

        prices_written = self._compute_and_store_prices(card_id, fetch_results, date)

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

    def _compute_and_store_prices(
        self, card_id: str, fetch_results: list[dict[str, Any]], date: str
    ) -> int:
        count = 0
        # Directly-priced results, keyed by the tier they describe, so the
        # derived pass below can look up an in-between grade's neighbors.
        priced: dict[tuple[str, str], Any] = {}
        priced_sku_ids: set[int] = set()

        for result in fetch_results:
            condition = normalize_condition(result["condition"])
            finish = normalize_finish(result["finish"])

            sku = self.skus.get_or_create(
                {
                    "card_id": card_id,
                    "condition": condition,
                    "finish": finish,
                    "specialty_one": result.get("specialtyOne") or "None",
                    "specialty_two": "None",
                    "qty": 0,
                }
            )

            has_manual_review_specialty = sku["specialty_two"] != "None"

            price_result = compute_price(
                result["activeListings"],
                result["soldListings"],
                condition,
                finish,
                has_manual_review_specialty,
            )

            self._store_price(sku["sku_id"], date, price_result)
            priced[(condition, finish)] = price_result
            priced_sku_ids.add(sku["sku_id"])
            count += 1

        count += self._price_derived_skus(card_id, priced, priced_sku_ids, date)
        return count

    def _price_derived_skus(
        self,
        card_id: str,
        priced: dict[tuple[str, str], Any],
        priced_sku_ids: set[int],
        date: str,
    ) -> int:
        """Price the card's SKUs that TCGplayer has no tier for.

        Two kinds, both previously skipped entirely by the collector: aliases of
        a real tier (MINT, DM) and in-between grades (MP-LP, LP-NM, HP-MP,
        DM-HP). Runs off `priced` — the results just computed from live data —
        so a derived price is never built on a stale neighbor.
        """
        count = 0

        for sku in self.skus.search({"card_id": card_id}):
            if sku["sku_id"] in priced_sku_ids:
                continue

            finish = normalize_finish(sku["finish"])
            condition = normalize_condition(sku["condition"])

            if is_primary(condition):
                source = priced.get((condition, finish))
                if source is None:
                    continue
                derived = relabel_alias(source, sku["condition"], condition)
            else:
                neighbors = primary_neighbors(condition)
                if not neighbors:
                    continue
                better, worse = neighbors
                derived = interpolate_in_between(
                    sku["condition"],
                    priced.get((better, finish)),
                    better,
                    priced.get((worse, finish)),
                    worse,
                )
                if derived is None:
                    continue

            self._store_price(sku["sku_id"], date, derived)
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
