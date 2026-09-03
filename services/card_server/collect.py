"""Market Data Collection Runner.

Standalone script that collects market data for owned cards. Run manually or via
Task Scheduler / cron. Ported from collect.ts.

Each card visit does two separable things: fetch its market state into
`market_snapshots`, then reprice its SKUs from what was just stored. Because the
pricing half reads the DB rather than the fetch, `--reprice-only` can redo just
that half for the same cards with no network calls at all — the way to apply a
changed pricing constant across the collection in seconds.

Usage:
  python -m services.card_server.collect                  # collect all owned cards
  python -m services.card_server.collect --stale          # cards not collected in 7+ days
  python -m services.card_server.collect --stale 14        # not collected in 14+ days
  python -m services.card_server.collect --cohort          # same-set neighbors (not owned)
  python -m services.card_server.collect --cards 123,456   # specific card IDs
  python -m services.card_server.collect --delay 1000      # 1s between cards (default 500ms)
  python -m services.card_server.collect --variant-delay 0 # no gap between a card's variant calls
  python -m services.card_server.collect --force           # re-collect cards already done today
  python -m services.card_server.collect --reprice-only    # reprice from stored data, no fetching

A card costs one listing request per owned variant (an MP-LP card fetches both
MP and LP), so `--variant-delay` paces the calls WITHIN a card and `--delay`
paces the gap BETWEEN cards. Neither applies under `--reprice-only`, which
issues no requests.

Selection modes (owned / stale / cohort) run most-expensive-card-first, by each
card's latest stored estimate, so an interrupted run has already refreshed the
cards worth the most; never-priced cards sort last. An explicit `--cards` list
is collected in the order given.

Environment:
  DB_PATH                — SQLite database path (default: services/card_server/data/cards.db)
  TCGPLAYER_AUTH_COOKIE  — Optional, enables full sold data pagination
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from datetime import datetime

from .db import close_database, init_database
from .helpers.market import MarketCollector


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TCGplayer market data collection")
    parser.add_argument("--stale", nargs="?", const=7, type=int, default=None)
    parser.add_argument("--cohort", action="store_true")
    parser.add_argument("--cards", type=str, default=None)
    parser.add_argument("--delay", type=int, default=500)
    parser.add_argument("--variant-delay", type=int, default=250)
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--reprice-only",
        dest="reprice_only",
        action="store_true",
        help="recompute prices from stored snapshots without fetching anything",
    )
    return parser.parse_args()


def _reprice(collector: MarketCollector, card_ids: list[str]) -> int:
    """Recompute prices for the selected cards from stored snapshots.

    Cards with no snapshot yet simply price nothing — they need a collecting run
    first, and their previous estimates are left untouched rather than cleared.
    """
    start = time.time()
    today = datetime.now().date().isoformat()
    priced = 0
    touched = 0

    for i, card_id in enumerate(card_ids):
        written = collector.repricer.price_card(card_id, today)
        priced += written
        if written:
            touched += 1
        if (i + 1) % 250 == 0:
            print(f"  [{i + 1}/{len(card_ids)}] {priced} prices written...")

    print(
        f"\nReprice complete: {touched}/{len(card_ids)} cards had stored data, "
        f"{priced} prices written, {time.time() - start:.1f}s"
    )
    return 0


async def _main() -> int:
    args = _parse_args()
    db = init_database(os.environ.get("DB_PATH"))
    collector = MarketCollector(db, variant_delay_ms=args.variant_delay)

    if args.cards:
        mode = "cards"
    elif args.cohort:
        mode = "cohort"
    elif args.stale is not None:
        mode = "stale"
    else:
        mode = "owned"

    opts = {"delayMs": args.delay, "force": args.force}

    if args.reprice_only:
        card_ids = (
            [s.strip() for s in args.cards.split(",") if s.strip()]
            if args.cards
            else collector.select_cards(mode, args.stale or 7)
        )
        print(f"Repricing from stored data (mode: {mode}, {len(card_ids)} cards, no fetching)\n")
        try:
            return _reprice(collector, card_ids)
        finally:
            close_database()

    print(
        f"Market data collection starting (mode: {mode}, delay: {args.delay}ms, "
        f"variant delay: {args.variant_delay}ms)"
    )
    print(f"Date: {datetime.now().date().isoformat()}\n")

    try:
        if mode == "owned":
            report = await collector.collect_owned(opts)
        elif mode == "stale":
            stale_days = args.stale or 7
            print(f"Collecting cards with snapshots older than {stale_days} days\n")
            report = await collector.collect_stale(stale_days, opts)
        elif mode == "cohort":
            report = await collector.collect_cohort(opts)
        else:  # cards
            card_ids = [s.strip() for s in args.cards.split(",") if s.strip()]
            if not card_ids:
                print("Error: --cards requires comma-separated card IDs", file=sys.stderr)
                return 1
            report = await collector.collect_cards(card_ids, opts)

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
