"""PricingService — High-level operations for pricing cards.

Two ways in, both landing on the same algorithm:

  * `reprice_card` / `reprice_sku` — recompute from data already stored by
    `collect`. No network, so this is the cheap path: change a `PricingConfig`
    and regenerate the collection's prices in seconds.
  * `fetch_and_store_prices` / `compute_and_store_price` — fetch live TCGplayer
    data for one card and price it now. The on-demand path the seller pipeline
    uses when a card comes up before the next collection run.

Both build a `PricingInputs` and hand it to `compute_price`, and both write
through `repricer.store_price`, so neither can drift from the collector.
Ported from pricingService.ts.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any, Optional

from ..helpers.crud.cards import CardsHelper
from ..helpers.crud.prices import PricesHelper
from ..helpers.crud.skus import SkusHelper
from ..helpers.pricing import inputs
from ..helpers.pricing.algorithm import (
    compute_liquid_value,
    compute_price,
    extrapolate_across_conditions,
)
from ..helpers.pricing.config import DEFAULT_CONFIG, PricingConfig
from ..helpers.pricing.repricer import Repricer, store_price
from ..helpers.tcgplayer.fetch_card_info import fetch_card_info
from ..helpers.tcgplayer.fetch_prices import fetch_active_listings, fetch_sold_listings


def _group_by(items: list[dict[str, Any]], key_fn) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        grouped.setdefault(key_fn(item), []).append(item)
    return grouped


def _variant_key(item: dict[str, Any]) -> str:
    """Full variant identity. Listing rows carry no printing of their own — they
    are labelled with whatever was requested — so they default to 'None'."""
    return f"{item['condition']}|{item['finish']}|{item.get('specialty_one') or 'None'}"


class PricingService:
    def __init__(self, db: sqlite3.Connection, config: PricingConfig = DEFAULT_CONFIG):
        self.db = db
        self.cards = CardsHelper(db)
        self.skus = SkusHelper(db)
        self.prices = PricesHelper(db)
        self.config = config
        self.repricer = Repricer(db, config)

    # ─── Repricing (stored inputs, no network) ─────────────────

    def reprice_card(self, card_id: str, date: Optional[str] = None) -> int:
        """Recompute every SKU of a card from its stored snapshots."""
        return self.repricer.price_card(card_id, date)

    def reprice_sku(self, sku_id: int, date: Optional[str] = None) -> Optional[dict]:
        """Recompute one SKU from stored snapshots, loading what it depends on."""
        return self.repricer.price_sku(sku_id, date)

    # ─── Price Lookups (DB only) ───────────────────────────────

    def get_latest_price(self, sku_id: int) -> Optional[dict]:
        return self.prices.get_latest(sku_id)

    def get_latest_price_for_item(self, inventory_id: int) -> Optional[dict]:
        return self.prices.get_latest_for_inventory_item(inventory_id)

    def get_price_history(self, sku_id: int, limit: int = 50, offset: int = 0) -> list[dict]:
        return self.prices.get_history(sku_id, limit, offset)

    def search_prices(self, filters: dict[str, Any]) -> list[dict]:
        return self.prices.search(filters)

    def compare_prices_across_conditions(self, card_id: str) -> list[dict]:
        return self.prices.get_prices_by_card_across_conditions(card_id)

    def get_pending_manual_checks(self) -> list[dict]:
        return self.prices.get_pending_manual_checks()

    def mark_price_checked(self, sku_id: int, calculation_date: str) -> bool:
        return self.prices.mark_as_checked(sku_id, calculation_date)

    # ─── Price Storage (manual / bulk import) ──────────────────

    def record_price(self, input: dict[str, Any]) -> Optional[dict]:
        return self.prices.upsert(input)

    def bulk_import_prices(self, prices: list[dict[str, Any]]) -> int:
        return self.prices.bulk_upsert(prices)

    # ─── Raw Data Fetching (for agent / LLM inspection) ────────

    async def fetch_sold_listings_data(
        self, tcgplayer_id: str, condition: Optional[str] = None, finish: Optional[str] = None
    ) -> list[dict]:
        return await fetch_sold_listings(tcgplayer_id, condition, finish)

    async def fetch_active_listings_data(
        self, tcgplayer_id: str, condition: Optional[str] = None, finish: Optional[str] = None
    ) -> list[dict]:
        return await fetch_active_listings(tcgplayer_id, condition, finish)

    # ─── Card Auto-Ensure ─────────────────────────────────────

    async def _ensure_card_exists(self, tcgplayer_id: str) -> bool:
        existing = self.cards.get_by_id(tcgplayer_id)
        if existing:
            return True

        fetched = await fetch_card_info(tcgplayer_id)
        if not fetched:
            return False

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
        return True

    # ─── Pricing Workflows (fetch + algorithm + store) ─────────

    async def fetch_and_store_prices(self, tcgplayer_id: str) -> list[dict]:
        """Fetch all TCGplayer data for a card and price every variant it reveals."""
        await self._ensure_card_exists(tcgplayer_id)

        all_solds = await fetch_sold_listings(tcgplayer_id)
        all_listings = await fetch_active_listings(tcgplayer_id)

        today = datetime.now().date().isoformat()
        results: list[dict] = []

        # specialty_one is part of the key: 1st Edition and Unlimited are
        # separate products at very different prices, and pooling their sales
        # skews the sold signal toward the rarer printing.
        solds_by_key = _group_by(all_solds, _variant_key)
        listings_by_key = _group_by(all_listings, _variant_key)

        for key in set(solds_by_key) | set(listings_by_key):
            condition, finish, specialty_one = key.split("|")

            sku = self.skus.get_or_create(
                {
                    "card_id": tcgplayer_id,
                    "condition": condition,
                    "finish": finish,
                    "specialty_one": specialty_one,
                    "specialty_two": "None",
                    "qty": 0,
                }
            )

            # The SKU resolved above is always the plain one (get_or_create keys
            # on specialty_two='None'), so there is no error specialty to honour
            # here. Error variants are priced by the repricer's derived pass,
            # which flags them; the seller pipeline routes them via the tier
            # router's own specialty_two check.
            result = compute_price(
                inputs.from_raw(
                    listings_by_key.get(key, []),
                    solds_by_key.get(key, []),
                    condition,
                    finish,
                    specialty_one,
                    self.config,
                ),
                self.config,
            )

            results.append(store_price(self.prices, sku["sku_id"], today, result))

        return results

    async def compute_and_store_price(
        self, tcgplayer_id: str, condition: str, finish: str
    ) -> Optional[dict]:
        """Price a specific condition+finish for a card (targeted fetch)."""
        await self._ensure_card_exists(tcgplayer_id)

        # MINT has no TCGplayer data — fetch NM data instead
        fetch_condition = "NM" if condition == "MINT" else condition

        solds = await fetch_sold_listings(tcgplayer_id, fetch_condition, finish)
        listings = await fetch_active_listings(tcgplayer_id, fetch_condition, finish)

        sku = self.skus.get_or_create(
            {
                "card_id": tcgplayer_id,
                "condition": condition,
                "finish": finish,
                "specialty_one": "None",
                "specialty_two": "None",
                "qty": 0,
            }
        )

        # Always the plain SKU — see the note in fetch_and_store_prices.
        result = compute_price(
            inputs.from_raw(listings, solds, condition, finish, "None", self.config), self.config
        )

        if result.estimated_price is None:
            extrapolated = await self._try_extrapolate_from_other_conditions(
                tcgplayer_id, condition, finish
            )
            if extrapolated:
                result.estimated_price = extrapolated["price"]
                result.estimated_liquid_value = extrapolated["liquid_value"]
                result.confidence_percent = min(result.confidence_percent, 35)
                result.manual_check_necessary = True
                result.reasoning += (
                    f" Extrapolated from {extrapolated['source_condition']} data "
                    f"({extrapolated['source_price']:.2f})."
                )

        today = datetime.now().date().isoformat()
        return store_price(self.prices, sku["sku_id"], today, result)

    async def _try_extrapolate_from_other_conditions(
        self, tcgplayer_id: str, target_condition: str, target_finish: str
    ) -> Optional[dict]:
        other_skus = self.skus.search({"card_id": tcgplayer_id})
        preferred_sources = ["NM", "LP-NM", "LP", "MP-LP", "MP", "HP-MP", "HP", "DMG", "DM"]

        for source_condition in preferred_sources:
            if source_condition == target_condition:
                continue

            # Same printing only — a plain SKU must not be extrapolated from the
            # 1st Edition price, which is a different product at a different
            # price. (`compute_and_store_price` only ever targets the plain one.)
            source_sku = next(
                (
                    s
                    for s in other_skus
                    if s["condition"] == source_condition
                    and s["finish"] == target_finish
                    and (s["specialty_one"] or "None") == "None"
                ),
                None,
            )
            if not source_sku:
                continue

            source_price = self.prices.get_latest(source_sku["sku_id"])
            if not source_price or not source_price.get("estimated_price"):
                continue

            extrapolated = extrapolate_across_conditions(
                source_price["estimated_price"], source_condition, target_condition
            )
            if extrapolated is None:
                continue

            return {
                "price": extrapolated,
                "liquid_value": compute_liquid_value(extrapolated),
                "source_condition": source_condition,
                "source_price": source_price["estimated_price"],
            }

        return None

    # ─── Maintenance ───────────────────────────────────────────

    def delete_prices_for_sku(self, sku_id: int) -> int:
        return self.prices.delete_for_sku(sku_id)

    def prune_old_prices(self, before_date: str) -> int:
        return self.prices.delete_older_than(before_date)
