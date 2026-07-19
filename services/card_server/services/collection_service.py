"""CollectionService — High-level operations for managing the card collection.

Composes CRUD helpers + TCGplayer fetchers into workflow-level operations.
One of the two service classes coded callers use. Ported from collectionService.ts.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any, Optional

from ..helpers.crud.cards import CardsHelper
from ..helpers.crud.graded_inventory import GradedInventoryHelper
from ..helpers.crud.graded_prices import GradedPricesHelper
from ..helpers.crud.graded_skus import GradedSkusHelper
from ..helpers.crud.inventory import InventoryHelper
from ..helpers.crud.prices import PricesHelper
from ..helpers.crud.skus import SkusHelper
from ..helpers.grading import registry as grading_registry
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

    def set_condition_qty(
        self,
        card_id: str,
        condition: str,
        qty: int,
        finish: str = "Regular",
        specialty_one: str = "None",
        specialty_two: str = "None",
    ) -> Optional[int]:
        """Set the owned (untagged) quantity of one variant+condition to `qty`.

        Resolves — creating if needed — the SKU for this exact
        (card_id, condition, finish, specialties), then drives the count through
        `inventory.set_untagged_qty`. Because it get_or_creates the SKU, it can add
        a condition that isn't owned yet (qty 0 → N), not just edit existing ones.
        Returns the new owned quantity, or None if the card_id is unknown.

        The composable seam for editing owned counts by condition — the detail-page
        editor and any future add/bulk-edit flow share this instead of duplicating
        SKU resolution + inventory math.
        """
        if not self.cards.get_by_id(card_id):
            return None
        sku = self.skus.get_or_create(
            {
                "card_id": card_id,
                "condition": condition,
                "finish": finish or "Regular",
                "specialty_one": specialty_one or "None",
                "specialty_two": specialty_two or "None",
                "qty": 0,
            }
        )
        return self.inventory.set_untagged_qty(sku["sku_id"], qty)

    def set_condition_quantities(
        self,
        card_id: str,
        quantities: dict[str, int],
        finish: str = "Regular",
        specialty_one: str = "None",
        specialty_two: str = "None",
    ) -> Optional[dict[str, int]]:
        """Batch form of `set_condition_qty`: apply a {condition: qty} map for one
        variant in a single call. Returns {condition: new_qty}, or None if the
        card_id is unknown."""
        if not self.cards.get_by_id(card_id):
            return None
        return {
            condition: self.set_condition_qty(
                card_id, condition, qty, finish, specialty_one, specialty_two
            )
            for condition, qty in quantities.items()
        }

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

    def set_graded_slab_tag(
        self, graded_inventory_id: int, tag: str, present: bool
    ) -> Optional[dict]:
        """Add or remove a tag on one graded slab (e.g. the 'to_crack' mark).

        Slab-level, cert-specific — acts only on this one physical slab, never the
        grade class or other slabs. Tags reuse the shared comma-separated column
        (see helpers.crud.tags), so any future slab tag flows through here too."""
        if present:
            return self.graded_inventory.add_tag(graded_inventory_id, tag)
        return self.graded_inventory.remove_tag(graded_inventory_id, tag)

    async def add_graded_by_cert(
        self, cert_id: str, grading_company: str = "PSA", card_id: Optional[str] = None
    ) -> Optional[dict]:
        """Add a graded slab by cert number: fetch identity + population from the
        grading company, upsert the graded SKU (identity from the grader), and
        create the physical slab. `card_id` is the OPTIONAL, manually-entered
        TCGplayer link (deferred feature — used only so the raw image/pricing can
        attach later). Returns the new slab's detail, or None if the cert isn't
        found. Re-adding a known cert's spec refreshes its population.
        """
        provider = grading_registry.get_provider(grading_company)
        if provider is None:
            raise ValueError(f"Unsupported grading company: {grading_company}")

        info = await provider.fetch_cert(cert_id)
        if not info:
            return None  # cert not found at the grader

        # Optional manual TCGplayer link. Prefer full metadata via get_card; but a
        # valid product id can have a CDN image even when the search API returns no
        # metadata — and the link is needed for the image to display. So if get_card
        # fails, still peg the id when it has a real image (a minimal cards row from
        # the grader's own fields satisfies the FK; the graded->raw converter can
        # enrich it later). Only ids with a real image are pegged, so a typo'd id
        # doesn't pollute the cards table.
        linked_card_id: Optional[str] = (
            await self._resolve_card_link(str(card_id), info) if card_id else None
        )

        grade = info.get("grade")
        sku_params = {
            **{k: info.get(k) for k in (
                "grading_company", "grade_label", "grader_spec_id", "card_year",
                "card_set", "card_category", "card_number", "card_subject",
                "card_variety", "card_language", "population", "population_higher",
            )},
            "grade": grade if grade is not None else -1.0,  # awkward/unparseable → -1 sentinel
            "pop_fetched_at": date.today().isoformat(),
            "card_id": linked_card_id,
        }
        graded_sku = self.graded_skus.get_or_create(sku_params)

        # Refresh pop (and attach the link if newly provided) on an existing SKU.
        updates: dict[str, Any] = {
            "graded_sku_id": graded_sku["graded_sku_id"],
            "population": sku_params["population"],
            "population_higher": sku_params["population_higher"],
            "pop_fetched_at": sku_params["pop_fetched_at"],
        }
        if linked_card_id and not graded_sku.get("card_id"):
            updates["card_id"] = linked_card_id
        self.graded_skus.update(updates)

        stored_cert = info.get("cert_id") or str(cert_id)
        # A cert number is a single physical slab, so never insert a duplicate:
        # reuse the existing row for this cert (re-pointing its graded_sku if the
        # variant changed). This makes re-adding a cert idempotent — it refreshes
        # pop/images below instead of creating a second copy.
        existing = self.graded_inventory.get_by_cert(stored_cert)
        if existing:
            inv_id = existing["graded_inventory_id"]
            if existing.get("graded_sku_id") != graded_sku["graded_sku_id"]:
                self.graded_inventory.update(
                    {"graded_inventory_id": inv_id, "graded_sku_id": graded_sku["graded_sku_id"]}
                )
        else:
            inv_id = self.graded_inventory.create(
                {
                    "graded_sku_id": graded_sku["graded_sku_id"],
                    "cert_id": stored_cert,
                    "qty": 1,
                }
            )["graded_inventory_id"]

        # Best-effort: download the slab's front/back images (their URLs are only
        # available from the scrape). Lazy import + swallow errors so a missing
        # image dep or a failed download never fails the add.
        try:
            from ..scripts.fetch_images import store_graded_images

            store_graded_images(
                stored_cert, info.get("image_front_url"), info.get("image_back_url")
            )
        except Exception:
            pass

        return self.graded_inventory.get_detail_by_id(inv_id)

    async def _resolve_card_link(self, card_id: str, info: dict[str, Any]) -> Optional[str]:
        """Resolve a user-provided TCGplayer id to a linkable card_id for a graded
        slab. Returns the id to link, or None if it can't be used.

        1. get_card → full metadata (creates the cards row). Best case.
        2. else, if the id has a real image on TCGplayer's CDN, peg it: upsert a
           minimal cards row (name/set/number from the grader's own fields) so the
           FK holds and the image displays. (Pre-fetching also caches it.)
        3. else (no metadata AND no image → likely a bad id) → None, so we don't
           pollute the cards table with a junk id.
        """
        card_id = str(card_id).strip()
        if not card_id:
            return None
        card = await self.get_card(card_id)
        if card:
            return card["id"]
        try:
            from ..scripts.fetch_images import fetch_card_image

            if fetch_card_image(card_id, retries=1, rate_limit_pause=0):
                self.cards.upsert(
                    {
                        "id": card_id,
                        "card_name": info.get("card_subject") or "Unknown",
                        "set_name": info.get("card_set"),
                        "card_number": info.get("card_number"),
                        "product_line": "Pokemon",
                    }
                )
                return card_id
        except Exception:
            pass
        return None

    def link_graded_to_card(self, graded_sku_id: int, card_id: Optional[str]) -> Optional[dict]:
        """Isolated seam for the future graded->raw converter to set the TCGplayer link."""
        return self.graded_skus.link_card(graded_sku_id, card_id)

    async def set_graded_link_by_cert(
        self, cert_id: str, card_id: Optional[str]
    ) -> Optional[dict]:
        """Manually set/clear the TCGplayer link for the graded card a cert belongs
        to. Interim manual editor for `graded_skus.card_id` (the deferred graded->raw
        mapper will automate this) — reachable from the slab detail page.

        The link identifies the *raw card*, which is the same across every grade of
        one card, so it's applied to the whole spec group (all graded_skus sharing
        this company + grader spec), keeping grades consistent. A blank id unlinks.
        A non-blank id is validated + resolved through the same `_resolve_card_link`
        path the add flow uses (creates the `cards` row so the FK holds and the
        image/price show); an unusable id raises ValueError. Returns
        {"card_id": <resolved or None>}, or None if the cert isn't owned."""
        slab = self.graded_inventory.get_by_cert(cert_id)
        if not slab:
            return None
        sku = self.graded_skus.get_by_id(slab["graded_sku_id"])
        if not sku:
            return None

        # Every grade of this card within the company (falls back to just this sku
        # when there's no grader spec).
        if sku.get("grader_spec_id"):
            group = self.graded_skus.get_by_spec(sku["grading_company"], sku["grader_spec_id"])
        else:
            group = [sku]

        resolved: Optional[str] = None
        if card_id and str(card_id).strip():
            # Reuse the add-time resolver; the sku's grader fields feed its stub
            # fallback (card_subject/card_set/card_number).
            resolved = await self._resolve_card_link(str(card_id).strip(), sku)
            if resolved is None:
                raise ValueError(
                    f"TCGplayer id {str(card_id).strip()} couldn't be linked "
                    "(no card or image found)."
                )

        for g in group:
            self.graded_skus.link_card(g["graded_sku_id"], resolved)
        return {"card_id": resolved}

    def supported_grading_companies(self) -> list[str]:
        return grading_registry.supported_companies()
