"""TcgAutoList — Entry point.

Initializes all components and starts the listing workflow:
1. Load environment variables
2. Initialize SQLite DB and card-server services
3. Start Telegram bot
4. Start orchestrator event loop
5. Handle graceful shutdown

Run from the repo root: python -m dashboard.backend.orchestrator.main
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from pathlib import Path

from dotenv import load_dotenv

from services.card_server.db import close_database, get_db, init_database
from services.card_server.services.collection_service import CollectionService
from services.card_server.services.pricing_service import PricingService
from services.telegram.bot import Bot

from .orchestrator import Orchestrator

# main.py lives at dashboard/backend/orchestrator/main.py -> repo root is 3 up.
_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        print(f"Missing required environment variable: {name}", file=sys.stderr)
        sys.exit(1)
    return val


async def main() -> None:
    load_dotenv()
    print("TcgAutoList starting...")

    telegram_token = _require_env("TELEGRAM_BOT_TOKEN")
    try:
        telegram_chat_id = int(_require_env("TELEGRAM_CHAT_ID"))
    except ValueError:
        print("TELEGRAM_CHAT_ID must be a number", file=sys.stderr)
        sys.exit(1)

    db_path = os.environ.get("DB_PATH") or str(
        _PROJECT_ROOT / "services" / "card_server" / "data" / "cards.db"
    )
    photos_dir = os.environ.get("PHOTOS_DIR") or str(_PROJECT_ROOT / "photos")

    print(f"Database: {db_path}")
    init_database(db_path)
    db = get_db()

    collection_service = CollectionService(db)
    pricing_service = PricingService(db)

    stats = collection_service.get_stats()
    print(f"Collection: {stats['total']} cards ({stats['by_status']})")

    print("Starting Telegram bot...")
    bot = Bot(
        token=telegram_token,
        chat_id=telegram_chat_id,
        photos_dir=photos_dir,
        project_root=str(_PROJECT_ROOT),
    )

    orchestrator = Orchestrator(
        bot=bot,
        collection_service=collection_service,
        pricing_service=pricing_service,
    )
    orchestrator.start()

    await bot.start()
    try:
        await bot.send_message("TcgAutoList is online. Send /next to start listing cards.")
        print("Startup message sent to Telegram.")
    except Exception as err:  # noqa: BLE001
        print(f"Failed to send startup message: {err}", file=sys.stderr)
        print("Check your TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.", file=sys.stderr)

    # ─── Run until shutdown signal ─────────────────────────────
    stop_event = asyncio.Event()

    def _request_shutdown() -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_shutdown)
        except NotImplementedError:
            # Windows event loop may not support add_signal_handler for SIGTERM.
            pass

    await stop_event.wait()

    print("\nShutting down...")
    await bot.stop()
    close_database()
    print("Goodbye.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
