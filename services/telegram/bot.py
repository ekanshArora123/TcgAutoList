"""Telegram Bot wrapper.

Initializes the bot, routes incoming messages to handlers, and exposes methods for
the orchestrator to send messages back to the user. TRANSPORT LAYER only — it
forwards events to the orchestrator and sends orchestrator output back.

Ported from bot.ts. Uses python-telegram-bot (v20+, asyncio). The TS EventEmitter
is replaced with a small async callback registry (`on()` / `_emit()`); events are
plain dicts matching shared.types.TelegramEvent.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

EventHandler = Callable[[dict], Awaitable[None]]


class Bot:
    def __init__(self, token: str, chat_id: int, photos_dir: str, project_root: str):
        self.bot_token = token
        self.chat_id = chat_id
        self.photos_dir = photos_dir
        self.project_root = project_root
        # Tracks which photo side we're expecting next per inventory item.
        self.expected_photo: Optional[dict[str, Any]] = None

        Path(self.photos_dir).mkdir(parents=True, exist_ok=True)

        self._handlers: dict[str, list[EventHandler]] = {}
        self.app: Application = Application.builder().token(token).build()
        self._setup_handlers()

    # ─── Event emitter ─────────────────────────────────────────

    def on(self, event_name: str, handler: EventHandler) -> None:
        self._handlers.setdefault(event_name, []).append(handler)

    async def _emit(self, event_name: str, payload: dict) -> None:
        for handler in self._handlers.get(event_name, []):
            await handler(payload)

    # ─── Handler registration ──────────────────────────────────

    def _setup_handlers(self) -> None:
        self.app.add_handler(
            CommandHandler(["next", "status", "skip", "pause"], self._on_command)
        )
        self.app.add_handler(MessageHandler(filters.PHOTO, self._on_photo))
        self.app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_text)
        )
        self.app.add_handler(CallbackQueryHandler(self._on_callback))

    def _is_authorized(self, chat_id: int) -> bool:
        return chat_id == self.chat_id

    async def _on_command(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.message
        if not msg or not self._is_authorized(msg.chat_id):
            return
        command = (msg.text or "").lstrip("/").split("@")[0]
        await self._emit("event", {"type": "command", "command": command, "chatId": msg.chat_id})

    async def _on_photo(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.message
        if not msg or not self._is_authorized(msg.chat_id):
            return
        largest = msg.photo[-1]  # highest resolution
        try:
            file_path = await self._download_photo(largest.file_id)
            await self._emit("event", {"type": "photo", "filePath": file_path, "chatId": msg.chat_id})
        except Exception as err:  # noqa: BLE001
            print(f"Failed to download photo: {err}")
            await self.send_message("Failed to download photo. Please try again.")

    async def _on_text(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.message
        if not msg or not self._is_authorized(msg.chat_id):
            return
        if (msg.text or "").startswith("/"):
            return
        await self._emit("event", {"type": "text", "message": msg.text or "", "chatId": msg.chat_id})

    async def _on_callback(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query or not query.message or not self._is_authorized(query.message.chat_id):
            return
        await query.answer()
        await self._emit(
            "event",
            {
                "type": "callback",
                "data": query.data or "",
                "chatId": query.message.chat_id,
                "messageId": query.message.message_id,
            },
        )

    async def _download_photo(self, file_id: str) -> str:
        """Download a photo from Telegram and save to disk with a readable name."""
        file = await self.app.bot.get_file(file_id)
        file_url = file.file_path
        if not file_url:
            raise RuntimeError("No file_path in Telegram response")

        async with httpx.AsyncClient() as client:
            response = await client.get(file_url)
            if response.status_code != 200:
                raise RuntimeError(f"Failed to fetch photo: {response.status_code}")
            content = response.content

        side = (self.expected_photo or {}).get("side", "front")
        inv_id = (self.expected_photo or {}).get("inventory_id", "unknown")
        timestamp = datetime.now().isoformat().replace(":", "-").replace(".", "-")[:19]
        filename = f"inv{inv_id}_{side}_{timestamp}.jpg"
        save_path = os.path.join(self.photos_dir, filename)

        Path(save_path).write_bytes(content)
        return save_path

    # ─── Public methods for the orchestrator ───────────────────

    def set_expected_photo(self, inventory_id: int, side: str) -> None:
        self.expected_photo = {"inventory_id": inventory_id, "side": side}

    def clear_expected_photo(self) -> None:
        self.expected_photo = None

    async def send_message(self, text: str, parse_mode: Optional[str] = None) -> None:
        await self.app.bot.send_message(self.chat_id, text, parse_mode=parse_mode)

    async def send_inline_keyboard(
        self, text: str, buttons: list[list[dict[str, str]]], parse_mode: Optional[str] = None
    ) -> int:
        keyboard = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(b["text"], callback_data=b["callback_data"]) for b in row]
                for row in buttons
            ]
        )
        msg = await self.app.bot.send_message(
            self.chat_id, text, parse_mode=parse_mode, reply_markup=keyboard
        )
        return msg.message_id

    async def edit_message(self, message_id: int, text: str, parse_mode: Optional[str] = None) -> None:
        try:
            await self.app.bot.edit_message_text(
                text, chat_id=self.chat_id, message_id=message_id, parse_mode=parse_mode
            )
        except Exception as err:  # noqa: BLE001
            # Ignore "message is not modified" — happens when editing to same content.
            if "message is not modified" in str(err):
                return
            raise

    async def send_photo(
        self, file_path: str, caption: Optional[str] = None, parse_mode: Optional[str] = None
    ) -> None:
        with open(file_path, "rb") as fh:
            await self.app.bot.send_photo(self.chat_id, fh, caption=caption, parse_mode=parse_mode)

    def get_card_image_path(self, card_id: str) -> Optional[str]:
        """Path to a card's digital image, or None. Images: data/card-images/{cardId}.webp."""
        img_path = Path(self.project_root) / "data" / "card-images" / f"{card_id}.webp"
        return str(img_path) if img_path.exists() else None

    # ─── Lifecycle ─────────────────────────────────────────────

    async def start(self) -> None:
        """Initialize and start polling. Clears stale webhook first (avoids 409)."""
        await self.app.initialize()
        await self.app.bot.delete_webhook(drop_pending_updates=True)
        await self.app.start()
        await self.app.updater.start_polling()
        print(f"Telegram bot polling started (chatId: {self.chat_id})")

    async def stop(self) -> None:
        """Stop polling and clean up."""
        if self.app.updater:
            await self.app.updater.stop()
        await self.app.stop()
        await self.app.shutdown()
