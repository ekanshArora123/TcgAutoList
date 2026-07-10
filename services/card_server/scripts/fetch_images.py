"""
Fetch card images from TCGplayer and save as .webp in data/card-images/.

Usage:
    python services/card_server/scripts/fetch_images.py                  # fetch missing images for all inventory cards
    python services/card_server/scripts/fetch_images.py 100503 100505    # fetch specific card IDs
    python services/card_server/scripts/fetch_images.py --force 100503   # re-download even if exists

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
from typing import Optional

import httpx
from PIL import Image

# ── Paths ──────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent
CARD_SERVER_ROOT = SCRIPT_DIR.parent
PROJECT_ROOT = CARD_SERVER_ROOT.parent.parent  # services/card_server -> services -> project root
DB_PATH = CARD_SERVER_ROOT / "data" / "cards.db"
IMAGES_DIR = PROJECT_ROOT / "data" / "card-images"
# Graded slab images (from the grading company, e.g. PSA), one folder per cert:
#   data/graded-card-images/{cert}/{cert}f.webp   (front)
#   data/graded-card-images/{cert}/{cert}b.webp   (back)
GRADED_IMAGES_DIR = PROJECT_ROOT / "data" / "graded-card-images"

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


def save_webp_from_url(
    url: str,
    out_path: Path,
    *,
    headers: dict = HEADERS,
    timeout: int = 15,
    retries: int = MAX_RETRIES,
    rate_limit_pause: int = RATE_LIMIT_PAUSE,
    force: bool = False,
) -> bool:
    """Download an image from ``url``, convert to webp, save to ``out_path``.
    Returns True iff the file exists afterward (already-present counts as success).
    Silent — the caller logs. The generic primitive both the TCGplayer card fetch
    and the PSA slab fetch build on. Pass ``retries=1, rate_limit_pause=0`` to fail
    fast (never hang a web request on the 429 sleep)."""
    out_path = Path(out_path)
    if out_path.exists() and not force:
        return True
    for attempt in range(1, retries + 1):
        try:
            resp = httpx.get(url, headers=headers, timeout=timeout, follow_redirects=True)
            if resp.status_code == 429:
                if rate_limit_pause <= 0:
                    return False  # fail fast (on-demand path)
                time.sleep(rate_limit_pause)
                continue
            if resp.status_code == 404:
                return False  # no such image
            resp.raise_for_status()

            out_path.parent.mkdir(parents=True, exist_ok=True)
            Image.open(BytesIO(resp.content)).save(str(out_path), "WEBP", quality=WEBP_QUALITY)
            return True
        except httpx.HTTPError:
            if attempt < retries:
                time.sleep(2)
            else:
                return False
        except Exception:
            return False  # image processing / decode error
    return False


def fetch_card_image(
    product_id: str,
    images_dir: Path = IMAGES_DIR,
    *,
    retries: int = MAX_RETRIES,
    rate_limit_pause: int = RATE_LIMIT_PAUSE,
    timeout: int = 15,
    force: bool = False,
) -> bool:
    """Gather ONE raw card image by TCGplayer product id → ``images_dir/{id}.webp``.
    The reusable "get a raw image by id" primitive, shared by the CLI backfill
    (default retry + 120s 429 pause) and the dashboard's on-demand image endpoint
    (``retries=1, rate_limit_pause=0`` to fail fast)."""
    return save_webp_from_url(
        TCGPLAYER_IMAGE_URL.format(product_id=product_id),
        Path(images_dir) / f"{product_id}.webp",
        retries=retries,
        rate_limit_pause=rate_limit_pause,
        timeout=timeout,
        force=force,
    )


def store_graded_images(
    cert_id: str,
    front_url: Optional[str],
    back_url: Optional[str],
    images_dir: Path = GRADED_IMAGES_DIR,
    *,
    force: bool = False,
) -> dict[str, bool]:
    """Download a graded slab's front/back images into a per-cert folder:
    ``images_dir/{cert}/{cert}f.webp`` and ``.../{cert}b.webp``. PSA shows both or
    neither; pass whatever URLs were scraped (None to skip a side). Fails fast
    (single attempt, no 429 sleep) — best-effort at add time. Returns
    ``{"front": bool, "back": bool}`` indicating which sides are now stored."""
    folder = Path(images_dir) / str(cert_id)
    result = {"front": False, "back": False}
    for side, url in (("front", front_url), ("back", back_url)):
        if not url:
            continue
        suffix = "f" if side == "front" else "b"
        result[side] = save_webp_from_url(
            url, folder / f"{cert_id}{suffix}.webp",
            retries=1, rate_limit_pause=0, force=force,
        )
    return result


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

    for i, cid in enumerate(card_ids, 1):
        if i % 50 == 0 or i == 1:
            print(f"[{i}/{total}] ({success} ok, {fail} fail)")

        if fetch_card_image(cid, force=args.force):
            success += 1
        else:
            fail += 1

        if i < total:
            time.sleep(DELAY_MS / 1000)

    print(f"\nDone! {success} downloaded/present, {fail} failed out of {total}")


if __name__ == "__main__":
    main()
