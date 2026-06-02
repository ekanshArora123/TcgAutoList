"""eBay Service — Manages eBay listing CRUD operations.

STUB — Not yet implemented. Will wrap the eBay Sell API (listing creation,
photo upload, category mapping, etc.). Ported from ebay/index.ts.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class ListingTemplate:
    """Template listing data ready for eBay posting."""

    title: str
    description: str
    price: float
    condition: str
    category: str
    photo_paths: list[str] = field(default_factory=list)


async def post_to_ebay(listing: ListingTemplate) -> str:
    """Post a listing to eBay.

    TODO: Implement when eBay integration is ready. For now, returns a stub ID.
    """
    print(
        f"[eBay STUB] Would post listing: {listing.title} @ ${listing.price:.2f} "
        f"({len(listing.photo_paths)} photos)"
    )
    return f"ebay-stub-{int(time.time() * 1000)}"
