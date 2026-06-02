"""Deduplication script: remove duplicate inventory rows caused by running the
migration multiple times.

Strategy: for each group of duplicates (same sku_id, qty, tags), keep ONE row.
If any row was modified (status != 'unlisted', has photos, ebay_listing_id),
keep that one; otherwise keep the lowest inventory_id. Delete the rest.

Run: python -m card_server.dedup_inventory [--db-path <path>] [--dry-run]
Ported from dedup-inventory.ts.
"""

from __future__ import annotations

import argparse
import sys

from .db import close_database, get_db, init_database


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Deduplicate inventory rows")
    parser.add_argument("--db-path", dest="db_path", default=None)
    parser.add_argument("--dry-run", dest="dry_run", action="store_true")
    return parser.parse_args()


def dedup(db_path: str | None, dry_run: bool) -> None:
    print("Deduplicating inventory...")
    print(f"  DB: {db_path or '(default)'}")
    print(f"  Mode: {'DRY RUN' if dry_run else 'LIVE'}")

    init_database(db_path)
    db = get_db()

    total_before = db.execute("SELECT COUNT(*) as c FROM inventory").fetchone()["c"]
    print(f"\n  Total inventory rows before: {total_before}")

    groups = db.execute(
        """
        SELECT sku_id, qty, COALESCE(tags, '') as tags, COUNT(*) as cnt,
               GROUP_CONCAT(inventory_id) as ids
        FROM inventory
        GROUP BY sku_id, qty, COALESCE(tags, '')
        HAVING cnt > 1
        ORDER BY cnt DESC
        """
    ).fetchall()

    print(f"  Duplicate groups: {len(groups)}")

    ids_to_delete: list[int] = []

    for group in groups:
        all_ids = [int(x) for x in group["ids"].split(",")]

        modified_rows = db.execute(
            f"""
            SELECT inventory_id FROM inventory
            WHERE inventory_id IN ({','.join(str(i) for i in all_ids)})
              AND (status != 'unlisted'
                   OR front_photo_path IS NOT NULL
                   OR back_photo_path IS NOT NULL
                   OR ebay_listing_id IS NOT NULL)
            ORDER BY inventory_id ASC
            """
        ).fetchall()

        if modified_rows:
            keep_id = modified_rows[0]["inventory_id"]
        else:
            keep_id = min(all_ids)

        for id_ in all_ids:
            if id_ != keep_id:
                ids_to_delete.append(id_)

    print(f"  Rows to delete: {len(ids_to_delete)}")
    print(f"  Rows to keep: {total_before - len(ids_to_delete)}")

    if dry_run:
        print("\n  DRY RUN — no changes made. Run without --dry-run to apply.")
    else:
        batch_size = 500
        db.execute("BEGIN")
        try:
            for i in range(0, len(ids_to_delete), batch_size):
                batch = ids_to_delete[i : i + batch_size]
                db.execute(
                    f"DELETE FROM inventory WHERE inventory_id IN ({','.join(str(b) for b in batch)})"
                )
            db.execute("COMMIT")

            total_after = db.execute("SELECT COUNT(*) as c FROM inventory").fetchone()["c"]
            print(f"\n  Total inventory rows after: {total_after}")
            print(f"  Deleted: {total_before - total_after}")

            print("  Recalculating SKU quantities...")
            db.execute(
                """
                UPDATE skus SET qty = (
                  SELECT COALESCE(SUM(qty), 0) FROM inventory WHERE inventory.sku_id = skus.sku_id
                )
                """
            )
            print("  Done.")
        except Exception:
            db.execute("ROLLBACK")
            raise

    status_breakdown = db.execute(
        "SELECT status, COUNT(*) as cnt FROM inventory GROUP BY status ORDER BY cnt DESC"
    ).fetchall()
    print("\n  Status breakdown (after):")
    for row in status_breakdown:
        print(f"    {row['status']}: {row['cnt']}")

    close_database()


def main() -> None:
    args = _parse_args()
    try:
        dedup(args.db_path, args.dry_run)
    except Exception as err:  # noqa: BLE001
        print(f"Dedup failed: {err}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
