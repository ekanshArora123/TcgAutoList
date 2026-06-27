"""Sales History Collection Runner.

Standalone script that gathers comprehensive raw sales history (~1 year) for
owned cards and stores it in the `sales` table. Separate from `collect.py`
(which gathers pricing snapshots) — this never touches the pricing path.

A full backfill is thousands of cards × ~14 requests each, so the recommended
way to run it is `--loop`: paced, resumable batches that drain the backlog
without tripping TCGplayer's rate limiting, then keep the data fresh.

Usage:
  python -m services.card_server.collect_sales --loop          # RECOMMENDED: paced batches until drained
  python -m services.card_server.collect_sales --batch         # one batch (150 cards, oldest first)
  python -m services.card_server.collect_sales --batch 50      # one batch of 50
  python -m services.card_server.collect_sales                 # all owned cards in one run
  python -m services.card_server.collect_sales --stale 14      # owned cards not fetched in 14+ days
  python -m services.card_server.collect_sales --cards 123,456 # specific card IDs
  python -m services.card_server.collect_sales --days 180      # history window (default 365)
  python -m services.card_server.collect_sales --rate 1.0      # avg requests/sec (default 1.5)

Pacing / resume flags (apply to --loop and --batch):
  --batch [N]     cards per batch (default 150)
  --max-age DAYS  skip cards refreshed within DAYS (default 7)
  --rate R        average requests/second across all endpoints (default 1.5)
  --loop-pause S  seconds between batches in --loop (default 90)
  --cooldown S    seconds to wait after a rate-limit stop in --loop (default 900)

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
    parser.add_argument("--loop", action="store_true", help="run paced batches until backlog drained")
    parser.add_argument("--batch", nargs="?", const=150, type=int, default=None, help="one batch of N cards")
    parser.add_argument("--stale", nargs="?", const=7, type=int, default=None)
    parser.add_argument("--cards", type=str, default=None)
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--max-age", dest="max_age", type=int, default=7)
    parser.add_argument("--rate", type=float, default=1.5)
    parser.add_argument("--loop-pause", dest="loop_pause", type=int, default=90)
    parser.add_argument("--cooldown", type=int, default=900)
    return parser.parse_args()


async def _run_loop(collector: SalesCollector, options: dict, batch_size: int,
                    max_age: int, loop_pause: int, cooldown: int) -> int:
    """Drain the backlog in paced batches; self-heals on rate-limit stops."""
    total_cards = 0
    while True:
        pending = collector.count_pending(max_age)
        if pending == 0:
            print(f"\nBacklog drained. {total_cards} cards collected this session.")
            return 0
        print(f"\n=== Batch: {pending} cards pending (≤{batch_size} this round) ===")
        report = await collector.collect_batch(batch_size, max_age, options)
        total_cards += report["cards_processed"]

        if report["stopped_early"]:
            print(f"Rate-limited — cooling down {cooldown}s before the next batch...")
            await asyncio.sleep(cooldown)
        elif report["cards_processed"] == 0:
            # No progress and not blocked (e.g. all errored) — avoid a hot loop.
            print("No cards processed this batch; stopping to avoid a hot loop.")
            return 1
        else:
            await asyncio.sleep(loop_pause)


async def _main() -> int:
    args = _parse_args()
    db = init_database(os.environ.get("DB_PATH"))
    collector = SalesCollector(db)

    options = {
        "maxDays": args.days,
        "ratePerSec": args.rate,
    }

    try:
        if args.loop:
            print(
                f"Sales collection loop starting "
                f"(batch: {args.batch or 150}, window: {args.days}d, "
                f"rate: {args.rate}/s, max-age: {args.max_age}d)\n"
            )
            return await _run_loop(
                collector, options, args.batch or 150, args.max_age,
                args.loop_pause, args.cooldown,
            )

        if args.batch is not None:
            mode_desc = f"batch of {args.batch}, oldest-fetched first"
        elif args.cards:
            mode_desc = "specific cards"
        elif args.stale is not None:
            mode_desc = f"stale (>{args.stale}d)"
        else:
            mode_desc = "all owned cards"
        print(
            f"Sales history collection starting "
            f"(mode: {mode_desc}, window: {args.days}d, rate: {args.rate}/s)\n"
        )

        if args.batch is not None:
            report = await collector.collect_batch(args.batch, args.max_age, options)
        elif args.cards:
            card_ids = [s.strip() for s in args.cards.split(",") if s.strip()]
            if not card_ids:
                print("Error: --cards requires comma-separated card IDs", file=sys.stderr)
                return 1
            report = await collector.collect_cards(card_ids, options)
        elif args.stale is not None:
            print(f"Collecting cards whose sales are older than {args.stale} days\n")
            report = await collector.collect_stale(args.stale, options)
        else:
            report = await collector.collect_owned(options)

        if report["errors"]:
            print("\nErrors:")
            for err in report["errors"]:
                print(f"  {err['card_id']}: {err['error']}")

        return 1 if (report["errors"] or report["stopped_early"]) else 0
    except Exception as err:  # noqa: BLE001
        print(f"Fatal error: {err}", file=sys.stderr)
        return 2
    finally:
        close_database()


def main() -> None:
    sys.exit(asyncio.run(_main()))


if __name__ == "__main__":
    main()
