"""Shared types for the parent project (orchestrator, telegram, agent).

Re-exports card_server data models for convenience and defines types specific to
the orchestrator and telegram integration. Ported from src/types.ts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

# Re-export card_server data models that the orchestrator uses.
from card_server.types import (  # noqa: F401
    Card,
    Sku,
    InventoryItem,
    InventoryDetail,
    Price,
)

# ─── Telegram Events ─────────────────────────────────────────

PhotoSide = Literal["front", "back"]

# Events emitted by the Telegram bot to the orchestrator are plain dicts with a
# "type" key, mirroring the TS discriminated union:
#   {"type": "command", "command": "next"|"status"|"skip"|"pause", "chatId": int}
#   {"type": "photo",    "filePath": str, "chatId": int}
#   {"type": "text",     "message": str, "chatId": int}
#   {"type": "callback", "data": str, "chatId": int, "messageId": int}
TelegramEvent = dict


# ─── Orchestrator State ──────────────────────────────────────


@dataclass
class CardWorkflowState:
    """The state of the current card being processed by the orchestrator."""

    inventory_id: int
    inventory_detail: dict
    # Workflow step: idle | pricing | tier_routing | awaiting_photo | building_listing | posting | done
    step: str = "idle"
    tier: Optional[int] = None
    price: Optional[dict] = None
    photo_path: Optional[str] = None
    back_photo_path: Optional[str] = None
    awaiting_side: Optional[str] = None
    confidence: Optional[float] = None


# ─── Tier Routing ────────────────────────────────────────────


@dataclass
class TierConfig:
    tier1_min_confidence: int = 80
    tier2_min_confidence: int = 40
    high_value_threshold: int = 50
    very_high_value_threshold: int = 200


DEFAULT_TIER_CONFIG = TierConfig(
    tier1_min_confidence=80,
    tier2_min_confidence=40,
    high_value_threshold=50,
    very_high_value_threshold=200,
)
