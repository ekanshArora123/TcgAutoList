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
import statistics
from datetime import datetime, timedelta
from typing import Any, Optional

from ..helpers.crud.cards import CardsHelper
from ..helpers.crud.graded_inventory import GRADED_INV_FROM, GradedInventoryHelper
from ..helpers.crud.graded_skus import GradedSkusHelper
from ..helpers.crud.inventory import INV_SKU_CARD_PRICE_FROM, InventoryHelper
from ..helpers.crud.skus import SkusHelper
from ..helpers.crud.tags import parse_tags

# Slab tag that marks a cert for cracking (removal from the slab + regrade). A
# plain reused tag on graded_inventory.tags — no dedicated status/queue. The
# frontend uses the same literal.
GRADED_TO_CRACK_TAG = "to_crack"

# Pseudo tag-filter option for slabs carrying no tag at all. Not a real tag —
# a reserved value in the graded `tags` filter (and its dropdown) that matches
# untagged slabs, so it ORs alongside real tags like any other predicate.
GRADED_NO_TAG_OPTION = "No tags"

# ── Card-show browse mode ──────────────────────────────────
# The dashboard doubles as a customer-facing browse screen at card shows, where
# flawed inventory must not be on display. Anything carrying a tag (hidden
# creases, surface scratches, holo bleeds, …) is hidden from every reporting
# read.
#
# The predicate is folded into the skus JOIN of the canonical inventory join
# rather than each query's WHERE: that join is an INNER JOIN, so an ON-clause
# predicate is exactly equivalent to a WHERE, and this applies it to all ~13
# reporting queries (browse, detail, analytics, breakdowns) in one place. The
# original constant is left untouched so the write/orchestrator path that also
# imports it still sees every card.
# MINT is excluded outright; see _CONDITION_DISPLAY for the grades that are
# relabelled rather than dropped.
_EXCLUDED_CONDITIONS = ("MINT",)

_SHOW_MODE_PREDICATE = (
    "AND (i.tags IS NULL OR TRIM(i.tags) = '')"
    + " AND s.condition NOT IN (" + ",".join(f"'{c}'" for c in _EXCLUDED_CONDITIONS) + ")"
)

_SKUS_JOIN = "JOIN skus s ON i.sku_id = s.sku_id"
assert _SKUS_JOIN in INV_SKU_CARD_PRICE_FROM, "inventory join changed shape"
_SHOW_FROM = INV_SKU_CARD_PRICE_FROM.replace(
    _SKUS_JOIN, f"{_SKUS_JOIN} {_SHOW_MODE_PREDICATE}"
)

# Stored condition -> the grade a customer is shown.
#
# Two collapses, both display-only:
#   * `DM` is an internal alias for DMG; showing both is two names for one grade.
#   * TCGplayer has no in-between grades, so LP-NM/MP-LP/HP-MP/DM-HP mean nothing
#     to a buyer. Each rounds UP to its better neighbour (the pair's first entry
#     in pricing config's IN_BETWEEN_CONDITIONS).
#
# What a card sells FOR is untouched: an LP-NM still carries its own, lower,
# LP-NM price. The stored value, the pricing path, and the market-snapshot join
# all keep the real condition — only the label changes.
_CONDITION_DISPLAY = {
    "DM": "DMG",
    "LP-NM": "NM",
    "MP-LP": "LP",
    "HP-MP": "MP",
    "DM-HP": "HP",
}

# Display grades best -> worst, for ordering. The in-between grades are gone by
# construction — nothing maps to them.
_DISPLAY_CONDITION_ORDER = ("NM", "LP", "MP", "HP", "DMG")


def _case(col: str, mapping: dict[str, Any], default: str) -> str:
    """A SQL CASE over `col`. Keys/values are module constants, never user input."""
    whens = " ".join(f"WHEN '{k}' THEN {v!r}" if isinstance(v, str) else f"WHEN '{k}' THEN {v}"
                     for k, v in mapping.items())
    return f"CASE {col} {whens} ELSE {default} END"


# The displayed grade, and a best->worst rank to ORDER BY (alphabetical order on
# a grade name is meaningless to a buyer).
_SHOW_CONDITION = _case("s.condition", _CONDITION_DISPLAY, "s.condition")
_CONDITION_RANK = _case(
    _SHOW_CONDITION,
    {c: i for i, c in enumerate(_DISPLAY_CONDITION_ORDER)},
    str(len(_DISPLAY_CONDITION_ORDER)),
)


def _display_conditions(stored: list[str]) -> list[str]:
    """Stored grades -> the deduped, grade-ordered list the dropdown offers."""
    shown = {_CONDITION_DISPLAY.get(c, c) for c in stored if c not in _EXCLUDED_CONDITIONS}
    rank = {c: i for i, c in enumerate(_DISPLAY_CONDITION_ORDER)}
    return sorted(shown, key=lambda c: (rank.get(c, len(rank)), c))


def _raw_conditions(shown: str) -> list[str]:
    """Every stored condition that displays as `shown` — the expansion a filter on
    a displayed grade needs (asking for DMG must also match stored `DM`)."""
    raws = [raw for raw, display in _CONDITION_DISPLAY.items() if display == shown]
    if shown not in _CONDITION_DISPLAY:
        raws.append(shown)  # `shown` is itself a stored grade
    return raws


# Collection browse additionally surfaces the latest market snapshot per SKU.
# specialty_one is part of the match: a 1st Edition card must not display the
# Unlimited printing's listing stats (they are separate products).
_MARKET_JOIN = """
    LEFT JOIN market_snapshots ms ON s.card_id = ms.card_id
        AND s.condition = ms.condition AND s.finish = ms.finish
        AND s.specialty_one = ms.specialty_one
        AND ms.snapshot_date = (
            SELECT MAX(snapshot_date) FROM market_snapshots ms2
            WHERE ms2.card_id = ms.card_id AND ms2.condition = ms.condition
              AND ms2.finish = ms.finish AND ms2.specialty_one = ms.specialty_one
        )
"""

# Sort keys the API exposes -> qualified columns (whitelist; never interpolate
# user input into ORDER BY directly).
_SORT_COLUMNS = {
    "card_name": "c.card_name",
    "set_name": "c.set_name",
    "era": "c.era",
    "rarity": "c.rarity",
    "condition": _CONDITION_RANK,
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
    "condition": {"col": _SHOW_CONDITION, "name": "condition", "coalesce": False, "extra_aggs": False, "order": "quantity DESC"},
}

_CARD_COLUMNS = f"""
    i.inventory_id, i.sku_id, i.qty, i.tags, i.status,
    i.front_photo_path, i.back_photo_path, i.ebay_listing_id,
    {_SHOW_CONDITION} as condition, s.finish, s.card_id, s.specialty_one, s.specialty_two,
    c.card_name, c.set_name, c.rarity, c.card_number, c.era,
    c.card_type, c.visual_layout,
    p.estimated_price, p.estimated_liquid_value,
    p.confidence_percent, p.manual_check_necessary,
    p.estimated_low_price, p.estimated_high_price,
    p.calculation_date, p.algorithm_version
"""

_COLLECTION_COLUMNS = f"""
    i.inventory_id, i.sku_id, i.qty, i.tags, i.status,
    i.front_photo_path, i.back_photo_path, i.ebay_listing_id,
    {_SHOW_CONDITION} as condition, s.finish, s.card_id, s.specialty_one, s.specialty_two,
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

    def add_in(col: str, raw: Any) -> None:
        """Multi-value (IN) filter. Accepts a list or a single scalar."""
        values = [v for v in (raw if isinstance(raw, (list, tuple)) else [raw]) if v not in (None, "")]
        if not values:
            return
        conditions.append(f"{col} IN ({','.join('?' * len(values))})")
        params.extend(values)

    if f.get("q"):
        add("c.card_name LIKE ?", f"%{f['q']}%")
    if f.get("set_name"):
        add("c.set_name = ?", f["set_name"])
    if f.get("era"):
        add("c.era = ?", f["era"])
    # Plural keys = multi-select (IN). era/set are mutually exclusive in the UI
    # (a set already implies an era), but the backend treats every key
    # independently — callers send only the ones they mean.
    if f.get("sets"):
        add_in("c.set_name", f["sets"])
    if f.get("eras"):
        add_in("c.era", f["eras"])
    if f.get("conditions"):
        shown = f["conditions"]
        shown = shown if isinstance(shown, (list, tuple)) else [shown]
        add_in("s.condition", [raw for c in shown if c for raw in _raw_conditions(c)])
    if f.get("rarity"):
        add("c.rarity = ?", f["rarity"])
    if f.get("condition"):
        add_in("s.condition", _raw_conditions(f["condition"]))
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


# ── Graded browse: its own filter/sort vocabulary (parallel to the raw ones
# above). Columns use the GRADED_INV_FROM aliases: i=graded_inventory,
# g=graded_skus, c=cards (LEFT JOIN, optional link), p=graded_prices. Adding a
# new graded filter/sort is a one-line change here. ──

# Graded analytics breakdown dimensions -> grouped column (whitelist).
_GRADED_BREAKDOWN_DIMS = {"grade": "g.grade", "company": "g.grading_company"}

# Sort keys the graded API exposes -> qualified columns (whitelist).
_GRADED_SORT_COLUMNS = {
    "grade": "g.grade",
    "card_name": "COALESCE(g.card_subject, c.card_name)",
    "set_name": "COALESCE(g.card_set, c.set_name)",
    "grading_company": "g.grading_company",
    "estimated_price": "p.estimated_price",
    "status": "i.status",
    "card_year": "g.card_year",
}


def _format_grade(g: Any) -> str:
    """Grade -> filter-option/display string: -1 sentinel -> 'ERR', whole numbers
    without a trailing .0 (10.0 -> '10'), halves kept (9.5 -> '9.5')."""
    if g is None:
        return ""
    g = float(g)
    if g < 0:
        return "ERR"
    return str(int(g)) if g.is_integer() else str(g)


def _parse_grade_filter(v: Any) -> Optional[float]:
    """Inverse of _format_grade for an incoming grade filter value."""
    s = str(v).strip()
    if not s:
        return None
    if s.upper() == "ERR":
        return -1.0
    try:
        return float(s)
    except ValueError:
        return None


def _build_graded_filters(f: dict[str, Any]) -> tuple[list[str], list[Any]]:
    """Translate a graded filters dict into parametrized WHERE conditions. Every
    key is optional. Shared by the graded browse (and future graded reads)."""
    conditions: list[str] = []
    params: list[Any] = []

    def add(cond: str, value: Any) -> None:
        conditions.append(cond)
        params.append(value)

    def as_list(raw: Any) -> list[Any]:
        return [v for v in (raw if isinstance(raw, (list, tuple)) else [raw]) if v not in (None, "")]

    if f.get("q"):
        add("COALESCE(g.card_subject, c.card_name) LIKE ?", f"%{f['q']}%")
    if f.get("card_id"):
        add("g.card_id = ?", f["card_id"])
    if f.get("grading_company"):
        add("g.grading_company = ?", f["grading_company"])
    if f.get("status"):
        add("i.status = ?", f["status"])
    if f.get("grade") not in (None, ""):
        gv = _parse_grade_filter(f["grade"])
        if gv is not None:
            add("g.grade = ?", gv)
    # Multi-select grades (IN).
    if f.get("grades"):
        vals = [gv for gv in (_parse_grade_filter(v) for v in as_list(f["grades"])) if gv is not None]
        if vals:
            conditions.append(f"g.grade IN ({','.join('?' * len(vals))})")
            params.extend(vals)
    # Multi-select tags: slabs carrying ANY selected tag (comma-separated column).
    # The reserved "No tags" option matches untagged slabs, ORing in like any tag.
    if f.get("tags"):
        clauses: list[str] = []
        tag_params: list[Any] = []
        for t in (str(t).strip() for t in as_list(f["tags"])):
            if not t:
                continue
            if t == GRADED_NO_TAG_OPTION:
                clauses.append("(i.tags IS NULL OR TRIM(i.tags) = '')")
            else:
                clauses.append("i.tags LIKE ?")
                tag_params.append(f"%{t}%")
        if clauses:
            conditions.append("(" + " OR ".join(clauses) + ")")
            params.extend(tag_params)
    if f.get("tags_contain"):
        add("i.tags LIKE ?", f"%{f['tags_contain']}%")
    # Crack status (a dedicated 3-state on top of the to_crack tag): "yes" = to
    # crack, "no" = not to crack, anything else = no constraint.
    crack = f.get("crack")
    if crack == "yes":
        add("i.tags LIKE ?", "%to_crack%")
    elif crack == "no":
        conditions.append("(i.tags IS NULL OR i.tags NOT LIKE ?)")
        params.append("%to_crack%")

    return conditions, params


def _resolve_graded_sort(sort: Optional[str], order: Optional[str]) -> tuple[str, str]:
    sort_col = _GRADED_SORT_COLUMNS.get((sort or "").strip(), "g.grade")
    order_dir = (order or "").strip().upper()
    order_dir = order_dir if order_dir in ("ASC", "DESC") else "DESC"
    return sort_col, order_dir


def _bin_prices(prices: list[float], breaks: list[float]) -> list[dict[str, Any]]:
    """Bucket a pooled price list into [breaks[i], breaks[i+1]) bins + a ">" cap
    bucket. Dimension-agnostic (raw, graded, or both), so the histogram is the one
    place raw and graded prices combine."""
    bin_counts = [0] * (len(breaks) - 1)
    bin_values = [0.0] * (len(breaks) - 1)
    over_count = 0
    over_value = 0.0
    max_break = breaks[-1]

    for price in prices:
        if price >= max_break:
            over_count += 1
            over_value += price
            continue
        for i in range(len(breaks) - 1):
            if breaks[i] <= price < breaks[i + 1]:
                bin_counts[i] += 1
                bin_values[i] += price
                break

    def fmt(v: float) -> str:
        return f"${v:g}"

    result = [
        {
            "range": f"{fmt(breaks[i])}-{fmt(breaks[i + 1])}",
            "count": bin_counts[i],
            "total_value": round(bin_values[i], 2),
        }
        for i in range(len(breaks) - 1)
    ]
    if over_count:
        result.append({"range": f">{fmt(max_break)}", "count": over_count, "total_value": round(over_value, 2)})
    return result


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
        # Graded reads use a parallel chain; raw helpers/queries are unchanged.
        self.graded_skus = GradedSkusHelper(db)
        self.graded_inventory = GradedInventoryHelper(db)

    # ─── Collection browse ───────────────────────────────────

    def browse_cards(self, filters: dict[str, Any]) -> dict[str, Any]:
        """Paginated card list with search/filter/sort (the table view)."""
        conditions, params = _build_filters(filters)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sort_col, order_dir = _resolve_sort(filters.get("sort"), filters.get("order"))
        page, per_page, offset = _paginate(filters.get("page"), filters.get("per_page"), 50)

        total = self.db.execute(
            f"SELECT COUNT(*) as total {_SHOW_FROM} {where}", params
        ).fetchone()["total"]

        rows = self.db.execute(
            f"SELECT {_CARD_COLUMNS} {_SHOW_FROM} {where} "
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

        from_clause = _SHOW_FROM + _MARKET_JOIN

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
            f"""
            SELECT {_SHOW_CONDITION} as condition,
                   SUM(i.qty) as qty,
                   MAX(p.estimated_price) as estimated_price,
                   MAX(p.confidence_percent) as confidence_percent
            """
            + _SHOW_FROM
            + f"""
            WHERE s.card_id = ? AND s.finish = ?
              AND s.specialty_one = ? AND s.specialty_two = ?
            GROUP BY {_SHOW_CONDITION}
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
        include_images: bool = False,
        specialty_one: str = "None",
    ) -> dict[str, Any]:
        """Daily sales rollup for the per-card graph, one series per condition.

        Reads the raw `sales` table (never the pricing tables). Each point is a
        day's MEDIAN sale price (purchase + shipping) plus min/max and volume for
        one condition; the median is over sale rows (not quantity-weighted) and
        is outlier-resistant. Sales are keyed by card_id + condition + finish +
        specialty_one, so a 1st Edition or Reverse-Holo resolves to its own
        series via the variant axes the page passes in.

        Photo/custom listings (has_image = 1) are excluded by default since they
        price very differently; pass include_images=True to fold them in.
        """
        days = max(int(days), 1)
        cutoff = (datetime.now() - timedelta(days=days)).date().isoformat()
        image_clause = "" if include_images else " AND has_image = 0"

        rows = self.db.execute(
            f"""
            SELECT condition,
                   substr(order_date, 1, 10) AS date,
                   (purchase_price + shipping_price) AS price,
                   quantity
            FROM sales
            WHERE card_id = ? AND finish = ? AND specialty_one = ? AND source = ?
              AND order_date >= ?{image_clause}
            ORDER BY date
            """,
            (card_id, finish, specialty_one, source, cutoff),
        ).fetchall()

        # Group per (condition, day) and take the median price; SQLite has no
        # MEDIAN, so aggregate in Python.
        groups: dict[tuple[str, str], dict[str, Any]] = {}
        for r in rows:
            g = groups.setdefault((r["condition"], r["date"]), {"prices": [], "volume": 0})
            g["prices"].append(r["price"])
            g["volume"] += r["quantity"] or 1

        points = [
            {
                "date": date,
                "condition": cond,
                "median_price": round(statistics.median(g["prices"]), 2),
                "min_price": round(min(g["prices"]), 2),
                "max_price": round(max(g["prices"]), 2),
                "volume": g["volume"],
            }
            for (cond, date), g in groups.items()
        ]
        points.sort(key=lambda p: (p["date"], p["condition"]))
        conditions = sorted({p["condition"] for p in points})

        return {
            "card_id": card_id,
            "finish": finish,
            "specialty_one": specialty_one,
            "source": source,
            "days": days,
            "include_images": include_images,
            "conditions": conditions,
            "points": points,
        }

    def card_sales_points(
        self,
        card_id: str,
        finish: str = "Regular",
        days: int = 365,
        source: str = "tcgplayer",
        include_images: bool = False,
        specialty_one: str = "None",
    ) -> dict[str, Any]:
        """Individual sales as graph points (one per unit) for the scatter view.

        Each row is expanded by quantity (a qty-3 sale yields 3 points at its
        price) and carries the sale's full timestamp (order_date) so the chart
        can spread points by their real time within a day. Same filters as
        card_sales_history. Read-only.
        """
        days = max(int(days), 1)
        cutoff = (datetime.now() - timedelta(days=days)).date().isoformat()
        image_clause = "" if include_images else " AND has_image = 0"

        rows = self.db.execute(
            f"""
            SELECT condition,
                   order_date,
                   (purchase_price + shipping_price) AS price,
                   quantity
            FROM sales
            WHERE card_id = ? AND finish = ? AND specialty_one = ? AND source = ?
              AND order_date >= ?{image_clause}
            ORDER BY order_date
            """,
            (card_id, finish, specialty_one, source, cutoff),
        ).fetchall()

        points = []
        for r in rows:
            price = round(r["price"], 2)
            for _ in range(max(int(r["quantity"] or 1), 1)):
                points.append(
                    {"order_date": r["order_date"], "condition": r["condition"], "price": price}
                )
        conditions = sorted({p["condition"] for p in points})

        return {
            "card_id": card_id,
            "finish": finish,
            "specialty_one": specialty_one,
            "source": source,
            "days": days,
            "include_images": include_images,
            "conditions": conditions,
            "points": points,
        }

    def card_price_history(
        self,
        card_id: str,
        finish: str = "Regular",
        days: int = 365,
        source: str = "tcgplayer",
        specialty_one: str = "None",
    ) -> dict[str, Any]:
        """TCGplayer market-price history for the per-card graph, per condition.

        Reads `market_price_history` (weekly buckets), never the pricing tables.
        Mirrors card_sales_history's shape so the chart can overlay the two.
        """
        days = max(int(days), 1)
        cutoff = (datetime.now() - timedelta(days=days)).date().isoformat()

        rows = self.db.execute(
            """
            SELECT condition, bucket_date AS date, market_price
            FROM market_price_history
            WHERE card_id = ? AND finish = ? AND specialty_one = ? AND source = ?
              AND bucket_date >= ? AND market_price IS NOT NULL
            ORDER BY bucket_date
            """,
            (card_id, finish, specialty_one, source, cutoff),
        ).fetchall()

        points = [
            {"date": r["date"], "condition": r["condition"], "market_price": r["market_price"]}
            for r in rows
        ]
        conditions = sorted({p["condition"] for p in points})

        return {
            "card_id": card_id,
            "finish": finish,
            "specialty_one": specialty_one,
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
            "conditions": _display_conditions(self.skus.get_all_conditions()),
            "finishes": self.skus.get_all_finishes(),
            "statuses": self.inventory.get_all_statuses(),
        }

    # ─── Analytics ───────────────────────────────────────────

    def analytics_summary(self) -> dict[str, Any]:
        """High-level collection stats."""
        # Counted over the browse-mode join, not the raw tables, so the headline
        # numbers match what the grid actually shows.
        counts = self.db.execute(
            "SELECT COUNT(*) as total_cards,"
            " COUNT(DISTINCT s.card_id) as unique_cards,"
            " COUNT(DISTINCT i.sku_id) as total_skus"
            + _SHOW_FROM
        ).fetchone()
        total_cards = counts["total_cards"]
        unique_cards = counts["unique_cards"]
        total_skus = counts["total_skus"]

        value_row = self.db.execute(
            """
            SELECT
                SUM(p.estimated_price * i.qty) as total_value,
                SUM(p.estimated_liquid_value * i.qty) as total_liquid_value,
                AVG(p.estimated_price) as avg_price,
                MIN(p.estimated_price) as min_price,
                MAX(p.estimated_price) as max_price
            """
            + _SHOW_FROM
            + "WHERE p.estimated_price IS NOT NULL"
        ).fetchone()

        status_rows = self.db.execute(
            "SELECT i.status as status, COUNT(*) as count" + _SHOW_FROM + "GROUP BY i.status"
        ).fetchall()
        by_status = {r["status"]: r["count"] for r in status_rows}

        manual_checks = self.db.execute(
            "SELECT COUNT(*) as c"
            + _SHOW_FROM
            + "WHERE p.manual_check_necessary = 1 AND p.manually_checked = 0"
        ).fetchone()["c"]

        avg_confidence = self.db.execute(
            "SELECT AVG(p.confidence_percent) as avg_conf"
            + _SHOW_FROM
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

    def combined_analytics_summary(self) -> dict[str, Any]:
        """Pooled raw + graded headline stats for the analytics 'All' view — only
        the dimensions that make sense across both kinds (count, value, liquid,
        avg/max price). Raw-only stats (unique cards, confidence, manual review)
        are intentionally omitted. avg/max are computed over the pooled prices, not
        naively added."""
        stats_select = (
            "SELECT COUNT(*) AS rows,"
            " SUM(p.estimated_price * i.qty) AS value,"
            " SUM(p.estimated_liquid_value * i.qty) AS liquid,"
            " SUM(p.estimated_price) AS psum, COUNT(p.estimated_price) AS pcount,"
            " MAX(p.estimated_price) AS pmax"
        )
        raw = self.db.execute(stats_select + _SHOW_FROM).fetchone()
        graded = self.db.execute(stats_select + GRADED_INV_FROM).fetchone()

        def _sum(a: Any, b: Any) -> Any:
            return None if a is None and b is None else (a or 0) + (b or 0)

        pcount = (raw["pcount"] or 0) + (graded["pcount"] or 0)
        psum = (raw["psum"] or 0) + (graded["psum"] or 0)
        maxes = [m for m in (raw["pmax"], graded["pmax"]) if m is not None]
        return {
            "total_cards": (raw["rows"] or 0) + (graded["rows"] or 0),
            "total_value": _sum(raw["value"], graded["value"]),
            "total_liquid_value": _sum(raw["liquid"], graded["liquid"]),
            "avg_price": (psum / pcount) if pcount else None,
            "max_price": max(maxes) if maxes else None,
        }

    def price_histogram(
        self,
        breaks: list[float],
        filters: Optional[dict[str, Any]] = None,
        kind: str = "raw",
        graded_filters: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        """Bucket current-inventory prices into [breaks[i], breaks[i+1]) bins.

        `kind` selects which prices are pooled: "raw" (default), "graded", or
        "all" (both together — the combined collection distribution). The last
        break is the upper cap; prices >= it go into a single ">" overflow bucket.
        Raises ValueError if fewer than 2 breaks.

        `filters` applies the raw browse vocabulary (set_name/era/condition/…) to
        raw prices; `graded_filters` applies the graded vocabulary (crack/…) to
        graded prices. Set/condition filters simply don't exist for graded, so the
        two never cross.
        """
        breaks = sorted(set(breaks))
        if len(breaks) < 2:
            raise ValueError("Need at least 2 breakpoints")

        prices: list[float] = []
        if kind in ("raw", "all"):
            conditions, params = _build_filters(filters or {})
            conditions.insert(0, "p.estimated_price IS NOT NULL")
            prices += [
                r["estimated_price"]
                for r in self.db.execute(
                    "SELECT p.estimated_price" + _SHOW_FROM
                    + " WHERE " + " AND ".join(conditions),
                    params,
                ).fetchall()
            ]
        if kind in ("graded", "all"):
            gconds, gparams = _build_graded_filters(graded_filters or {})
            gconds.insert(0, "p.estimated_price IS NOT NULL")
            prices += [
                r["estimated_price"]
                for r in self.db.execute(
                    "SELECT p.estimated_price" + GRADED_INV_FROM
                    + " WHERE " + " AND ".join(gconds),
                    gparams,
                ).fetchall()
            ]

        return _bin_prices(prices, breaks)

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
            + _SHOW_FROM
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
            else f"{cfg['col']} as {cfg['name']}"
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
            + _SHOW_FROM
            + f"GROUP BY {cfg['col']} ORDER BY {cfg['order']}"
        ).fetchall()
        return [dict(r) for r in rows]

    def top_cards(self, n: int = 25) -> list[dict[str, Any]]:
        """Top N most valuable owned cards."""
        n = min(int(n), 100)
        rows = self.db.execute(
            f"""
            SELECT
                c.card_name, c.set_name, c.era, c.rarity,
                {_SHOW_CONDITION} as condition, s.finish,
                p.estimated_price, p.estimated_liquid_value,
                p.confidence_percent,
                i.inventory_id, i.qty
            """
            + _SHOW_FROM
            + "WHERE p.estimated_price IS NOT NULL ORDER BY p.estimated_price DESC LIMIT ?",
            [n],
        ).fetchall()
        return [dict(r) for r in rows]


    # ─── Graded reads (parallel to the raw browse/detail above) ──

    def _raw_top_price(self, card_id: str, finish: str, specialty_one: str = "None") -> Optional[float]:
        """Best (highest) raw price estimate for a card variant — the raw anchor a
        graded slab is compared against. Reads raw skus/prices; no raw code changes."""
        row = self.db.execute(
            """
            SELECT MAX(p.estimated_price) AS raw_price
            FROM skus s
            JOIN prices p ON s.sku_id = p.sku_id AND p.calculation_date = s.latest_calc_date
            WHERE s.card_id = ? AND s.finish = ? AND s.specialty_one = ?
              AND s.specialty_two = 'None'
            """,
            [card_id, finish, specialty_one],
        ).fetchone()
        return row["raw_price"] if row else None

    def browse_graded(self, filters: dict[str, Any]) -> dict[str, Any]:
        """Paginated list of owned graded slabs (the graded collection view).

        Reuses the shared pagination helpers (_paginate/_page_result) and the
        graded filter/sort vocabulary (_build_graded_filters/_resolve_graded_sort).
        Filters: q (name search), card_id, grading_company, status, grade,
        grades (multi), tags (multi). Sort: any _GRADED_SORT_COLUMNS key.
        """
        conditions, params = _build_graded_filters(filters)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sort_col, order_dir = _resolve_graded_sort(filters.get("sort"), filters.get("order"))
        page, per_page, offset = _paginate(filters.get("page"), filters.get("per_page"), 48)

        total = self.db.execute(
            f"SELECT COUNT(*) as total {GRADED_INV_FROM} {where}", params
        ).fetchone()["total"]

        rows = self.db.execute(
            f"""
            SELECT i.graded_inventory_id, i.graded_sku_id, i.cert_id, i.qty, i.tags, i.status,
                   i.front_photo_path, i.back_photo_path, i.ebay_listing_id,
                   g.card_id, g.finish, g.grading_company, g.grade, g.grade_label,
                   g.grader_spec_id, g.card_year, g.card_variety, g.card_language,
                   g.population, g.population_higher,
                   COALESCE(g.card_subject, c.card_name) AS card_name,
                   COALESCE(g.card_set, c.set_name) AS set_name,
                   COALESCE(g.card_number, c.card_number) AS card_number,
                   c.rarity, c.era,
                   p.estimated_price, p.confidence_percent, p.calculation_date
            {GRADED_INV_FROM} {where}
            ORDER BY {sort_col} {order_dir}, i.created_at ASC LIMIT ? OFFSET ?
            """,
            params + [per_page, offset],
        ).fetchall()
        return _page_result([dict(r) for r in rows], total, page, per_page)

    def graded_card_detail(
        self,
        card_id: str,
        finish: str = "Regular",
        specialty_one: str = "None",
        grading_company: str = "",
        grade: Optional[float] = None,
        image_card_ids: Optional[list[str]] = None,
    ) -> Optional[dict[str, Any]]:
        """One graded variant's detail: the slab rollup for a company+grade plus
        `raw_estimated_price` (the top raw price of the same card) for comparison."""
        card = self.cards.get_by_id(card_id)
        if not card or not grading_company or grade is None:
            return None

        row = self.db.execute(
            """
            SELECT SUM(i.qty) AS qty,
                   MAX(p.estimated_price) AS estimated_price,
                   MAX(p.confidence_percent) AS confidence_percent
            """
            + GRADED_INV_FROM
            + """
            WHERE g.card_id = ? AND g.finish = ? AND g.specialty_one = ?
              AND g.grading_company = ? AND g.grade = ?
            """,
            [card_id, finish, specialty_one, grading_company, float(grade)],
        ).fetchone()
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
                "grading_company": grading_company,
                "grade": float(grade),
            },
            "kind": "graded",
            "has_image": str(card_id) in image_set,
            "qty": (row["qty"] or 0) if row else 0,
            "estimated_price": row["estimated_price"] if row else None,
            "confidence_percent": row["confidence_percent"] if row else None,
            "raw_estimated_price": self._raw_top_price(card_id, finish, specialty_one),
        }

    def graded_slab_detail(
        self, cert_id: str, image_card_ids: Optional[list[str]] = None
    ) -> Optional[dict[str, Any]]:
        """The graded card detail page, for one physical slab (a cert number).

        The page's subject is the one cert; alongside it we return the spec-level
        context so the two tiers sit together:
          - **slab** (this `graded_inventory` row): the cert-specific data the page
            focuses on — status + tags (incl. the 'to_crack' mark) + its grade.
          - **grades** (the `graded_skus` grade class): every grade of this card,
            grouped by (grading_company, grader_spec_id) — owned qty, population,
            price per grade — shown as read-only context (cross-company grouping
            waits on the deferred graded->raw mapper).

        Returns None if the cert isn't owned. Falls back to a single-sku group when
        the sku has no grader_spec_id (a manual/legacy add)."""
        slab = self.graded_inventory.get_by_cert(cert_id)
        if not slab:
            return None
        sku = self.graded_skus.get_by_id(slab["graded_sku_id"])
        if not sku:
            return None

        company = sku["grading_company"]
        spec = sku["grader_spec_id"]
        # Group all grades of this card within the company by grader spec; when the
        # spec is missing, the page is just this one sku.
        if spec:
            group_where = "s.grading_company = ? AND s.grader_spec_id = ?"
            group_params: list[Any] = [company, spec]
        else:
            group_where = "s.graded_sku_id = ?"
            group_params = [sku["graded_sku_id"]]

        grade_rows = self.db.execute(
            f"""
            SELECT s.graded_sku_id, s.grade, s.grade_label,
                   s.population, s.population_higher,
                   COALESCE(SUM(i.qty), 0) AS qty,
                   MAX(p.estimated_price) AS estimated_price,
                   MAX(p.estimated_low_price) AS estimated_low_price,
                   MAX(p.estimated_high_price) AS estimated_high_price,
                   MAX(p.estimated_liquid_value) AS estimated_liquid_value,
                   MAX(p.confidence_percent) AS confidence_percent
            FROM graded_skus s
            LEFT JOIN graded_inventory i ON i.graded_sku_id = s.graded_sku_id
            LEFT JOIN graded_prices p
              ON p.graded_sku_id = s.graded_sku_id AND p.calculation_date = s.latest_calc_date
            WHERE {group_where}
            GROUP BY s.graded_sku_id
            ORDER BY s.grade DESC
            """,
            group_params,
        ).fetchall()
        grades = [dict(r) for r in grade_rows]

        # The focused slab: cert-specific data straight from the entered cert's row
        # (no extra query — get_by_cert already loaded it above).
        tag_list = parse_tags(slab["tags"])
        focused_slab = {
            "graded_inventory_id": slab["graded_inventory_id"],
            "cert_id": slab["cert_id"],
            "grade": sku["grade"],
            "grade_label": sku["grade_label"],
            "grading_company": company,
            "status": slab["status"],
            "tags": tag_list,
            "to_crack": GRADED_TO_CRACK_TAG in tag_list,
        }

        card_id = sku["card_id"]
        card = self.cards.get_by_id(card_id) if card_id else None
        image_set = set(_digit_ids(image_card_ids))
        return {
            "identity": {
                # Grader-derived (always present), raw `cards` only as fallback.
                "card_name": sku["card_subject"] or (card["card_name"] if card else None),
                "set_name": sku["card_set"] or (card["set_name"] if card else None),
                "card_number": sku["card_number"] or (card["card_number"] if card else None),
                "card_year": sku["card_year"],
                "card_variety": sku["card_variety"],
                "card_language": sku["card_language"],
                "finish": sku["finish"],
                "specialty_one": sku["specialty_one"],
                "grading_company": company,
                "grader_spec_id": spec,
                "rarity": card["rarity"] if card else None,
                "era": card["era"] if card else None,
                # Optional TCGplayer link (deferred mapper populates it broadly).
                "card_id": card_id,
                "raw_has_image": bool(card_id) and str(card_id) in image_set,
                "raw_estimated_price": (
                    self._raw_top_price(card_id, sku["finish"], sku["specialty_one"])
                    if card_id
                    else None
                ),
            },
            "slab": focused_slab,
            "grades": grades,
        }

    def graded_filter_options(self) -> dict[str, list[str]]:
        """Distinct values for the graded view's filter dropdowns."""
        statuses = self.db.execute(
            "SELECT DISTINCT status FROM graded_inventory WHERE status IS NOT NULL ORDER BY status"
        ).fetchall()
        grade_rows = self.db.execute(
            "SELECT DISTINCT grade FROM graded_skus WHERE grade IS NOT NULL ORDER BY grade DESC"
        ).fetchall()
        # Distinct individual tags across owned slabs (the comma-separated column).
        tag_rows = self.db.execute(
            "SELECT tags FROM graded_inventory WHERE tags IS NOT NULL AND TRIM(tags) != ''"
        ).fetchall()
        tagset: set[str] = set()
        for r in tag_rows:
            tagset.update(parse_tags(r["tags"]))
        # Offer the "No tags" pseudo-option (first) only when some slab is tagged —
        # otherwise every slab is untagged and the filter is moot.
        tags = sorted(tagset)
        if tags:
            tags = [GRADED_NO_TAG_OPTION] + tags
        return {
            "grading_companies": self.graded_skus.get_all_companies(),
            "statuses": [r["status"] for r in statuses],
            "grades": [_format_grade(r["grade"]) for r in grade_rows],
            "tags": tags,
        }

    # ─── Graded analytics (parallel to the raw analytics above) ──
    # All take a graded filters dict (the same vocabulary as the graded browse —
    # so the crack filter, and future ones, flow through unchanged).

    def _graded_where(self, filters: Optional[dict[str, Any]], price_only: bool = False) -> tuple[str, list[Any]]:
        conds, params = _build_graded_filters(filters or {})
        if price_only:
            conds.insert(0, "p.estimated_price IS NOT NULL")
        where = ("WHERE " + " AND ".join(conds)) if conds else ""
        return where, params

    def graded_analytics_summary(self, filters: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """Headline graded stats (slab count, value, avg grade/price, to-crack)."""
        where, params = self._graded_where(filters)
        row = self.db.execute(
            f"""
            SELECT COALESCE(SUM(i.qty), 0) AS total_slabs,
                   SUM(p.estimated_price * i.qty) AS total_value,
                   SUM(p.estimated_liquid_value * i.qty) AS total_liquid_value,
                   AVG(p.estimated_price) AS avg_price,
                   AVG(CASE WHEN g.grade > 0 THEN g.grade END) AS avg_grade,
                   SUM(CASE WHEN i.tags LIKE '%to_crack%' THEN i.qty ELSE 0 END) AS to_crack_count
            {GRADED_INV_FROM} {where}
            """,
            params,
        ).fetchone()
        return dict(row)

    def graded_breakdown(self, dimension: str, filters: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
        """Owned-slab rollup by a graded dimension ("grade" or "company"): quantity,
        total value, avg price per group. `label` is the display key (grade -> ERR-
        aware string; company -> name)."""
        col = _GRADED_BREAKDOWN_DIMS.get(dimension)
        if not col:
            raise ValueError(f"Unknown graded breakdown dimension: {dimension}")
        where, params = self._graded_where(filters)
        order = "g.grade DESC" if dimension == "grade" else "total_value DESC"
        rows = self.db.execute(
            f"""
            SELECT {col} AS grp,
                   SUM(i.qty) AS quantity,
                   SUM(p.estimated_price * i.qty) AS total_value,
                   AVG(p.estimated_price) AS avg_price
            {GRADED_INV_FROM} {where}
            GROUP BY {col} ORDER BY {order}
            """,
            params,
        ).fetchall()
        return [
            {
                "label": _format_grade(r["grp"]) if dimension == "grade" else r["grp"],
                "quantity": r["quantity"] or 0,
                "total_value": r["total_value"],
                "avg_price": r["avg_price"],
            }
            for r in rows
        ]

    def graded_price_points(self, filters: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
        """One point per priced slab for the grade x price scatter (population is
        carried for the future bubble-size axis; None for now)."""
        where, params = self._graded_where(filters, price_only=True)
        rows = self.db.execute(
            f"""
            SELECT i.cert_id, g.grade, g.grading_company, g.population,
                   p.estimated_price AS price,
                   COALESCE(g.card_subject, c.card_name) AS card_name
            {GRADED_INV_FROM} {where}
            ORDER BY g.grade DESC
            """,
            params,
        ).fetchall()
        return [dict(r) for r in rows]

    def top_graded_slabs(self, n: int = 25, filters: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
        """Most valuable owned slabs (priced), highest first."""
        n = max(1, min(int(n), 200))
        where, params = self._graded_where(filters, price_only=True)
        rows = self.db.execute(
            f"""
            SELECT i.graded_inventory_id, i.cert_id, g.grade, g.grade_label, g.grading_company,
                   g.card_year, p.estimated_price, p.estimated_liquid_value,
                   COALESCE(g.card_subject, c.card_name) AS card_name,
                   COALESCE(g.card_set, c.set_name) AS set_name
            {GRADED_INV_FROM} {where}
            ORDER BY p.estimated_price DESC LIMIT ?
            """,
            params + [n],
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
