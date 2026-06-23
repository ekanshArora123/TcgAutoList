"""Sales History Collection Runner.

Standalone script that gathers comprehensive raw sales history (~1 year) for
owned cards and stores it in the `sales` table. Separate from `collect.py`
(which gathers pricing snapshots) — this never touches the pricing path.

Usage:
  python -m services.card_server.collect_sales                 # all owned cards
  python -m services.card_server.collect_sales --stale         # not fetched in 7+ days
  python -m services.card_server.collect_sales --stale 14      # not fetched in 14+ days
  python -m services.card_server.collect_sales --cards 123,456 # specific card IDs
  python -m services.card_server.collect_sales --days 180      # history window (default 365)
  python -m services.card_server.collect_sales --delay 1000    # ms between cards (default 500)

Environment:
  DB_PATH                — SQLite database path (default: services/card_server/data/cards.db)
  TCGPLAYER_AUTH_COOKIE  — Required for full pagination; without it only ~5 sales/card
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from .db import close_database, init_database
from .helpers.market import SalesCollector


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TCGplayer comprehensive sales history collection")
    parser.add_argument("--stale", nargs="?", const=7, type=int, default=None)
    parser.add_argument("--cards", type=str, default=None)
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--delay", type=int, default=500)
    return parser.parse_args()


async def _main() -> int:
    args = _parse_args()
    db = init_database(os.environ.get("DB_PATH"))
    collector = SalesCollector(db)

    if args.cards:
        mode = "cards"
    elif args.stale is not None:
        mode = "stale"
    else:
        mode = "owned"

    print(f"Sales history collection starting (mode: {mode}, window: {args.days}d, delay: {args.delay}ms)\n")
    options = {"delayMs": args.delay, "maxDays": args.days}

    try:
        if mode == "owned":
            report = await collector.collect_owned(options)
        elif mode == "stale":
            stale_days = args.stale or 7
            print(f"Collecting cards whose sales are older than {stale_days} days\n")
            report = await collector.collect_stale(stale_days, options)
        else:  # cards
            card_ids = [s.strip() for s in args.cards.split(",") if s.strip()]
            if not card_ids:
                print("Error: --cards requires comma-separated card IDs", file=sys.stderr)
                return 1
            report = await collector.collect_cards(card_ids, options)

        if report["errors"]:
            print("\nErrors:")
            for err in report["errors"]:
                print(f"  {err['card_id']}: {err['error']}")

        return 1 if report["errors"] else 0
    except Exception as err:  # noqa: BLE001
        print(f"Fatal error: {err}", file=sys.stderr)
        return 2
    finally:
        close_database()


def main() -> None:
    sys.exit(asyncio.run(_main()))


if __name__ == "__main__":
    main()
