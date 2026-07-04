"""Pydantic models + shared type aliases for the card-server data layer.

Ported from types.ts (Zod schemas). These models mirror the DB schema and the
input shapes the data service accepts. CRUD helpers generally return plain dicts
(sqlite Row -> dict); these models document the shapes and validate inputs at the
service / MCP boundary.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

# ─── Enums / Shared ──────────────────────────────────────────

CardCondition = Literal[
    "MINT", "NM", "LP-NM", "LP", "MP-LP", "MP", "HP-MP", "HP", "DM", "DMG"
]

CardFinish = Literal["Regular", "Holo", "Reverse-Holo"]

InventoryStatus = Literal["unlisted", "photo_requested", "listed", "skipped", "sold"]

CARD_CONDITIONS: tuple[str, ...] = (
    "MINT", "NM", "LP-NM", "LP", "MP-LP", "MP", "HP-MP", "HP", "DM", "DMG",
)
CARD_FINISHES: tuple[str, ...] = ("Regular", "Holo", "Reverse-Holo")
INVENTORY_STATUSES: tuple[str, ...] = (
    "unlisted", "photo_requested", "listed", "skipped", "sold",
)


# ─── Cards ───────────────────────────────────────────────────


class Card(BaseModel):
    id: str
    card_name: str
    set_name: Optional[str] = None
    product_line: str = "Pokemon"
    card_type: Optional[str] = None
    visual_layout: Optional[str] = None
    rarity: Optional[str] = None
    card_number: Optional[str] = None
    product_type: Optional[str] = None
    era: Optional[str] = None
    set_type: Optional[str] = None


class CreateCard(BaseModel):
    id: str
    card_name: str
    set_name: Optional[str] = None
    product_line: str = "Pokemon"
    card_type: Optional[str] = None
    visual_layout: Optional[str] = None
    rarity: Optional[str] = None
    card_number: Optional[str] = None
    product_type: Optional[str] = None
    era: Optional[str] = None
    set_type: Optional[str] = None


class UpdateCard(BaseModel):
    id: str
    card_name: Optional[str] = None
    set_name: Optional[str] = None
    product_line: Optional[str] = None
    card_type: Optional[str] = None
    visual_layout: Optional[str] = None
    rarity: Optional[str] = None
    card_number: Optional[str] = None
    product_type: Optional[str] = None
    era: Optional[str] = None
    set_type: Optional[str] = None


class SearchCardsInput(BaseModel):
    query: Optional[str] = None
    set_name: Optional[str] = None
    rarity: Optional[str] = None
    product_line: Optional[str] = None
    limit: int = 50
    offset: int = 0


# ─── SKUs ────────────────────────────────────────────────────


class Sku(BaseModel):
    sku_id: int
    card_id: str
    condition: CardCondition
    finish: CardFinish
    specialty_one: str = "None"
    specialty_two: str = "None"
    qty: int = 0
    latest_calc_date: Optional[str] = None


class CreateSku(BaseModel):
    card_id: str
    condition: CardCondition
    finish: CardFinish
    specialty_one: str = "None"
    specialty_two: str = "None"
    qty: int = 0


class SearchSkusInput(BaseModel):
    card_id: Optional[str] = None
    condition: Optional[CardCondition] = None
    finish: Optional[CardFinish] = None
    specialty_one: Optional[str] = None
    limit: int = 50
    offset: int = 0


# ─── Inventory ───────────────────────────────────────────────


class InventoryItem(BaseModel):
    inventory_id: int
    sku_id: int
    pricing_sku_id: Optional[int] = None
    qty: int = 1
    tags: Optional[str] = None
    status: InventoryStatus = "unlisted"
    front_photo_path: Optional[str] = None
    back_photo_path: Optional[str] = None
    ebay_listing_id: Optional[str] = None
    listed_at: Optional[str] = None


class CreateInventoryItem(BaseModel):
    sku_id: int
    pricing_sku_id: Optional[int] = None
    qty: int = 1
    tags: Optional[str] = None
    status: InventoryStatus = "unlisted"


class UpdateInventoryItem(BaseModel):
    inventory_id: int
    sku_id: Optional[int] = None
    pricing_sku_id: Optional[int] = None
    qty: Optional[int] = None
    tags: Optional[str] = None
    status: Optional[InventoryStatus] = None
    front_photo_path: Optional[str] = None
    back_photo_path: Optional[str] = None
    ebay_listing_id: Optional[str] = None
    listed_at: Optional[str] = None


class SearchInventoryInput(BaseModel):
    sku_id: Optional[int] = None
    status: Optional[InventoryStatus] = None
    tags_contain: Optional[str] = None
    card_id: Optional[str] = None
    limit: int = 50
    offset: int = 0


# ─── Prices ──────────────────────────────────────────────────


class Price(BaseModel):
    sku_id: int
    calculation_date: str
    estimated_price: Optional[float] = None
    estimated_liquid_value: Optional[float] = None
    confidence_percent: Optional[float] = None
    manual_check_necessary: bool = False
    manually_checked: bool = False
    algorithm_version: Optional[str] = None
    estimated_low_price: Optional[float] = None
    estimated_high_price: Optional[float] = None
    estimated_low_price_liquid: Optional[float] = None
    estimated_high_price_liquid: Optional[float] = None


CreatePrice = Price


class SearchPricesInput(BaseModel):
    sku_id: Optional[int] = None
    card_id: Optional[str] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    manual_check_only: Optional[bool] = None
    limit: int = 50
    offset: int = 0


# ─── Joined / Composite Types ────────────────────────────────


class InventoryDetail(InventoryItem):
    """Full inventory item with card info, SKU details, and latest price."""

    card_id: str
    card_name: str
    set_name: Optional[str] = None
    condition: CardCondition
    finish: CardFinish
    specialty_one: str = "None"
    specialty_two: str = "None"
    rarity: Optional[str] = None
    card_number: Optional[str] = None
    estimated_price: Optional[float] = None


# ─── Graded cards (parallel to skus / inventory / prices) ────
# Graded slabs are their own parallel chain; the raw models above are untouched.

# Grading companies. Free-form is allowed at the DB level (grading_company is
# TEXT), but these are the recognized values.
GradingCompany = Literal["PSA", "BGS", "CGC", "SGC", "ACE", "TAG", "Other"]

GRADING_COMPANIES: tuple[str, ...] = ("PSA", "BGS", "CGC", "SGC", "ACE", "TAG", "Other")


class GradedSku(BaseModel):
    graded_sku_id: int
    card_id: str
    finish: CardFinish = "Regular"
    specialty_one: str = "None"
    grading_company: str
    grade: float
    qty: int = 0
    latest_calc_date: Optional[str] = None


class CreateGradedSku(BaseModel):
    card_id: str
    finish: CardFinish = "Regular"
    specialty_one: str = "None"
    grading_company: str
    grade: float
    qty: int = 0


class SearchGradedSkusInput(BaseModel):
    card_id: Optional[str] = None
    finish: Optional[CardFinish] = None
    grading_company: Optional[str] = None
    grade: Optional[float] = None
    limit: int = 50
    offset: int = 0


class GradedInventoryItem(BaseModel):
    graded_inventory_id: int
    graded_sku_id: int
    cert_id: Optional[str] = None
    qty: int = 1
    tags: Optional[str] = None
    status: InventoryStatus = "unlisted"
    front_photo_path: Optional[str] = None
    back_photo_path: Optional[str] = None
    ebay_listing_id: Optional[str] = None
    listed_at: Optional[str] = None


class CreateGradedInventoryItem(BaseModel):
    graded_sku_id: int
    cert_id: Optional[str] = None
    qty: int = 1
    tags: Optional[str] = None
    status: InventoryStatus = "unlisted"


class UpdateGradedInventoryItem(BaseModel):
    graded_inventory_id: int
    graded_sku_id: Optional[int] = None
    cert_id: Optional[str] = None
    qty: Optional[int] = None
    tags: Optional[str] = None
    status: Optional[InventoryStatus] = None
    front_photo_path: Optional[str] = None
    back_photo_path: Optional[str] = None
    ebay_listing_id: Optional[str] = None
    listed_at: Optional[str] = None


class SearchGradedInventoryInput(BaseModel):
    graded_sku_id: Optional[int] = None
    status: Optional[InventoryStatus] = None
    card_id: Optional[str] = None
    grading_company: Optional[str] = None
    limit: int = 50
    offset: int = 0


class GradedPrice(BaseModel):
    graded_sku_id: int
    calculation_date: str
    estimated_price: Optional[float] = None
    estimated_liquid_value: Optional[float] = None
    confidence_percent: Optional[float] = None
    manual_check_necessary: bool = False
    manually_checked: bool = False
    algorithm_version: Optional[str] = None
    estimated_low_price: Optional[float] = None
    estimated_high_price: Optional[float] = None
    estimated_low_price_liquid: Optional[float] = None
    estimated_high_price_liquid: Optional[float] = None


CreateGradedPrice = GradedPrice


class GradedInventoryDetail(GradedInventoryItem):
    """Full graded slab with card info, graded-SKU details, and latest price."""

    card_id: str
    card_name: str
    set_name: Optional[str] = None
    finish: CardFinish
    specialty_one: str = "None"
    grading_company: str
    grade: float
    rarity: Optional[str] = None
    card_number: Optional[str] = None
    estimated_price: Optional[float] = None
