"""CollectionService — High-level operations for managing the card collection.

Composes CRUD helpers + TCGplayer fetchers into workflow-level operations.
One of the two service classes coded callers use. Ported from collectionService.ts.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Optional

from ..helpers.crud.cards import CardsHelper
from ..helpers.crud.graded_inventory import GradedInventoryHelper
from ..helpers.crud.graded_prices import GradedPricesHelper
from ..helpers.crud.graded_skus import GradedSkusHelper
from ..helpers.crud.inventory import InventoryHelper
from ..helpers.crud.prices import PricesHelper
from ..helpers.crud.skus import SkusHelper
from ..helpers.tcgplayer.fetch_card_info import fetch_card_info


class CollectionService:
    def __init__(self, db: sqlite3.Connection):
        self.db = db
        self.cards = CardsHelper(db)
        self.skus = SkusHelper(db)
        self.inventory = InventoryHelper(db)
        self.prices = PricesHelper(db)
        # Graded cards: parallel chain, separate helpers (raw helpers unchanged).
        self.graded_skus = GradedSkusHelper(db)
        self.graded_inventory = GradedInventoryHelper(db)
        self.graded_prices = GradedPricesHelper(db)

    # ─── Card Lookups (with auto-fetch) ──────────────────────

    async def get_card(self, tcgplayer_id: str) -> Optional[dict]:
        """Get a card by TCGplayer ID. If not in DB, fetch from TCGplayer and store it."""
        existing = self.cards.get_by_id(tcgplayer_id)
        if existing:
            return existing

        fetched = await fetch_card_info(tcgplayer_id)
        if not fetched:
            return None

        metadata = fetched["metadata"]
        price_info = fetched["price_info"]

        card = self.cards.upsert(
            {
                "id": metadata["tcgplayer_id"],
                "card_name": metadata["card_name"],
                "set_name": metadata["set_name"],
                "product_line": metadata["product_line"],
                "card_type": metadata["card_type"],
                "visual_layout": None,
                "rarity": metadata["rarity"],
                "card_number": metadata["card_number"],
                "product_type": None,
                "era": None,
                "set_type": None,
            }
        )

        for variant in price_info["available_conditions"]:
            self.skus.get_or_create(
                {
                    "card_id": card["id"],
                    "condition": variant["condition"],
                    "finish": variant["finish"],
                    "specialty_one": "None",
                    "specialty_two": "None",
                    "qty": 0,
                }
            )

        return card

    def get_card_local(self, tcgplayer_id: str) -> Optional[dict]:
        """Direct DB-only card lookup (no fetch)."""
        return self.cards.get_by_id(tcgplayer_id)

    def search_cards(self, filters: dict[str, Any]) -> list[dict]:
        return self.cards.search(filters)

    def update_card(self, input: dict[str, Any]) -> Optional[dict]:
        return self.cards.update(input)

    def bulk_import_cards(self, cards: list[dict[str, Any]]) -> int:
        return self.cards.bulk_upsert(cards)

    def list_sets(self) -> list[str]:
        return self.cards.get_all_set_names()

    def list_rarities(self) -> list[str]:
        return self.cards.get_all_rarities()

    # ─── SKU Operations ──────────────────────────────────────

    def resolve_sku(self, input: dict[str, Any]) -> dict:
        return self.skus.get_or_create(input)

    def get_skus_for_card(self, card_id: str) -> list[dict]:
        return self.skus.get_by_card_id(card_id)

    def search_skus(self, filters: dict[str, Any]) -> list[dict]:
        return self.skus.search(filters)

    def list_conditions(self) -> list[str]:
        return self.skus.get_all_conditions()

    def list_specialties(self) -> list[str]:
        return self.skus.get_all_specialties()

    # ─── Inventory Operations ────────────────────────────────

    def add_to_inventory(self, params: dict[str, Any]) -> Optional[dict]:
        """Add a card to inventory, resolving the SKU automatically."""
        sku = self.skus.get_or_create(
            {
                "card_id": params["card_id"],
                "condition": params["condition"],
                "finish": params.get("finish") or "Regular",
                "specialty_one": params.get("specialty_one") or "None",
                "specialty_two": params.get("specialty_two") or "None",
                "qty": 0,
            }
        )

        pricing_sku_id: Optional[int] = None
        if params.get("pricing_condition") or params.get("pricing_finish"):
            pricing_sku = self.skus.get_or_create(
                {
                    "card_id": params["card_id"],
                    "condition": params.get("pricing_condition") or params["condition"],
                    "finish": params.get("pricing_finish") or params.get("finish") or "Regular",
                    "specialty_one": params.get("specialty_one") or "None",
                    "specialty_two": params.get("specialty_two") or "None",
                    "qty": 0,
                }
            )
            pricing_sku_id = pricing_sku["sku_id"]

        return self.inventory.create(
            {
                "sku_id": sku["sku_id"],
                "pricing_sku_id": pricing_sku_id,
                "qty": params.get("qty") or 1,
                "tags": params.get("tags"),
                "status": "unlisted",
            }
        )

    def get_inventory_detail(self, inventory_id: int) -> Optional[dict]:
        return self.inventory.get_detail_by_id(inventory_id)

    def get_next_unlisted(self) -> Optional[dict]:
        return self.inventory.get_next_unlisted()

    def search_inventory(self, filters: dict[str, Any]) -> list[dict]:
        return self.inventory.search(filters)

    def search_inventory_with_details(self, filters: dict[str, Any]) -> list[dict]:
        return self.inventory.search_with_details(filters)

    def update_inventory(self, input: dict[str, Any]) -> Optional[dict]:
        return self.inventory.update(input)

    def mark_as_listed(self, inventory_id: int, ebay_listing_id: str) -> Optional[dict]:
        return self.inventory.mark_as_listed(inventory_id, ebay_listing_id)

    def mark_as_sold(self, inventory_id: int) -> Optional[dict]:
        return self.inventory.mark_as_sold(inventory_id)

    def update_status(self, inventory_id: int, status: str) -> Optional[dict]:
        return self.inventory.update_status(inventory_id, status)

    def set_photos(self, inventory_id: int, front_photo_path: str, back_photo_path: str) -> Optional[dict]:
        return self.inventory.set_photos(inventory_id, front_photo_path, back_photo_path)

    def set_pricing_sku(self, inventory_id: int, pricing_sku_id: Optional[int]) -> Optional[dict]:
        return self.inventory.set_pricing_sku(inventory_id, pricing_sku_id)

    def add_tag(self, inventory_id: int, tag: str) -> Optional[dict]:
        return self.inventory.add_tag(inventory_id, tag)

    def remove_tag(self, inventory_id: int, tag: str) -> Optional[dict]:
        return self.inventory.remove_tag(inventory_id, tag)

    def get_stats(self) -> dict[str, Any]:
        return self.inventory.get_stats()

    def delete_inventory_item(self, inventory_id: int) -> bool:
        return self.inventory.delete(inventory_id)

    def bulk_add_to_inventory(self, items: list[dict[str, Any]]) -> int:
        return self.inventory.bulk_create(items)

    # ─── Graded cards (parallel to the raw operations above) ──

    def add_graded_to_inventory(self, params: dict[str, Any]) -> Optional[dict]:
        """Add a physical graded slab: resolve its graded SKU (company+grade) and
        create a graded_inventory row carrying the cert id."""
        graded_sku = self.graded_skus.get_or_create(
            {
                "card_id": params["card_id"],
                "finish": params.get("finish") or "Regular",
                "specialty_one": params.get("specialty_one") or "None",
                "grading_company": params["grading_company"],
                "grade": params["grade"],
                "qty": 0,
            }
        )
        return self.graded_inventory.create(
            {
                "graded_sku_id": graded_sku["graded_sku_id"],
                "cert_id": params.get("cert_id"),
                "qty": params.get("qty") or 1,
                "tags": params.get("tags"),
                "status": "unlisted",
            }
        )

    def resolve_graded_sku(self, input: dict[str, Any]) -> dict:
        return self.graded_skus.get_or_create(input)

    def get_graded_skus_for_card(self, card_id: str) -> list[dict]:
        return self.graded_skus.get_by_card_id(card_id)

    def search_graded_skus(self, filters: dict[str, Any]) -> list[dict]:
        return self.graded_skus.search(filters)

    def list_grading_companies(self) -> list[str]:
        return self.graded_skus.get_all_companies()

    def get_graded_inventory_detail(self, graded_inventory_id: int) -> Optional[dict]:
        return self.graded_inventory.get_detail_by_id(graded_inventory_id)

    def search_graded_inventory_with_details(self, filters: dict[str, Any]) -> list[dict]:
        return self.graded_inventory.search_with_details(filters)

    def delete_graded_inventory_item(self, graded_inventory_id: int) -> bool:
        return self.graded_inventory.delete(graded_inventory_id)
