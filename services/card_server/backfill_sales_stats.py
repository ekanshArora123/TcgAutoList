"""Backfill the pricing-input columns on market_snapshots from raw `sales`.

`collect` writes `divergence_sale_price` and `weighted_sale_price` on every
snapshot it takes, but rows collected before those columns existed have NULL
there — and the repricer deliberately skips such rows rather than price them
without a sold signal. That leaves a freshly-migrated DB unable to reprice until
every card has been re-collected, which costs a full run against a rate-limited
API.

`collect_sales` has already stored ~1yr of raw sold listings per variant in
`sales`, keyed identically (card + condition + finish + specialty_one + source).
This recomputes the sale-side statistics from those rows and writes them onto
each variant's most recent snapshot — no network at all.

It writes the WHOLE sale-side statistic set, not just the two new columns, so a
row's sale figures all describe the same set of sales. Using `sales` for the
weighted mean while leaving `recent_sales_count` at whatever a single fetch once
saw would be incoherent, and would break the backfill outright for any variant
the fetch recorded as having zero sales. Listing columns are never touched —
only a real collect run observes listings.

Two deliberate limits:

  * Variants with no snapshot row are SKIPPED, not created. A fabricated row
    would assert we looked at the market on that date and found no listings,
    which we did not. Those variants need a collect run.
  * Photo/custom listings (`has_image`) are excluded by default, matching the
    pricing fetch, which drops them — they are frequently off-market.

Usage:
  python -m services.card_server.backfill_sales_stats              # backfill
  python -m services.card_server.backfill_sales_stats --dry-run    # report only
  python -m services.card_server.backfill_sales_stats --half-life 21
  python -m services.card_server.backfill_sales_stats --compare 15,21,30,45
  python -m services.card_server.backfill_sales_stats --cards 88075,45163
  python -m services.card_server.backfill_sales_stats --include-photos

Afterwards, `collect.bat --reprice-only` turns the backfilled inputs into prices.

Environment:
  DB_PATH — SQLite database path (default: services/card_server/data/cards.db)
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from dataclasses import replace
from typing import Any, Iterator, Optional

from .db import close_database, init_database
from .helpers.pricing.config import DEFAULT_CONFIG, PricingConfig
from .helpers.pricing.inputs import sale_statistics

VariantKey = tuple[str, str, str, str, str]

# Sorted by variant then newest-first, so each variant's rows arrive together and
# already in the order the statistics want — the reader keeps only the newest few
# per variant and never holds the 1M-row table in memory.
_SALES_SQL = """
    SELECT card_id, condition, finish, specialty_one, source,
           order_date, purchase_price, shipping_price
    FROM sales
    WHERE ({photo_filter}) AND ({card_filter})
    ORDER BY card_id, condition, finish, specialty_one, source, order_date DESC
"""

# Only the sale side, and only on the variant's most recent snapshot — the row
# the repricer reads. Listing columns are left exactly as collected.
_UPDATE_SQL = """
    UPDATE market_snapshots SET
      recent_sales_count    = :sale_count,
      avg_sale_price        = :avg_sale_price,
      median_sale_price     = :median_sale_price,
      min_sale_price        = :min_sale_price,
      max_sale_price        = :max_sale_price,
      newest_sale_date      = :newest_sale_date,
      oldest_sale_date      = :oldest_sale_date,
      divergence_sale_price = :divergence_sale_price,
      weighted_sale_price   = :weighted_sale_price
    WHERE card_id = :card_id AND condition = :condition AND finish = :finish
      AND specialty_one = :specialty_one AND source = :source
      AND snapshot_date = (
        SELECT MAX(snapshot_date) FROM market_snapshots m2
        WHERE m2.card_id = :card_id AND m2.condition = :condition
          AND m2.finish = :finish AND m2.specialty_one = :specialty_one
          AND m2.source = :source
      )
"""


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Recompute snapshot sale statistics from the raw sales table"
    )
    parser.add_argument("--dry-run", dest="dry_run", action="store_true",
                        help="report what would change without writing")
    parser.add_argument("--half-life", dest="half_life", type=float, default=None,
                        help=f"override the sold weighting half-life in days "
                             f"(default {DEFAULT_CONFIG.sold_weight_half_life_days:g})")
    parser.add_argument("--compare", type=str, default=None,
                        help="comma-separated half-lives to compare; reports only, never writes")
    parser.add_argument("--cards", type=str, default=None,
                        help="comma-separated TCGplayer card IDs to limit to")
    parser.add_argument("--include-photos", dest="include_photos", action="store_true",
                        help="keep photo/custom listings, which the pricing fetch drops")
    return parser.parse_args()


def iter_variant_sales(
    db: sqlite3.Connection,
    config: PricingConfig,
    *,
    include_photos: bool = False,
    card_ids: Optional[list[str]] = None,
) -> Iterator[tuple[VariantKey, list[dict[str, Any]]]]:
    """Yield (variant, sale rows) pairs, newest first, capped per variant.

    Streams the sorted result set and emits one group at a time, so peak memory
    is a couple of dozen rows rather than the whole table.
    """
    photo_filter = "1=1" if include_photos else "has_image = 0"
    if card_ids:
        placeholders = ",".join("?" for _ in card_ids)
        card_filter = f"card_id IN ({placeholders})"
        params: list[Any] = list(card_ids)
    else:
        card_filter = "1=1"
        params = []

    sql = _SALES_SQL.format(photo_filter=photo_filter, card_filter=card_filter)

    current: Optional[VariantKey] = None
    batch: list[dict[str, Any]] = []

    for row in db.execute(sql, params):
        key: VariantKey = (
            row["card_id"], row["condition"], row["finish"],
            row["specialty_one"], row["source"],
        )
        if key != current:
            if current is not None:
                yield current, batch
            current, batch = key, []
        # Already sorted newest-first, so once we hold the cap the rest of this
        # variant's history is older than anything that could displace it.
        if len(batch) < config.max_sales_considered:
            batch.append(
                {
                    # Match the pricing fetch, which sums the two into one
                    # sold_price — a stored statistic has to be comparable to a
                    # live-computed one.
                    "sold_price": (row["purchase_price"] or 0) + (row["shipping_price"] or 0),
                    "sold_date": row["order_date"],
                }
            )

    if current is not None:
        yield current, batch


def backfill(
    db: sqlite3.Connection,
    config: PricingConfig,
    *,
    dry_run: bool = False,
    include_photos: bool = False,
    card_ids: Optional[list[str]] = None,
) -> dict[str, int]:
    """Recompute and store sale statistics for every variant found in `sales`."""
    stats = {"variants": 0, "updated": 0, "no_snapshot": 0, "no_usable_sales": 0}

    if not dry_run:
        db.execute("BEGIN")
    try:
        for key, sales in iter_variant_sales(
            db, config, include_photos=include_photos, card_ids=card_ids
        ):
            stats["variants"] += 1
            computed = sale_statistics(sales, config)
            if computed["sale_count"] == 0:
                stats["no_usable_sales"] += 1
                continue

            card_id, condition, finish, specialty_one, source = key
            params = {
                **computed,
                "card_id": card_id, "condition": condition, "finish": finish,
                "specialty_one": specialty_one, "source": source,
            }
            if dry_run:
                exists = db.execute(
                    "SELECT 1 FROM market_snapshots WHERE card_id=? AND condition=? "
                    "AND finish=? AND specialty_one=? AND source=? LIMIT 1",
                    key,
                ).fetchone()
                stats["updated" if exists else "no_snapshot"] += 1
                continue

            changed = db.execute(_UPDATE_SQL, params).rowcount
            stats["updated" if changed else "no_snapshot"] += 1

            if stats["variants"] % 2500 == 0:
                print(f"  {stats['variants']} variants processed...")

        if not dry_run:
            db.execute("COMMIT")
    except Exception:
        if not dry_run:
            db.execute("ROLLBACK")
        raise

    return stats


def compare_half_lives(
    db: sqlite3.Connection,
    half_lives: list[float],
    *,
    include_photos: bool = False,
    card_ids: Optional[list[str]] = None,
) -> None:
    """Report how much each half-life leans the sold mean toward recent sales.

    For every variant it computes the weighted mean and the plain mean over the
    same sales, and summarises their ratio. A ratio near 1.0 means the weighting
    barely matters for that variant (its sales are all around the same age or
    the same price); ratios far from 1.0 are where the choice actually moves
    prices. This is the calibration the half-life needs, since it is the one
    constant a reprice cannot re-derive from stored data.
    """
    base = DEFAULT_CONFIG
    ratios: dict[float, list[float]] = {h: [] for h in half_lives}

    for _, sales in iter_variant_sales(
        db, base, include_photos=include_photos, card_ids=card_ids
    ):
        for half_life in half_lives:
            computed = sale_statistics(sales, replace(base, sold_weight_half_life_days=half_life))
            plain = computed["avg_sale_price"]
            weighted = computed["weighted_sale_price"]
            if plain and weighted:
                ratios[half_life].append(weighted / plain)

    print(f"\n{'half-life':>10}  {'variants':>9}  {'median':>8}  {'p90':>8}  {'>10% moved':>11}")
    print("-" * 54)
    for half_life in half_lives:
        values = sorted(ratios[half_life])
        if not values:
            print(f"{half_life:>10.0f}  {'0':>9}")
            continue
        median = values[len(values) // 2]
        p90 = values[int(len(values) * 0.9)]
        moved = sum(1 for v in values if abs(v - 1.0) > 0.10)
        print(
            f"{half_life:>10.0f}  {len(values):>9}  {median:>8.3f}  {p90:>8.3f}  "
            f"{moved:>10} ({moved / len(values) * 100:.0f}%)"
        )
    print(
        "\nRatio = age-weighted mean / plain mean over the same sales. A shorter "
        "half-life\nleans harder on recent sales, so it moves further from 1.0 "
        "where prices have drifted."
    )


def _main() -> int:
    args = _parse_args()
    db = init_database(os.environ.get("DB_PATH"))

    config = DEFAULT_CONFIG
    if args.half_life is not None:
        config = replace(config, sold_weight_half_life_days=args.half_life)

    card_ids = [s.strip() for s in args.cards.split(",") if s.strip()] if args.cards else None

    try:
        if args.compare:
            half_lives = [float(s.strip()) for s in args.compare.split(",") if s.strip()]
            if not half_lives:
                print("Error: --compare needs comma-separated half-lives", file=sys.stderr)
                return 1
            print(f"Comparing half-lives {half_lives} over the raw sales history...")
            compare_half_lives(
                db, half_lives, include_photos=args.include_photos, card_ids=card_ids
            )
            return 0

        mode = "Dry run" if args.dry_run else "Backfilling"
        print(
            f"{mode}: sale statistics from `sales` -> market_snapshots "
            f"(half-life {config.sold_weight_half_life_days:g}d, "
            f"photos {'included' if args.include_photos else 'excluded'})\n"
        )
        stats = backfill(
            db,
            config,
            dry_run=args.dry_run,
            include_photos=args.include_photos,
            card_ids=card_ids,
        )

        verb = "would update" if args.dry_run else "updated"
        print(
            f"\n{stats['variants']} variants in `sales`\n"
            f"  {verb:>13}: {stats['updated']}\n"
            f"  no snapshot  : {stats['no_snapshot']} (need a collect run)\n"
            f"  unusable     : {stats['no_usable_sales']} (no dated sales)"
        )
        if not args.dry_run and stats["updated"]:
            print("\nNext: `collect.bat --reprice-only` to turn these inputs into prices.")
        return 0
    except Exception as err:  # noqa: BLE001
        print(f"Fatal error: {err}", file=sys.stderr)
        return 2
    finally:
        close_database()


def main() -> None:
    sys.exit(_main())


if __name__ == "__main__":
    main()
