"""card-server MCP entry point.

Combined collection, card info, and pricing data service exposed over MCP via
the Python SDK's FastMCP. Delegates to CollectionService / PricingService.
Ported from index.ts (which used the TypeScript @modelcontextprotocol/sdk).

Run:  python -m card_server.index
"""

from __future__ import annotations

import json
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

from .db import close_database, init_database
from .services.collection_service import CollectionService
from .services.pricing_service import PricingService

mcp = FastMCP("card-server")

_db = init_database()
collection = CollectionService(_db)
pricing = PricingService(_db)


def _ok(data: Any) -> str:
    return json.dumps(data, indent=2, default=str)


def _err(msg: str) -> str:
    return msg


# ─── Card Tools ──────────────────────────────────────────────


@mcp.tool()
async def get_card(id: str) -> str:
    """Get a card by TCGplayer ID (auto-fetches from TCGplayer if not in DB)."""
    card = await collection.get_card(id)
    return _ok(card) if card else _err(f"Card {id} not found")


@mcp.tool()
async def search_cards(
    query: Optional[str] = None,
    set_name: Optional[str] = None,
    rarity: Optional[str] = None,
    product_line: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> str:
    """Search cards by name, set, rarity, etc."""
    return _ok(
        collection.search_cards(
            {
                "query": query,
                "set_name": set_name,
                "rarity": rarity,
                "product_line": product_line,
                "limit": limit,
                "offset": offset,
            }
        )
    )


@mcp.tool()
async def update_card(id: str, **fields: Any) -> str:
    """Update card metadata."""
    card = collection.update_card({"id": id, **fields})
    return _ok(card) if card else _err(f"Card {id} not found")


@mcp.tool()
async def bulk_import_cards(cards: list[dict]) -> str:
    """Bulk import/update cards from an array."""
    return _ok({"imported": collection.bulk_import_cards(cards)})


@mcp.tool()
async def list_sets() -> str:
    """Get all distinct set names."""
    return _ok(collection.list_sets())


@mcp.tool()
async def list_rarities() -> str:
    """Get all distinct rarities."""
    return _ok(collection.list_rarities())


# ─── SKU Tools ───────────────────────────────────────────────


@mcp.tool()
async def resolve_sku(
    card_id: str,
    condition: str,
    finish: str = "Regular",
    specialty_one: str = "None",
    specialty_two: str = "None",
    qty: int = 0,
) -> str:
    """Get or create a SKU for a card+condition+finish+specialty combo."""
    return _ok(
        collection.resolve_sku(
            {
                "card_id": card_id,
                "condition": condition,
                "finish": finish,
                "specialty_one": specialty_one,
                "specialty_two": specialty_two,
                "qty": qty,
            }
        )
    )


@mcp.tool()
async def get_skus_for_card(card_id: str) -> str:
    """Get all SKU variants for a card."""
    return _ok(collection.get_skus_for_card(card_id))


@mcp.tool()
async def search_skus(
    card_id: Optional[str] = None,
    condition: Optional[str] = None,
    finish: Optional[str] = None,
    specialty_one: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> str:
    """Search SKUs with filters."""
    return _ok(
        collection.search_skus(
            {
                "card_id": card_id,
                "condition": condition,
                "finish": finish,
                "specialty_one": specialty_one,
                "limit": limit,
                "offset": offset,
            }
        )
    )


@mcp.tool()
async def list_conditions() -> str:
    """Get all card conditions in use."""
    return _ok(collection.list_conditions())


@mcp.tool()
async def list_specialties() -> str:
    """Get all specialty values in use."""
    return _ok(collection.list_specialties())


# ─── Inventory Tools ─────────────────────────────────────────


@mcp.tool()
async def add_to_inventory(
    card_id: str,
    condition: str,
    finish: Optional[str] = None,
    specialty_one: Optional[str] = None,
    specialty_two: Optional[str] = None,
    pricing_condition: Optional[str] = None,
    pricing_finish: Optional[str] = None,
    qty: Optional[int] = None,
    tags: Optional[str] = None,
) -> str:
    """Add a card to your physical inventory (resolves SKU automatically)."""
    return _ok(
        collection.add_to_inventory(
            {
                "card_id": card_id,
                "condition": condition,
                "finish": finish,
                "specialty_one": specialty_one,
                "specialty_two": specialty_two,
                "pricing_condition": pricing_condition,
                "pricing_finish": pricing_finish,
                "qty": qty,
                "tags": tags,
            }
        )
    )


@mcp.tool()
async def get_inventory_item(inventory_id: int) -> str:
    """Get an inventory item with full card/price details."""
    item = collection.get_inventory_detail(inventory_id)
    return _ok(item) if item else _err(f"Inventory item {inventory_id} not found")


@mcp.tool()
async def get_next_unlisted() -> str:
    """Get the next unlisted card for the listing workflow."""
    item = collection.get_next_unlisted()
    return _ok(item) if item else _err("No unlisted cards remaining")


@mcp.tool()
async def search_inventory(
    sku_id: Optional[int] = None,
    status: Optional[str] = None,
    tags_contain: Optional[str] = None,
    card_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> str:
    """Search inventory with filters."""
    return _ok(
        collection.search_inventory(
            {
                "sku_id": sku_id,
                "status": status,
                "tags_contain": tags_contain,
                "card_id": card_id,
                "limit": limit,
                "offset": offset,
            }
        )
    )


@mcp.tool()
async def search_inventory_detailed(
    status: Optional[str] = None,
    card_id: Optional[str] = None,
    limit: Optional[int] = None,
    offset: Optional[int] = None,
) -> str:
    """Search inventory with full card/price details."""
    return _ok(
        collection.search_inventory_with_details(
            {"status": status, "card_id": card_id, "limit": limit or 50, "offset": offset or 0}
        )
    )


@mcp.tool()
async def update_inventory(inventory_id: int, **fields: Any) -> str:
    """Update an inventory item."""
    item = collection.update_inventory({"inventory_id": inventory_id, **fields})
    return _ok(item) if item else _err(f"Inventory item {inventory_id} not found")


@mcp.tool()
async def mark_as_listed(inventory_id: int, ebay_listing_id: str) -> str:
    """Mark an inventory item as listed on eBay."""
    item = collection.mark_as_listed(inventory_id, ebay_listing_id)
    return _ok(item) if item else _err(f"Inventory item {inventory_id} not found")


@mcp.tool()
async def mark_as_sold(inventory_id: int) -> str:
    """Mark an inventory item as sold."""
    item = collection.mark_as_sold(inventory_id)
    return _ok(item) if item else _err(f"Inventory item {inventory_id} not found")


@mcp.tool()
async def update_status(inventory_id: int, status: str) -> str:
    """Update inventory item status."""
    item = collection.update_status(inventory_id, status)
    return _ok(item) if item else _err(f"Inventory item {inventory_id} not found")


@mcp.tool()
async def set_pricing_sku(inventory_id: int, pricing_sku_id: Optional[int]) -> str:
    """Override the pricing SKU for borderline condition cards."""
    item = collection.set_pricing_sku(inventory_id, pricing_sku_id)
    return _ok(item) if item else _err(f"Inventory item {inventory_id} not found")


@mcp.tool()
async def add_tag(inventory_id: int, tag: str) -> str:
    """Add a hidden tag to an inventory item."""
    item = collection.add_tag(inventory_id, tag)
    return _ok(item) if item else _err(f"Inventory item {inventory_id} not found")


@mcp.tool()
async def remove_tag(inventory_id: int, tag: str) -> str:
    """Remove a tag from an inventory item."""
    item = collection.remove_tag(inventory_id, tag)
    return _ok(item) if item else _err(f"Inventory item {inventory_id} not found")


@mcp.tool()
async def collection_stats() -> str:
    """Get collection statistics (counts by status, total value)."""
    return _ok(collection.get_stats())


# ─── Pricing Tools ───────────────────────────────────────────


@mcp.tool()
async def get_latest_price(sku_id: int) -> str:
    """Get the most recent price for a SKU."""
    price = pricing.get_latest_price(sku_id)
    return _ok(price) if price else _err(f"No price found for SKU {sku_id}")


@mcp.tool()
async def get_price_for_item(inventory_id: int) -> str:
    """Get latest price for an inventory item (respects pricing SKU override)."""
    price = pricing.get_latest_price_for_item(inventory_id)
    return _ok(price) if price else _err(f"No price found for inventory item {inventory_id}")


@mcp.tool()
async def get_price_history(sku_id: int, limit: Optional[int] = None, offset: Optional[int] = None) -> str:
    """Get price history for a SKU."""
    return _ok(pricing.get_price_history(sku_id, limit or 50, offset or 0))


@mcp.tool()
async def search_prices(
    sku_id: Optional[int] = None,
    card_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    manual_check_only: Optional[bool] = None,
    limit: int = 50,
    offset: int = 0,
) -> str:
    """Search prices with filters."""
    return _ok(
        pricing.search_prices(
            {
                "sku_id": sku_id,
                "card_id": card_id,
                "date_from": date_from,
                "date_to": date_to,
                "manual_check_only": manual_check_only,
                "limit": limit,
                "offset": offset,
            }
        )
    )


@mcp.tool()
async def compare_prices(card_id: str) -> str:
    """Compare prices for a card across all conditions."""
    return _ok(pricing.compare_prices_across_conditions(card_id))


@mcp.tool()
async def record_price(sku_id: int, calculation_date: str, **fields: Any) -> str:
    """Manually record a price estimate for a SKU."""
    return _ok(pricing.record_price({"sku_id": sku_id, "calculation_date": calculation_date, **fields}))


@mcp.tool()
async def bulk_import_prices(prices: list[dict]) -> str:
    """Bulk import price records."""
    return _ok({"imported": pricing.bulk_import_prices(prices)})


@mcp.tool()
async def get_pending_manual_checks() -> str:
    """Get prices flagged for manual review."""
    return _ok(pricing.get_pending_manual_checks())


@mcp.tool()
async def mark_price_checked(sku_id: int, calculation_date: str) -> str:
    """Mark a price as manually verified."""
    updated = pricing.mark_price_checked(sku_id, calculation_date)
    return _ok({"success": True}) if updated else _err("Price record not found")


# ─── TCGplayer Fetch Tools (active data gathering) ───────────


@mcp.tool()
async def fetch_prices(tcgplayer_id: str) -> str:
    """Fetch fresh prices from TCGplayer for all conditions and store them."""
    prices = await pricing.fetch_and_store_prices(tcgplayer_id)
    return _ok({"fetched": len(prices), "prices": prices})


@mcp.tool(name="compute_price")
async def compute_price_tool(tcgplayer_id: str, condition: str, finish: str) -> str:
    """Fetch data and compute price for a specific card+condition+finish."""
    result = await pricing.compute_and_store_price(tcgplayer_id, condition, finish)
    return _ok(result) if result else _err("Could not compute price — no data available")


@mcp.tool(name="fetch_sold_listings")
async def fetch_sold_listings_tool(
    tcgplayer_id: str, condition: Optional[str] = None, finish: Optional[str] = None
) -> str:
    """Fetch recent sold listings from TCGplayer for price analysis."""
    return _ok(await pricing.fetch_sold_listings_data(tcgplayer_id, condition, finish))


@mcp.tool(name="fetch_active_listings")
async def fetch_active_listings_tool(
    tcgplayer_id: str, condition: Optional[str] = None, finish: Optional[str] = None
) -> str:
    """Fetch current for-sale listings from TCGplayer for competitive pricing."""
    return _ok(await pricing.fetch_active_listings_data(tcgplayer_id, condition, finish))


def main() -> None:
    try:
        mcp.run(transport="stdio")
    finally:
        close_database()


if __name__ == "__main__":
    main()
