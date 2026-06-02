"""LLM Client — Claude API integration for Tier 2/3 cards.

PSEUDOCODE — Not yet implemented. Outlines the intended architecture for spawning
per-card LLM conversations. Ported from llm.ts.

ARCHITECTURE:
- Each card gets a fresh Claude conversation (no history accumulation).
- Tier 2: scoped context (pricing data only), scoped tools (pricing/research).
- Tier 3: full context (all card data), full tools (all card-server tools).
- The orchestrator relays Telegram free-text into the active conversation.
- When the card is done, the conversation is discarded.

ESTIMATED TOKEN USAGE:
- Tier 2: ~5,000 tokens per card
- Tier 3: ~10,000-15,000 tokens per card
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# from anthropic import Anthropic  # Will use when implemented


@dataclass
class ConversationState:
    """Active LLM conversation state."""

    inventory_id: int
    tier: int
    messages: list[dict[str, str]] = field(default_factory=list)
    pending_tool_calls: list[Any] = field(default_factory=list)


async def create_tier2_conversation(
    detail: dict[str, Any], price: dict[str, Any], algorithm_reasoning: str
) -> None:
    """PSEUDOCODE: Create a Tier 2 conversation (scoped pricing tools).

    The LLM decides the final price, whether to list/skip/hold, or escalate to
    Tier 3. See llm.ts for the intended Anthropic SDK message loop.
    """
    print(f"[LLM STUB] Tier 2 conversation for {detail['card_name']} — not implemented")


async def create_tier3_conversation(
    detail: dict[str, Any], price: Optional[dict[str, Any]]
) -> None:
    """PSEUDOCODE: Create a Tier 3 conversation (full tools + authority).

    The LLM researches comps, analyzes trends, makes a judgment call, can ask the
    user via Telegram, or recommend manual review. See llm.ts for the agentic loop.
    """
    print(f"[LLM STUB] Tier 3 conversation for {detail['card_name']} — not implemented")


async def relay_user_message(conversation: ConversationState, message: str) -> str:
    """PSEUDOCODE: Relay user free-text from Telegram into the active conversation."""
    return "[LLM relay not implemented]"
