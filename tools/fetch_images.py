"""
Fetch card images from TCGplayer and save as .webp in data/card-images/.

Usage:
    python tools/fetch_images.py                  # fetch missing images for all inventory cards
    python tools/fetch_images.py 100503 100505    # fetch specific card IDs
    python tools/fetch_images.py --force 100503   # re-download even if exists

Reads the SQLite DB to find all unique card (product) IDs in inventory,
skips any that already have a .webp file, fetches the rest from TCGplayer's
product-images CDN, converts to webp, and saves.
"""

import argparse
import os
import sqlite3
import sys
import time
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image

# ── Paths ──────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DB_PATH = PROJECT_ROOT / "card-server" / "data" / "cards.db"
IMAGES_DIR = PROJECT_ROOT / "data" / "card-images"

# ── Config ─────────────────────────────────────────────────────

TCGPLAYER_IMAGE_URL = "https://product-images.tcgplayer.com/fit-in/437x437/{product_id}.jpg"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
    "Referer": "https://www.tcgplayer.com/",
}
DELAY_MS = 100          # ms between requests
RATE_LIMIT_PAUSE = 120  # seconds to wait on 429
MAX_RETRIES = 3
WEBP_QUALITY = 85


def get_inventory_card_ids() -> list[str]:
    """Return distinct card (product) IDs that are in inventory."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        rows = conn.execute(
            "SELECT DISTINCT c.id FROM inventory i "
            "JOIN skus s ON i.sku_id = s.sku_id "
            "JOIN cards c ON s.card_id = c.id "
            "ORDER BY c.id"
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def existing_images() -> set[str]:
    """Return set of card IDs that already have a .webp file."""
    if not IMAGES_DIR.exists():
        return set()
    return {f.stem for f in IMAGES_DIR.glob("*.webp")}


def fetch_and_save(product_id: str) -> bool:
    """Fetch image from TCGplayer, convert to webp, save. Returns True on success."""
    url = TCGPLAYER_IMAGE_URL.format(product_id=product_id)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)

            if resp.status_code == 429:
                print(f"  Rate limited. Pausing {RATE_LIMIT_PAUSE}s...")
                time.sleep(RATE_LIMIT_PAUSE)
                continue

            if resp.status_code == 404:
                print(f"  {product_id}: not found on TCGplayer (404)")
                return False

            resp.raise_for_status()

            img = Image.open(BytesIO(resp.content))
            out_path = IMAGES_DIR / f"{product_id}.webp"
            img.save(str(out_path), "WEBP", quality=WEBP_QUALITY)
            return True

        except requests.RequestException as e:
            if attempt < MAX_RETRIES:
                print(f"  {product_id}: attempt {attempt} failed ({e}), retrying...")
                time.sleep(2)
            else:
                print(f"  {product_id}: FAILED after {MAX_RETRIES} attempts ({e})")
                return False
        except Exception as e:
            print(f"  {product_id}: image processing error ({e})")
            return False

    return False


def main():
    parser = argparse.ArgumentParser(description="Fetch TCGplayer card images")
    parser.add_argument("ids", nargs="*", help="Specific card IDs to fetch (default: all inventory)")
    parser.add_argument("--force", action="store_true", help="Re-download even if image exists")
    args = parser.parse_args()

    IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    if args.ids:
        card_ids = args.ids
    else:
        card_ids = get_inventory_card_ids()

    if not args.force:
        have = existing_images()
        card_ids = [cid for cid in card_ids if cid not in have]

    total = len(card_ids)
    if total == 0:
        print("All images already downloaded.")
        return

    print(f"Fetching {total} card images...")
    success = 0
    fail = 0
    not_found = 0

    for i, cid in enumerate(card_ids, 1):
        if i % 50 == 0 or i == 1:
            print(f"[{i}/{total}] ({success} ok, {fail} fail, {not_found} not found)")

        result = fetch_and_save(cid)
        if result:
            success += 1
        else:
            # Distinguish 404 from other failures by checking if file was created
            if not (IMAGES_DIR / f"{cid}.webp").exists():
                # Check if it was a 404 (logged as "not found")
                fail += 1
            else:
                success += 1

        if i < total:
            time.sleep(DELAY_MS / 1000)

    print(f"\nDone! {success} downloaded, {fail} failed out of {total}")


if __name__ == "__main__":
    main()
