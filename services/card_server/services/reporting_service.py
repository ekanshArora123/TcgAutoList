"""ReportingService — Read-only collection browse + analytics for the dashboard.

Owns every query the dashboard backend used to run with raw SQL. The dashboard
(Flask `app.py`) is now a thin HTTP layer that parses query params, calls these
methods, and serializes the result — it holds no SQL and no DB connection logic.

All inventory+price reads build on `INV_SKU_CARD_PRICE_FROM` (the canonical
"owned card with its current price" join, defined once in the inventory helper),
so the join lives in exactly one place. Ported from the SQL that lived in
dashboard/backend/app.py. Pure reads — no writes.
"""

from __future__ import annotations

import math
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Optional

from ..helpers.crud.cards import CardsHelper
from ..helpers.crud.inventory import INV_SKU_CARD_PRICE_FROM, InventoryHelper
from ..helpers.crud.skus import SkusHelper

# Collection browse additionally surfaces the latest market snapshot per SKU.
_MARKET_JOIN = """
    LEFT JOIN market_snapshots ms ON s.card_id = ms.card_id
        AND s.condition = ms.condition AND s.finish = ms.finish
        AND ms.snapshot_date = (
            SELECT MAX(snapshot_date) FROM market_snapshots ms2
            WHERE ms2.card_id = ms.card_id AND ms2.condition = ms.condition
              AND ms2.finish = ms.finish
        )
"""

# Sort keys the API exposes -> qualified columns (whitelist; never interpolate
# user input into ORDER BY directly).
_SORT_COLUMNS = {
    "card_name": "c.card_name",
    "set_name": "c.set_name",
    "era": "c.era",
    "rarity": "c.rarity",
    "condition": "s.condition",
    "estimated_price": "p.estimated_price",
    "confidence": "p.confidence_percent",
    "status": "i.status",
    "card_number": "c.card_number",
}

# Dimensions the `breakdown()` rollup supports. `col` is grouped/ordered on,
# `name` is the output column alias when coalesced. era additionally surfaces
# total_qty + avg_confidence.
_BREAKDOWN_DIMS = {
    "era": {"col": "c.era", "name": "era", "coalesce": True, "extra_aggs": True, "order": "total_value DESC"},
    "rarity": {"col": "c.rarity", "name": "rarity", "coalesce": True, "extra_aggs": False, "order": "total_value DESC"},
    "set": {"col": "c.set_name", "name": "set_name", "coalesce": True, "extra_aggs": False, "order": "total_value DESC"},
    "condition": {"col": "s.condition", "name": "condition", "coalesce": False, "extra_aggs": False, "order": "quantity DESC"},
}

_CARD_COLUMNS = """
    i.inventory_id, i.sku_id, i.qty, i.tags, i.status,
    i.front_photo_path, i.back_photo_path, i.ebay_listing_id,
    s.condition, s.finish, s.card_id, s.specialty_one, s.specialty_two,
    c.card_name, c.set_name, c.rarity, c.card_number, c.era,
    c.card_type, c.visual_layout,
    p.estimated_price, p.estimated_liquid_value,
    p.confidence_percent, p.manual_check_necessary,
    p.estimated_low_price, p.estimated_high_price,
    p.calculation_date, p.algorithm_version
"""

_COLLECTION_COLUMNS = """
    i.inventory_id, i.sku_id, i.qty, i.tags, i.status,
    i.front_photo_path, i.back_photo_path, i.ebay_listing_id,
    s.condition, s.finish, s.card_id, s.specialty_one, s.specialty_two,
    c.card_name, c.set_name, c.rarity, c.card_number, c.era,
    c.card_type, c.visual_layout, c.product_type,
    p.estimated_price, p.estimated_liquid_value,
    p.confidence_percent, p.manual_check_necessary, p.manually_checked,
    p.estimated_low_price, p.estimated_high_price,
    p.estimated_low_price_liquid, p.estimated_high_price_liquid,
    p.calculation_date, p.algorithm_version,
    ms.listing_count, ms.lowest_listing_price,
    ms.median_listing_price, ms.mean_listing_price,
    ms.p25_listing_price, ms.p75_listing_price,
    ms.recent_sales_count, ms.avg_sale_price,
    ms.median_sale_price, ms.min_sale_price, ms.max_sale_price,
    ms.newest_sale_date, ms.oldest_sale_date,
    ms.snapshot_date as market_snapshot_date
"""


def _build_filters(f: dict[str, Any]) -> tuple[list[str], list[Any]]:
    """Translate a filters dict into parametrized WHERE conditions.

    Every key is optional; absent/blank keys contribute nothing. Shared by both
    browse methods so the filter vocabulary is defined once.
    """
    conditions: list[str] = []
    params: list[Any] = []

    def add(cond: str, value: Any) -> None:
        conditions.append(cond)
        params.append(value)

    if f.get("q"):
        add("c.card_name LIKE ?", f"%{f['q']}%")
    if f.get("set_name"):
        add("c.set_name = ?", f["set_name"])
    if f.get("era"):
        add("c.era = ?", f["era"])
    if f.get("rarity"):
        add("c.rarity = ?", f["rarity"])
    if f.get("condition"):
        add("s.condition = ?", f["condition"])
    if f.get("finish"):
        add("s.finish = ?", f["finish"])
    if f.get("status"):
        add("i.status = ?", f["status"])
    if f.get("specialty"):
        add("s.specialty_one = ?", f["specialty"])
    if f.get("tags_contain"):
        add("i.tags LIKE ?", f"%{f['tags_contain']}%")
    if f.get("price_min") not in (None, ""):
        add("p.estimated_price >= ?", float(f["price_min"]))
    if f.get("price_max") not in (None, ""):
        add("p.estimated_price <= ?", float(f["price_max"]))
    if f.get("confidence_min") not in (None, ""):
        add("p.confidence_percent >= ?", int(f["confidence_min"]))
    if f.get("confidence_max") not in (None, ""):
        add("p.confidence_percent <= ?", int(f["confidence_max"]))
    if f.get("manual_check") == "true":
        conditions.append("p.manual_check_necessary = 1 AND p.manually_checked = 0")

    return conditions, params


def _resolve_sort(sort: Optional[str], order: Optional[str]) -> tuple[str, str]:
    sort_col = _SORT_COLUMNS.get((sort or "").strip(), "c.card_name")
    order_dir = (order or "").strip().upper()
    order_dir = order_dir if order_dir in ("ASC", "DESC") else "ASC"
    return sort_col, order_dir


def _paginate(page: Any, per_page: Any, default_per_page: int) -> tuple[int, int, int]:
    page = max(int(page or 1), 1)
    per_page = min(int(per_page or default_per_page), 200)
    return page, per_page, (page - 1) * per_page


def _digit_ids(card_ids: Any) -> list[str]:
    """Keep only numeric TCGplayer IDs — safe to inline in an IN(...) list."""
    return [str(c) for c in (card_ids or []) if str(c).isdigit()]


class ReportingService:
    def __init__(self, db: sqlite3.Connection):
        self.db = db
        self.cards = CardsHelper(db)
        self.skus = SkusHelper(db)
        self.inventory = InventoryHelper(db)

    # ─── Collection browse ───────────────────────────────────

    def browse_cards(self, filters: dict[str, Any]) -> dict[str, Any]:
        """Paginated card list with search/filter/sort (the table view)."""
        conditions, params = _build_filters(filters)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sort_col, order_dir = _resolve_sort(filters.get("sort"), filters.get("order"))
        page, per_page, offset = _paginate(filters.get("page"), filters.get("per_page"), 50)

        total = self.db.execute(
            f"SELECT COUNT(*) as total {INV_SKU_CARD_PRICE_FROM} {where}", params
        ).fetchone()["total"]

        rows = self.db.execute(
            f"SELECT {_CARD_COLUMNS} {INV_SKU_CARD_PRICE_FROM} {where} "
            f"ORDER BY {sort_col} {order_dir} LIMIT ? OFFSET ?",
            params + [per_page, offset],
        ).fetchall()

        return _page_result([dict(r) for r in rows], total, page, per_page)

    def browse_collection(
        self,
        filters: dict[str, Any],
        image_card_ids: Optional[list[str]] = None,
        has_image: str = "",
    ) -> dict[str, Any]:
        """Image-grid view: card browse + latest market snapshot + image filter.

        `image_card_ids` are the card IDs that have an image on disk (a
        filesystem concern the caller owns); `has_image` of 'true'/'false'
        filters rows to/from that set.
        """
        conditions, params = _build_filters(filters)

        if has_image in ("true", "false"):
            ids = _digit_ids(image_card_ids)
            id_list = ",".join(ids)
            if has_image == "true":
                conditions.append(f"s.card_id IN ({id_list})" if id_list else "1=0")
            else:
                conditions.append(f"s.card_id NOT IN ({id_list})" if id_list else "1=1")

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sort_col, order_dir = _resolve_sort(filters.get("sort"), filters.get("order"))
        page, per_page, offset = _paginate(filters.get("page"), filters.get("per_page"), 48)

        from_clause = INV_SKU_CARD_PRICE_FROM + _MARKET_JOIN

        total = self.db.execute(
            f"SELECT COUNT(*) as total {from_clause} {where}", params
        ).fetchone()["total"]

        rows = self.db.execute(
            f"SELECT {_COLLECTION_COLUMNS} {from_clause} {where} "
            f"ORDER BY {sort_col} {order_dir} LIMIT ? OFFSET ?",
            params + [per_page, offset],
        ).fetchall()

        image_set = set(_digit_ids(image_card_ids))
        items = []
        for r in rows:
            item = dict(r)
            item["has_image"] = str(item["card_id"]) in image_set
            items.append(item)

        return _page_result(items, total, page, per_page)

    def card_detail(
        self,
        card_id: str,
        finish: str = "Regular",
        specialty_one: str = "None",
        specialty_two: str = "None",
        image_card_ids: Optional[list[str]] = None,
    ) -> Optional[dict[str, Any]]:
        """One card's detail page: metadata + a per-condition qty rollup.

        Identity is the SKU minus condition — (card_id, finish, specialty_one,
        specialty_two) — so a Reverse-Holo, a 1st Edition, and a graded slab each
        resolve to their own page, while every condition of the same variant is
        folded together here. Tagged inventory is excluded (tags get their own
        treatment later), so this view reflects the plain variant "in general"
        rather than one specific physical card.

        Returns None when the card_id is unknown. Conditions with no untagged
        inventory simply don't appear; the list may be empty.
        """
        card = self.cards.get_by_id(card_id)
        if not card:
            return None

        rows = self.db.execute(
            """
            SELECT s.condition,
                   SUM(i.qty) as qty,
                   MAX(p.estimated_price) as estimated_price,
                   MAX(p.confidence_percent) as confidence_percent
            """
            + INV_SKU_CARD_PRICE_FROM
            + """
            WHERE s.card_id = ? AND s.finish = ?
              AND s.specialty_one = ? AND s.specialty_two = ?
              AND (i.tags IS NULL OR TRIM(i.tags) = '')
            GROUP BY s.condition
            """,
            [card_id, finish, specialty_one, specialty_two],
        ).fetchall()

        conditions = [dict(r) for r in rows]
        total_qty = sum(r["qty"] or 0 for r in conditions)
        image_set = set(_digit_ids(image_card_ids))

        return {
            "card": {
                "card_id": card["id"],
                "card_name": card["card_name"],
                "set_name": card["set_name"],
                "rarity": card["rarity"],
                "card_number": card["card_number"],
                "era": card["era"],
                "card_type": card["card_type"],
                "finish": finish,
                "specialty_one": specialty_one,
                "specialty_two": specialty_two,
            },
            "has_image": str(card_id) in image_set,
            "conditions": conditions,
            "total_qty": total_qty,
        }

    def card_sales_history(
        self,
        card_id: str,
        finish: str = "Regular",
        days: int = 365,
        source: str = "tcgplayer",
    ) -> dict[str, Any]:
        """Daily sales rollup for the per-card graph, one series per condition.

        Reads the raw `sales` table (never the pricing tables). Each point is a
        day's average sale price (purchase + shipping) and volume for one
        condition. Sales are keyed by card_id + condition + finish, so a 1st
        Edition (its own product id) or Reverse-Holo resolves correctly via the
        card_id/finish the page passes in.
        """
        days = max(int(days), 1)
        cutoff = (datetime.now() - timedelta(days=days)).date().isoformat()

        rows = self.db.execute(
            """
            SELECT condition,
                   substr(order_date, 1, 10) AS date,
                   AVG(purchase_price + shipping_price) AS avg_price,
                   MIN(purchase_price + shipping_price) AS min_price,
                   MAX(purchase_price + shipping_price) AS max_price,
                   SUM(quantity) AS volume
            FROM sales
            WHERE card_id = ? AND finish = ? AND source = ? AND order_date >= ?
            GROUP BY condition, date
            ORDER BY date
            """,
            (card_id, finish, source, cutoff),
        ).fetchall()

        points = [
            {
                "date": r["date"],
                "condition": r["condition"],
                "avg_price": round(r["avg_price"], 2) if r["avg_price"] is not None else None,
                "min_price": round(r["min_price"], 2) if r["min_price"] is not None else None,
                "max_price": round(r["max_price"], 2) if r["max_price"] is not None else None,
                "volume": r["volume"] or 0,
            }
            for r in rows
        ]
        conditions = sorted({p["condition"] for p in points})

        return {
            "card_id": card_id,
            "finish": finish,
            "source": source,
            "days": days,
            "conditions": conditions,
            "points": points,
        }

    def filter_options(self) -> dict[str, list[str]]:
        """Distinct values for the filter dropdowns."""
        return {
            "sets": self.cards.get_all_set_names(),
            "eras": self.cards.get_all_eras(),
            "rarities": self.cards.get_all_rarities(),
            "conditions": self.skus.get_all_conditions(),
            "finishes": self.skus.get_all_finishes(),
            "statuses": self.inventory.get_all_statuses(),
        }

    # ─── Analytics ───────────────────────────────────────────

    def analytics_summary(self) -> dict[str, Any]:
        """High-level collection stats."""
        total_cards = self.db.execute("SELECT COUNT(*) as c FROM inventory").fetchone()["c"]
        unique_cards = self.db.execute("SELECT COUNT(*) as c FROM cards").fetchone()["c"]
        total_skus = self.db.execute("SELECT COUNT(*) as c FROM skus").fetchone()["c"]

        value_row = self.db.execute(
            """
            SELECT
                SUM(p.estimated_price * i.qty) as total_value,
                SUM(p.estimated_liquid_value * i.qty) as total_liquid_value,
                AVG(p.estimated_price) as avg_price,
                MIN(p.estimated_price) as min_price,
                MAX(p.estimated_price) as max_price
            """
            + INV_SKU_CARD_PRICE_FROM
            + "WHERE p.estimated_price IS NOT NULL"
        ).fetchone()

        status_rows = self.db.execute(
            "SELECT status, COUNT(*) as count FROM inventory GROUP BY status"
        ).fetchall()
        by_status = {r["status"]: r["count"] for r in status_rows}

        manual_checks = self.db.execute(
            "SELECT COUNT(*) as c"
            + INV_SKU_CARD_PRICE_FROM
            + "WHERE p.manual_check_necessary = 1 AND p.manually_checked = 0"
        ).fetchone()["c"]

        avg_confidence = self.db.execute(
            "SELECT AVG(p.confidence_percent) as avg_conf"
            + INV_SKU_CARD_PRICE_FROM
            + "WHERE p.confidence_percent IS NOT NULL"
        ).fetchone()["avg_conf"]

        return {
            "total_inventory": total_cards,
            "unique_cards": unique_cards,
            "total_skus": total_skus,
            "total_value": value_row["total_value"],
            "total_liquid_value": value_row["total_liquid_value"],
            "avg_price": value_row["avg_price"],
            "min_price": value_row["min_price"],
            "max_price": value_row["max_price"],
            "by_status": by_status,
            "pending_manual_checks": manual_checks,
            "avg_confidence": avg_confidence,
        }

    def price_histogram(self, breaks: list[float]) -> list[dict[str, Any]]:
        """Bucket current-inventory prices into [breaks[i], breaks[i+1]) bins.

        The last break is the upper cap; prices >= it go into a single ">"
        overflow bucket. Raises ValueError if fewer than 2 breaks.
        """
        breaks = sorted(set(breaks))
        if len(breaks) < 2:
            raise ValueError("Need at least 2 breakpoints")

        rows = self.db.execute(
            "SELECT p.estimated_price"
            + INV_SKU_CARD_PRICE_FROM
            + "WHERE p.estimated_price IS NOT NULL"
        ).fetchall()

        bin_counts = [0] * (len(breaks) - 1)
        bin_values = [0.0] * (len(breaks) - 1)
        over_max_count = 0
        over_max_value = 0.0
        max_break = breaks[-1]

        for r in rows:
            price = r["estimated_price"]
            if price >= max_break:
                over_max_count += 1
                over_max_value += price
                continue
            for i in range(len(breaks) - 1):
                if breaks[i] <= price < breaks[i + 1]:
                    bin_counts[i] += 1
                    bin_values[i] += price
                    break

        def fmt(v: float) -> str:
            return f"${v:g}"

        result = []
        for i in range(len(breaks) - 1):
            result.append(
                {
                    "range": f"{fmt(breaks[i])}-{fmt(breaks[i + 1])}",
                    "count": bin_counts[i],
                    "total_value": round(bin_values[i], 2),
                }
            )
        if over_max_count:
            result.append(
                {
                    "range": f">{fmt(max_break)}",
                    "count": over_max_count,
                    "total_value": round(over_max_value, 2),
                }
            )
        return result

    def confidence_distribution(self) -> list[dict[str, Any]]:
        """Confidence-percent distribution in 10-point buckets."""
        rows = self.db.execute(
            """
            SELECT
                CASE
                    WHEN p.confidence_percent IS NULL THEN 'No Data'
                    WHEN p.confidence_percent >= 90 THEN '90-100'
                    WHEN p.confidence_percent >= 80 THEN '80-89'
                    WHEN p.confidence_percent >= 70 THEN '70-79'
                    WHEN p.confidence_percent >= 60 THEN '60-69'
                    WHEN p.confidence_percent >= 50 THEN '50-59'
                    WHEN p.confidence_percent >= 40 THEN '40-49'
                    WHEN p.confidence_percent >= 30 THEN '30-39'
                    WHEN p.confidence_percent >= 20 THEN '20-29'
                    WHEN p.confidence_percent >= 10 THEN '10-19'
                    ELSE '0-9'
                END as bucket,
                COUNT(*) as count
            """
            + INV_SKU_CARD_PRICE_FROM
            + "GROUP BY bucket ORDER BY bucket"
        ).fetchall()
        return [dict(r) for r in rows]

    def breakdown(self, dimension: str) -> list[dict[str, Any]]:
        """Quantity + value rollup of owned cards grouped by one dimension.

        `dimension` is one of _BREAKDOWN_DIMS (era/rarity/set/condition). Each
        returns rows of {<dimension>, quantity, total_value, avg_price}; the era
        view additionally yields total_qty + avg_confidence. Raises KeyError on an
        unknown dimension.
        """
        cfg = _BREAKDOWN_DIMS[dimension]
        label = (
            f"COALESCE({cfg['col']}, 'Unknown') as {cfg['name']}"
            if cfg["coalesce"]
            else cfg["col"]
        )
        extra = (
            ", SUM(i.qty) as total_qty, AVG(p.confidence_percent) as avg_confidence"
            if cfg["extra_aggs"]
            else ""
        )
        rows = self.db.execute(
            f"""
            SELECT
                {label},
                COUNT(*) as quantity{extra},
                SUM(CASE WHEN p.estimated_price IS NOT NULL THEN p.estimated_price * i.qty ELSE 0 END) as total_value,
                AVG(p.estimated_price) as avg_price
            """
            + INV_SKU_CARD_PRICE_FROM
            + f"GROUP BY {cfg['col']} ORDER BY {cfg['order']}"
        ).fetchall()
        return [dict(r) for r in rows]

    def top_cards(self, n: int = 25) -> list[dict[str, Any]]:
        """Top N most valuable owned cards."""
        n = min(int(n), 100)
        rows = self.db.execute(
            """
            SELECT
                c.card_name, c.set_name, c.era, c.rarity,
                s.condition, s.finish,
                p.estimated_price, p.estimated_liquid_value,
                p.confidence_percent,
                i.inventory_id, i.qty
            """
            + INV_SKU_CARD_PRICE_FROM
            + "WHERE p.estimated_price IS NOT NULL ORDER BY p.estimated_price DESC LIMIT ?",
            [n],
        ).fetchall()
        return [dict(r) for r in rows]


def _page_result(items: list[dict], total: int, page: int, per_page: int) -> dict[str, Any]:
    return {
        "items": items,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": math.ceil(total / per_page) if total else 0,
    }
