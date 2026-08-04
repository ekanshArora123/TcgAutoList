"""Repricer — turns stored pricing inputs into stored prices.

This is the one place a price is written from. It performs no network I/O: it
reads `market_snapshots` (which `collect` fills), runs the algorithm, and writes
`prices`. That separation is the point — collection and pricing are independent,
so prices can be regenerated after a config change without re-fetching anything,
and the collector and the on-demand service can't drift apart.

Two entry points, differing only in scope:

  * `price_card` — price every SKU of a card. What the collector calls.
  * `price_sku`  — price one SKU, loading whichever variants it depends on.

The distinction matters because derived SKUs have no snapshot of their own.
TCGplayer sells five condition tiers, so an in-between grade (MP-LP), an alias
(MINT, DM) and an error variant (specialty_two) never get a snapshot row; each
is computed from the results of the variants that do. `price_sku` therefore
still loads and prices an MP-LP's MP and LP neighbours before interpolating.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any, Optional

from ..crud.prices import PricesHelper
from ..crud.skus import SkusHelper
from .algorithm import (
    PricingResult,
    compute_price,
    flag_error_variant,
    interpolate_in_between,
    relabel_alias,
)
from .conditions import (
    expand_to_primaries,
    is_primary,
    normalize_condition,
    normalize_finish,
    primary_neighbors,
)
from .config import DEFAULT_CONFIG, PricingConfig
from .inputs import PricingInputs, from_snapshot

# One row per variant: its most recent snapshot, whatever date that landed on.
# Using the latest rather than a fixed date means a variant that wasn't refetched
# this run (sold-only coverage, a skipped fan-out) still contributes its last
# known state instead of dropping out of the card entirely.
_LATEST_SNAPSHOTS_SQL = """
    SELECT ms.* FROM market_snapshots ms
    WHERE ms.card_id = :card_id
      AND ms.snapshot_date = (
        SELECT MAX(m2.snapshot_date) FROM market_snapshots m2
        WHERE m2.card_id = ms.card_id
          AND m2.condition = ms.condition
          AND m2.finish = ms.finish
          AND m2.specialty_one = ms.specialty_one
          AND m2.source = ms.source
      )
"""

VariantKey = tuple[str, str, str]


def store_price(
    prices: PricesHelper, sku_id: int, date: str, result: PricingResult
) -> Optional[dict]:
    """Persist one PricingResult. The single writer of the `prices` columns.

    `reasoning` is stored so a price can be explained after the fact — which
    branch fired, what diverged, whether it was derived.
    """
    return prices.upsert(
        {
            "sku_id": sku_id,
            "calculation_date": date,
            "estimated_price": result.estimated_price,
            "estimated_liquid_value": result.estimated_liquid_value,
            "confidence_percent": result.confidence_percent,
            "manual_check_necessary": result.manual_check_necessary,
            "manually_checked": False,
            "algorithm_version": result.algorithm_version,
            "estimated_low_price": result.estimated_low_price,
            "estimated_high_price": result.estimated_high_price,
            "estimated_low_price_liquid": result.estimated_low_price_liquid,
            "estimated_high_price_liquid": result.estimated_high_price_liquid,
            "reasoning": result.reasoning,
        }
    )


class Repricer:
    def __init__(self, db: sqlite3.Connection, config: PricingConfig = DEFAULT_CONFIG):
        self.db = db
        self.skus = SkusHelper(db)
        self.prices = PricesHelper(db)
        self.config = config

    # ─── Entry Points ─────────────────────────────────────────

    def price_card(self, card_id: str, date: Optional[str] = None) -> int:
        """Price every SKU of a card from its stored snapshots. Returns the count.

        Directly-fetchable variants are priced from their own snapshot; the rest
        (aliases, in-between grades, error variants) are derived from those
        results, so a derived price is never built on a stale neighbour.
        """
        date = date or datetime.now().date().isoformat()
        priced = self._price_variants(card_id, self._load_inputs(card_id), date)

        count = len(priced.written)
        for sku in self.skus.search({"card_id": card_id}):
            if sku["sku_id"] in priced.written:
                continue
            derived = self._derive(sku, priced.results)
            if derived is None:
                continue
            store_price(self.prices, sku["sku_id"], date, derived)
            count += 1
        return count

    def price_sku(self, sku_id: int, date: Optional[str] = None) -> Optional[dict]:
        """Price a single SKU, loading only the variants it depends on.

        Returns the stored price row, or None when the SKU has no usable inputs
        (no snapshot for it or its neighbours) — in which case its previous
        estimate is left untouched rather than replaced with a fabricated one.
        """
        sku = self.skus.get_by_id(sku_id)
        if sku is None:
            return None

        date = date or datetime.now().date().isoformat()
        finish = normalize_finish(sku["finish"])
        specialty_one = sku["specialty_one"] or "None"

        # An in-between grade needs both neighbours; a primary needs only itself.
        needed = {
            (condition, finish, specialty_one)
            for condition in expand_to_primaries(sku["condition"])
        }
        inputs = [i for i in self._load_inputs(sku["card_id"]) if _key(i) in needed]
        priced = self._price_variants(sku["card_id"], inputs, date, store=False)

        own_key = (normalize_condition(sku["condition"]), finish, specialty_one)
        if own_key in priced.results and _is_plain_primary(sku):
            result = priced.results[own_key]
        else:
            result = self._derive(sku, priced.results)

        if result is None:
            return None
        return store_price(self.prices, sku_id, date, result)

    # ─── Internals ────────────────────────────────────────────

    def _load_inputs(self, card_id: str) -> list[PricingInputs]:
        """The latest snapshot per variant, as pricing inputs.

        Rows predating the pricing-input columns are dropped rather than priced:
        they know sales existed but not what they were, so using them would
        replace a blended estimate with a listing-only one — or, for a variant
        with no listings, wipe the price entirely. Skipping leaves the previous
        estimate standing, which is the same choice made everywhere else here.
        """
        rows = self.db.execute(_LATEST_SNAPSHOTS_SQL, {"card_id": card_id}).fetchall()
        loaded = [from_snapshot(dict(row)) for row in rows]
        return [i for i in loaded if not i.predates_pricing_inputs]

    def _price_variants(
        self, card_id: str, inputs: list[PricingInputs], date: str, store: bool = True
    ) -> "_PricedVariants":
        """Run the algorithm over each directly-fetchable variant.

        The SKU resolved here is always the plain one — specialty_two is an
        off-TCGplayer error attribute and `get_or_create` keys on
        specialty_two='None' — so `has_manual_review_specialty` is never set
        here. Error SKUs are derived from these results in `_derive`, which is
        where their review flag is applied.
        """
        results: dict[VariantKey, PricingResult] = {}
        written: set[int] = set()

        for inputs_row in inputs:
            key = _key(inputs_row)
            results[key] = compute_price(inputs_row, self.config)

            if store:
                sku = self.skus.get_or_create(
                    {
                        "card_id": card_id,
                        "condition": inputs_row.condition,
                        "finish": inputs_row.finish,
                        "specialty_one": inputs_row.specialty_one,
                        "specialty_two": "None",
                        "qty": 0,
                    }
                )
                store_price(self.prices, sku["sku_id"], date, results[key])
                written.add(sku["sku_id"])

        return _PricedVariants(results, written)

    def _derive(
        self, sku: dict[str, Any], results: dict[VariantKey, PricingResult]
    ) -> Optional[PricingResult]:
        """Build the result for a SKU TCGplayer has no tier for.

        Derives only from the SAME printing — a plain SKU must never inherit the
        1st Edition price, or vice versa.
        """
        finish = normalize_finish(sku["finish"])
        stored_condition = (sku["condition"] or "").strip().upper()
        condition = normalize_condition(sku["condition"])
        specialty_one = sku["specialty_one"] or "None"
        specialty_two = sku["specialty_two"] or "None"

        if is_primary(condition):
            source = results.get((condition, finish, specialty_one))
            if source is None:
                return None
            # Same tier under a different name (MINT/DM) gets relabelled; the
            # same tier under the SAME name is an error variant of it, whose
            # estimate carries over untouched (only the flag below changes).
            derived = (
                source
                if stored_condition == condition
                else relabel_alias(source, sku["condition"], condition)
            )
        else:
            neighbors = primary_neighbors(condition)
            if not neighbors:
                return None
            better, worse = neighbors
            derived = interpolate_in_between(
                sku["condition"],
                results.get((better, finish, specialty_one)),
                better,
                results.get((worse, finish, specialty_one)),
                worse,
                self.config,
            )
            if derived is None:
                return None

        if specialty_two != "None":
            derived = flag_error_variant(derived, specialty_two)
        return derived


class _PricedVariants:
    """Results of the direct pass, keyed by full variant, plus the SKUs written."""

    __slots__ = ("results", "written")

    def __init__(self, results: dict[VariantKey, PricingResult], written: set[int]):
        self.results = results
        self.written = written


def _key(inputs: PricingInputs) -> VariantKey:
    return (
        normalize_condition(inputs.condition),
        normalize_finish(inputs.finish),
        inputs.specialty_one or "None",
    )


def _is_plain_primary(sku: dict[str, Any]) -> bool:
    """True when the SKU is exactly a variant TCGplayer sells, under its own
    canonical name — the only case that maps 1:1 onto a snapshot result."""
    stored = (sku["condition"] or "").strip().upper()
    return (
        is_primary(stored)
        and stored == normalize_condition(stored)
        and (sku["specialty_two"] or "None") == "None"
    )
