"""Migration script: MySQL dump -> SQLite (historical).

Reads the UTF-16LE MySQL dump and populates the SQLite schema. Mapping:
  cardinfo        -> cards
  skutable        -> skus  (hashed SKU -> auto-increment ID)
  actualinventory -> inventory  (hashed SKU FK -> sku_id FK)
  cardprices      -> prices (hashed SKU PK -> sku_id PK)

Run: python -m card_server.migrate [--dump-path <path>] [--db-path <path>]
Ported from migrate.ts.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from .db import close_database, get_db, init_database

_THIS_DIR = Path(__file__).resolve().parent


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate MySQL dump to SQLite")
    parser.add_argument(
        "--dump-path",
        dest="dump_path",
        default=str(_THIS_DIR.parent.parent.parent / "Old implementation files" / "database-dump.sql"),
    )
    parser.add_argument("--db-path", dest="db_path", default=str(_THIS_DIR.parent / "data" / "cards.db"))
    return parser.parse_args()


def parse_insert_values(line: str) -> list[list[str]]:
    """Parse a MySQL INSERT INTO statement and extract rows of values."""
    values_idx = line.find("VALUES ")
    if values_idx == -1:
        return []
    values_part = line[values_idx + 7 :]

    rows: list[list[str]] = []
    current: list[str] = []
    i = 0
    in_value = False

    while i < len(values_part):
        ch = values_part[i]

        if ch == "(":
            current = []
            in_value = True
            i += 1
            continue

        if ch == ")" and in_value:
            rows.append(current)
            in_value = False
            i += 1
            continue

        if not in_value:
            i += 1
            continue

        if ch == "'":
            s = ""
            i += 1  # skip opening quote
            while i < len(values_part):
                if values_part[i] == "'" and i + 1 < len(values_part) and values_part[i + 1] == "'":
                    s += "'"
                    i += 2
                elif values_part[i] == "\\" and i + 1 < len(values_part) and values_part[i + 1] == "'":
                    s += "'"
                    i += 2
                elif values_part[i] == "\\" and i + 1 < len(values_part) and values_part[i + 1] == "\\":
                    s += "\\"
                    i += 2
                elif values_part[i] == "'":
                    i += 1  # skip closing quote
                    break
                else:
                    s += values_part[i]
                    i += 1
            current.append(s)
        elif ch == "N" and values_part[i : i + 4] == "NULL":
            current.append("NULL")
            i += 4
        elif ch == "," or ch == " ":
            i += 1
        else:
            num = ""
            while i < len(values_part) and values_part[i] not in (",", ")"):
                num += values_part[i]
                i += 1
            current.append(num.strip())

    return rows


def _nullable_str(val: str) -> Optional[str]:
    return None if val in ("NULL", "") else val


def _nullable_num(val: str) -> Optional[float]:
    if val in ("NULL", ""):
        return None
    try:
        return float(val)
    except ValueError:
        return None


def _nullable_int(val: str) -> Optional[int]:
    if val in ("NULL", ""):
        return None
    try:
        return int(float(val))
    except ValueError:
        return None


def migrate(dump_path: str, db_path: str) -> None:
    print("Reading MySQL dump...")
    print(f"  Dump: {dump_path}")
    print(f"  DB:   {db_path}")

    raw = Path(dump_path).read_bytes()
    if len(raw) >= 2 and raw[0] == 0xFF and raw[1] == 0xFE:
        content = raw.decode("utf-16-le")
    else:
        content = raw.decode("utf-8")

    lines = content.splitlines()

    card_info_line = next((l for l in lines if l.startswith("INSERT INTO `cardinfo`")), None)
    sku_table_line = next((l for l in lines if l.startswith("INSERT INTO `skutable`")), None)
    inventory_lines = [l for l in lines if l.startswith("INSERT INTO `actualinventory`")]
    price_lines = [l for l in lines if l.startswith("INSERT INTO `cardprices`")]

    if not card_info_line:
        raise RuntimeError("No cardinfo INSERT found in dump")
    if not sku_table_line:
        raise RuntimeError("No skutable INSERT found in dump")
    if not inventory_lines:
        raise RuntimeError("No actualinventory INSERT found in dump")
    if not price_lines:
        raise RuntimeError("No cardprices INSERT found in dump")

    print("Parsing rows...")
    card_rows = parse_insert_values(card_info_line)
    sku_rows = parse_insert_values(sku_table_line)
    inventory_rows = [r for l in inventory_lines for r in parse_insert_values(l)]
    price_rows = [r for l in price_lines for r in parse_insert_values(l)]

    print(f"  cardinfo:        {len(card_rows)} rows")
    print(f"  skutable:        {len(sku_rows)} rows")
    print(f"  actualinventory: {len(inventory_rows)} rows")
    print(f"  cardprices:      {len(price_rows)} rows")

    init_database(db_path)
    db = get_db()

    # ── 1. Migrate cards ──
    print("\nMigrating cards...")
    insert_card = (
        "INSERT OR IGNORE INTO cards (id, card_name, set_name, product_line, card_type, "
        "visual_layout, rarity, card_number, product_type, era, set_type) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    card_count = 0
    db.execute("BEGIN")
    for row in card_rows:
        if not row or not row[0]:
            continue
        db.execute(
            insert_card,
            (
                row[0],
                row[1],
                _nullable_str(row[2]),
                _nullable_str(row[3]),
                _nullable_str(row[4]),
                _nullable_str(row[5]),
                _nullable_str(row[6]),
                _nullable_str(row[7]),
                _nullable_str(row[8]),
                _nullable_str(row[9]),
                _nullable_str(row[10]),
            ),
        )
        card_count += 1
    db.execute("COMMIT")
    print(f"  Inserted {card_count} cards")

    # ── 2. Migrate SKUs (build hash->id mapping) ──
    print("Migrating SKUs...")
    insert_sku = (
        "INSERT OR IGNORE INTO skus (card_id, condition, finish, specialty_one, specialty_two, "
        "qty, latest_calc_date) VALUES (?, ?, ?, ?, ?, ?, ?)"
    )
    get_sku_id = (
        "SELECT sku_id FROM skus WHERE card_id = ? AND condition = ? AND finish = ? "
        "AND specialty_one = ? AND specialty_two = ?"
    )

    sku_hash_to_id: dict[str, int] = {}
    sku_count = 0
    sku_skipped = 0

    db.execute("BEGIN")
    for row in sku_rows:
        card_id = row[0]
        condition = row[1]
        finish = row[2]
        specialty_one = row[3]
        specialty_two = row[4]
        hashed_sku = row[6]
        qty = _nullable_int(row[7]) or 0
        latest_calc_date = _nullable_str(row[8])

        if not card_id:
            sku_skipped += 1
            continue

        card_exists = db.execute("SELECT 1 FROM cards WHERE id = ?", (card_id,)).fetchone()
        if not card_exists:
            sku_skipped += 1
            continue

        db.execute(
            insert_sku,
            (card_id, condition, finish, specialty_one, specialty_two, qty, latest_calc_date),
        )
        result = db.execute(
            get_sku_id, (card_id, condition, finish, specialty_one, specialty_two)
        ).fetchone()
        if result:
            sku_hash_to_id[hashed_sku] = result["sku_id"]
            sku_count += 1
    db.execute("COMMIT")
    print(f"  Inserted {sku_count} SKUs (skipped {sku_skipped})")

    # ── 3. Migrate inventory ──
    print("Migrating inventory...")
    insert_inventory = (
        "INSERT INTO inventory (sku_id, pricing_sku_id, qty, tags, status) VALUES (?, ?, ?, ?, 'unlisted')"
    )
    inv_count = 0
    inv_skipped = 0

    db.execute("BEGIN")
    for row in inventory_rows:
        sku_hash = row[0]
        tags = _nullable_str(row[1])
        qty = _nullable_int(row[3]) or 1
        pricing_sku_hash = _nullable_str(row[5])

        sku_id = sku_hash_to_id.get(sku_hash)
        if not sku_id:
            inv_skipped += 1
            continue

        pricing_sku_id = (
            sku_hash_to_id.get(pricing_sku_hash)
            if pricing_sku_hash and pricing_sku_hash != sku_hash
            else None
        )

        db.execute(insert_inventory, (sku_id, pricing_sku_id, qty, tags))
        inv_count += 1
    db.execute("COMMIT")
    print(f"  Inserted {inv_count} inventory items (skipped {inv_skipped})")

    # ── 4. Migrate prices ──
    print("Migrating prices...")
    insert_price = (
        "INSERT OR IGNORE INTO prices (sku_id, calculation_date, estimated_price, "
        "estimated_liquid_value, confidence_percent, manual_check_necessary, manually_checked, "
        "algorithm_version, estimated_low_price, estimated_high_price, estimated_low_price_liquid, "
        "estimated_high_price_liquid) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    price_count = 0
    price_skipped = 0

    db.execute("BEGIN")
    for row in price_rows:
        sku_hash = row[0]
        sku_id = sku_hash_to_id.get(sku_hash)
        if not sku_id:
            price_skipped += 1
            continue

        db.execute(
            insert_price,
            (
                sku_id,
                row[1],
                _nullable_num(row[2]),
                _nullable_num(row[3]),
                _nullable_int(row[4]),
                _nullable_int(row[5]),
                _nullable_int(row[6]) or 0,
                _nullable_str(row[7]),
                _nullable_num(row[8]),
                _nullable_num(row[9]),
                _nullable_num(row[10]),
                _nullable_num(row[11]),
            ),
        )
        price_count += 1
    db.execute("COMMIT")
    print(f"  Inserted {price_count} price records (skipped {price_skipped})")

    print("\n" + "=" * 39)
    print("Migration complete!")
    print(f"  Cards:     {card_count}")
    print(f"  SKUs:      {sku_count}")
    print(f"  Inventory: {inv_count}")
    print(f"  Prices:    {price_count}")
    print(f"  DB:        {db_path}")
    print("=" * 39)

    close_database()


def main() -> None:
    args = _parse_args()
    try:
        migrate(args.dump_path, args.db_path)
    except Exception as err:  # noqa: BLE001
        print(f"Migration failed: {err}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
