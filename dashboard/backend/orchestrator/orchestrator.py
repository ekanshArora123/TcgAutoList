"""Orchestrator — Coded event loop that drives the listing workflow.

NOT an LLM. Pure Python that:
1. Listens for /next from Telegram
2. Gets next unlisted card from DB
3. Fetches/refreshes price
4. Routes to appropriate tier
5. Tier 1: requests photo -> builds listing -> posts to eBay
6. Tier 2/3: spawns LLM conversation (pseudocode for now)
7. Marks card as done, waits for next /next

Context resets per card. All durable state is in SQLite. Ported from orchestrator.ts.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from card_server.services.collection_service import CollectionService
from card_server.services.pricing_service import PricingService
from shared.types import CardWorkflowState
from telegram_service.bot import Bot
from telegram_service.renderer import (
    build_photo_request_keyboard,
    build_review_keyboard,
    render_card_summary,
    render_listing_confirmation,
    render_photo_request,
    render_status,
)

from .tier1_pipeline import build_listing_template, post_to_ebay
from .tier_router import route_to_tier


class Orchestrator:
    def __init__(
        self,
        bot: Bot,
        collection_service: CollectionService,
        pricing_service: PricingService,
    ):
        self.bot = bot
        self.collection = collection_service
        self.pricing = pricing_service
        self.state: Optional[CardWorkflowState] = None
        self.paused = False
        # Prevents concurrent handle_next execution.
        self.processing_next = False

    def start(self) -> None:
        """Register the Telegram event handler."""
        self.bot.on("event", self._handle_event_safe)
        print("Orchestrator started. Waiting for /next...")

    async def _handle_event_safe(self, event: dict) -> None:
        try:
            await self._handle_event(event)
        except Exception as err:  # noqa: BLE001
            print(f"Orchestrator error handling event: {err}")
            try:
                await self.bot.send_message(f"Error: {err}")
            except Exception:
                pass

    async def _handle_event(self, event: dict) -> None:
        etype = event["type"]
        if etype == "command":
            await self._handle_command(event["command"])
        elif etype == "photo":
            await self._handle_photo(event["filePath"])
        elif etype == "text":
            await self._handle_text(event["message"])
        elif etype == "callback":
            await self._handle_callback(event["data"], event["messageId"])

    # ─── Command Handlers ──────────────────────────────────────

    async def _handle_command(self, command: str) -> None:
        if command == "next":
            await self._handle_next()
        elif command == "status":
            await self._handle_status()
        elif command == "skip":
            await self._handle_skip()
        elif command == "pause":
            await self._handle_pause()

    async def _handle_next(self) -> None:
        """/next — Pick next unlisted card and start the workflow."""
        if self.paused:
            await self.bot.send_message("Bot is paused. Send /pause to resume.")
            return

        if self.processing_next:
            return

        if self.state and self.state.step not in ("idle", "done"):
            await self.bot.send_message(
                f"Already processing a card (step: {self.state.step}). Send /skip to skip it."
            )
            return

        self.processing_next = True

        try:
            detail = self.collection.get_next_unlisted()
            if not detail:
                await self.bot.send_message("No more unlisted cards! All done.")
                return

            self.state = CardWorkflowState(
                inventory_id=detail["inventory_id"],
                inventory_detail=detail,
                step="pricing",
            )

            # Check for existing price in DB first (fast, no network).
            price = self.pricing.get_latest_price_for_item(detail["inventory_id"])

            is_stale = price and self._is_price_stale(price["calculation_date"])
            if not price or is_stale:
                await self.bot.send_message("Fetching price...")
                try:
                    result = await self.pricing.compute_and_store_price(
                        detail["card_id"], detail["condition"], detail["finish"]
                    )
                    if result:
                        price = result
                except Exception:
                    if not price:
                        await self.bot.send_message("Failed to fetch price. No existing price in DB.")

            self.state.price = price
            self.state.confidence = (price or {}).get("confidence_percent") or 0

            specialty_two = detail.get("specialty_two") or "None"

            self.state.step = "tier_routing"
            tier_result = route_to_tier(price, specialty_two)
            self.state.tier = tier_result["tier"]

            summary = render_card_summary(detail, price)
            summary_text = f"{summary}\n\nTier: {tier_result['tier']} — {tier_result['reason']}"
            card_image_path = self.bot.get_card_image_path(detail["card_id"])

            if card_image_path:
                await self.bot.send_photo(card_image_path, summary_text, "Markdown")
            else:
                await self.bot.send_message(
                    f"{summary_text}\n\n_No digital image available_", "Markdown"
                )

            if tier_result["tier"] == 1:
                await self._execute_tier1()
            elif tier_result["tier"] == 2:
                await self._execute_tier2_stub()
            elif tier_result["tier"] == 3:
                await self._execute_tier3_stub()
        finally:
            self.processing_next = False

    async def _execute_tier1(self) -> None:
        """Execute Tier 1: template listing, no LLM."""
        if not self.state:
            return

        self.state.step = "awaiting_photo"
        self.state.awaiting_side = "front"
        self.bot.set_expected_photo(self.state.inventory_id, "front")

        photo_msg = render_photo_request(self.state.inventory_detail, "front")
        await self.bot.send_inline_keyboard(
            photo_msg, build_photo_request_keyboard(self.state.inventory_id), "Markdown"
        )

        self.collection.update_status(self.state.inventory_id, "photo_requested")

    async def _handle_photo(self, file_path: str) -> None:
        """Handle received photo — continue the workflow."""
        if not self.state or self.state.step != "awaiting_photo":
            await self.bot.send_message("Not expecting a photo right now.")
            return

        if self.state.awaiting_side == "front":
            # Front received — request back.
            self.state.photo_path = file_path
            self.state.awaiting_side = "back"
            self.bot.set_expected_photo(self.state.inventory_id, "back")

            photo_msg = render_photo_request(self.state.inventory_detail, "back")
            await self.bot.send_inline_keyboard(
                photo_msg, build_photo_request_keyboard(self.state.inventory_id), "Markdown"
            )
            return

        # Back received — proceed with listing.
        self.state.back_photo_path = file_path
        self.bot.clear_expected_photo()
        self.state.awaiting_side = None

        self.state.step = "building_listing"
        await self.bot.send_message("Photos received. Building listing...")

        if (
            self.state.tier == 1
            and self.state.price
            and self.state.price.get("estimated_price") is not None
        ):
            photo_paths = [self.state.photo_path, self.state.back_photo_path]

            self.collection.set_photos(self.state.inventory_id, photo_paths[0], photo_paths[1])

            listing = build_listing_template(
                self.state.inventory_detail, self.state.price, photo_paths
            )

            self.state.step = "posting"
            ebay_listing_id = await post_to_ebay(listing)

            self.collection.mark_as_listed(self.state.inventory_id, ebay_listing_id)

            confirmation = render_listing_confirmation(self.state.inventory_detail, listing.price)
            await self.bot.send_message(confirmation, "Markdown")

            self.state.step = "done"
            await self.bot.send_message("Send /next for the next card.")

    async def _handle_status(self) -> None:
        """/status — Show current state and collection stats."""
        stats = self.collection.get_stats()
        current = self.state.inventory_detail if self.state else None
        status_msg = render_status(current, stats)
        await self.bot.send_message(status_msg, "Markdown")

        if self.paused:
            await self.bot.send_message("Bot is currently *paused*. Send /pause to resume.", "Markdown")

    async def _handle_skip(self) -> None:
        """/skip — Skip the current card."""
        if not self.state or self.state.step in ("idle", "done"):
            await self.bot.send_message("No card to skip.")
            return

        self.collection.update_status(self.state.inventory_id, "skipped")
        self.bot.clear_expected_photo()
        await self.bot.send_message(
            f"Skipped: {self.state.inventory_detail['card_name']}. Send /next for the next card."
        )
        self.state.step = "done"

    async def _handle_pause(self) -> None:
        """/pause — Toggle pause."""
        self.paused = not self.paused
        await self.bot.send_message("Bot paused." if self.paused else "Bot resumed.")

    async def _handle_callback(self, data: str, message_id: int) -> None:
        """Handle inline keyboard callbacks."""
        action, _, id_str = data.partition(":")
        inventory_id = int(id_str) if id_str else None

        if action == "skip":
            if self.state and self.state.inventory_id == inventory_id:
                await self._handle_skip()
                await self.bot.edit_message(message_id, "Skipped.")
        elif action == "approve":
            await self.bot.edit_message(message_id, "Approved.")
        elif action == "override":
            await self.bot.send_message('Send new price as a number (e.g., "12.50").')
        elif action == "details":
            if self.state and self.state.price:
                await self.bot.send_message(f"Algorithm: {self.state.price.get('algorithm_version')}")

    async def _handle_text(self, message: str) -> None:
        """Handle free text (Tier 2/3 relay, or price override)."""
        try:
            price_num = float(message)
        except ValueError:
            price_num = None

        if price_num is not None and price_num > 0 and self.state and self.state.step != "done":
            # TODO: implement price override
            await self.bot.send_message(f"Price override to ${price_num:.2f} — not yet implemented.")
            return

        if self.state and self.state.tier in (2, 3):
            await self.bot.send_message("LLM relay not implemented yet. Tier 2/3 is pseudocode.")
            return

    def _is_price_stale(self, calculation_date: str) -> bool:
        """Check if a price is older than 14 days."""
        try:
            price_date = datetime.fromisoformat(calculation_date)
        except ValueError:
            return False
        diff_days = (datetime.now() - price_date).total_seconds() / 86400
        return diff_days > 14

    # ─── Tier 2/3 Stubs ───────────────────────────────────────

    async def _execute_tier2_stub(self) -> None:
        """Tier 2 stub — will be replaced with scoped LLM conversation."""
        if not self.state:
            return

        await self.bot.send_inline_keyboard(
            "🔶 *Tier 2* — This card needs scoped LLM review (not yet implemented).\n"
            "You can approve the algorithm price or skip.",
            build_review_keyboard(self.state.inventory_id),
            "Markdown",
        )
        await self._execute_tier1()

    async def _execute_tier3_stub(self) -> None:
        """Tier 3 stub — will be replaced with full LLM conversation."""
        if not self.state:
            return

        await self.bot.send_inline_keyboard(
            "🔴 *Tier 3* — This card needs full LLM analysis (not yet implemented).\n"
            "You can approve the algorithm price or skip.",
            build_review_keyboard(self.state.inventory_id),
            "Markdown",
        )
        await self._execute_tier1()
