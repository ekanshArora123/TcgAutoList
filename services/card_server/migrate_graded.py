"""One-off migration to the parallel graded-card model. Idempotent; safe to re-run.

Relocates legacy graded cards out of the raw `skus.specialty_two` convention
(e.g. "PSA9", "BGS 9.5") and `specialty_one='Graded'` into the new parallel
graded tables (graded_skus / graded_inventory / graded_prices). Any physical
inventory rows pointing at a graded raw-SKU are moved to graded_inventory, prices
are copied to graded_prices, and the orphaned raw SKU is dropped. Error
attributes in specialty_two (HoloBleed, MisCut, ...) are left untouched.

Unlike a mixed-table design, this does NOT rebuild the `inventory` table — the
raw chain is unchanged. New tables are created by schema.sql (auto on init).

Run from the repo root:  python -m services.card_server.migrate_graded
"""

from __future__ import annotations

import re
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from .db import init_database
from .helpers.crud.graded_skus import GradedSkusHelper

# Recognized grading companies -> parse "PSA9" / "PSA 10" / "BGS 9.5" / "CGC 9".
_GRADED_RE = re.compile(r"^\s*(PSA|BGS|CGC|SGC|ACE|TAG)\s*([0-9]+(?:\.[0-9]+)?)\s*$", re.IGNORECASE)


def backup_db(db_path: str) -> Optional[str]:
    """Copy the DB file to a timestamped backup next to it. Returns the path.

    Checkpoints the WAL first so the copied .db is a complete, consistent snapshot."""
    src = Path(db_path)
    if not src.exists():
        return None
    conn = sqlite3.connect(str(src))
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
    dst = src.with_name(f"{src.stem}.pre-graded-{datetime.now():%Y%m%d%H%M%S}{src.suffix}")
    shutil.copy2(src, dst)
    return str(dst)


def _parse_graded(sku: dict) -> Optional[tuple[str, float]]:
    """Return (company, grade) if this SKU represents a graded card, else None."""
    m = _GRADED_RE.match(sku["specialty_two"] or "")
    if m:
        return m.group(1).upper(), float(m.group(2))
    return None


def relocate_graded_skus(db: sqlite3.Connection) -> dict[str, int]:
    """Move legacy graded SKUs (and their inventory/prices) into the parallel
    graded tables. Idempotent — once moved, nothing matches on re-run."""
    graded = GradedSkusHelper(db)
    candidates = db.execute(
        "SELECT * FROM skus WHERE specialty_two != 'None' OR specialty_one = 'Graded'"
    ).fetchall()

    moved_skus, moved_inv, skipped = 0, 0, 0
    for sku in candidates:
        sku = dict(sku)
        parsed = _parse_graded(sku)
        if not parsed:
            skipped += 1  # error attribute (HoloBleed/MisCut) or unparseable — leave it
            continue
        company, grade = parsed
        # specialty_one on a graded card is a real TCGplayer variant (First
        # Edition); the stray literal 'Graded' is not — normalize it to 'None'.
        specialty_one = "None" if sku["specialty_one"] == "Graded" else sku["specialty_one"]

        new_sku = graded.get_or_create(
            {
                "card_id": sku["card_id"],
                "finish": sku["finish"],
                "specialty_one": specialty_one,
                "grading_company": company,
                "grade": grade,
            }
        )
        gid = new_sku["graded_sku_id"]
        old_sku_id = sku["sku_id"]

        # Move any physical inventory rows for this raw SKU into graded_inventory.
        inv_rows = db.execute(
            "SELECT * FROM inventory WHERE sku_id = ?", (old_sku_id,)
        ).fetchall()
        for row in inv_rows:
            row = dict(row)
            db.execute(
                """
                INSERT INTO graded_inventory
                    (graded_sku_id, cert_id, qty, tags, status,
                     front_photo_path, back_photo_path, ebay_listing_id, listed_at)
                VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    gid, row.get("qty") or 1, row.get("tags"), row.get("status") or "unlisted",
                    row.get("front_photo_path"), row.get("back_photo_path"),
                    row.get("ebay_listing_id"), row.get("listed_at"),
                ),
            )
            db.execute("DELETE FROM inventory WHERE inventory_id = ?", (row["inventory_id"],))
            moved_inv += 1

        # Copy price history across, then drop the raw rows + the orphaned SKU.
        db.execute(
            """
            INSERT OR IGNORE INTO graded_prices (
                graded_sku_id, calculation_date, estimated_price, estimated_liquid_value,
                confidence_percent, manual_check_necessary, manually_checked, algorithm_version,
                estimated_low_price, estimated_high_price, estimated_low_price_liquid, estimated_high_price_liquid)
            SELECT ?, calculation_date, estimated_price, estimated_liquid_value,
                confidence_percent, manual_check_necessary, manually_checked, algorithm_version,
                estimated_low_price, estimated_high_price, estimated_low_price_liquid, estimated_high_price_liquid
            FROM prices WHERE sku_id = ?
            """,
            (gid, old_sku_id),
        )
        db.execute("DELETE FROM prices WHERE sku_id = ?", (old_sku_id,))
        db.execute("DELETE FROM skus WHERE sku_id = ?", (old_sku_id,))
        graded.recalculate_qty(gid)
        moved_skus += 1

    return {"moved_skus": moved_skus, "moved_inventory": moved_inv, "skipped": skipped}


def run(db: Optional[sqlite3.Connection] = None, db_path: Optional[str] = None) -> dict:
    """Run the graded migration. init_database creates the parallel tables (via
    schema.sql); then legacy graded SKUs are relocated. Idempotent."""
    if db is None:
        db = init_database(db_path)
    else:
        db.executescript(
            (Path(__file__).resolve().parent / "schema.sql").read_text(encoding="utf-8")
        )
    return relocate_graded_skus(db)


if __name__ == "__main__":
    import os

    default_path = str(Path(__file__).resolve().parent / "data" / "cards.db")
    path = os.environ.get("DB_PATH", default_path)
    backup = backup_db(path)
    if backup:
        print(f"Backed up DB -> {backup}")
    result = run(db_path=path)
    print(f"Migration complete: {result}")
