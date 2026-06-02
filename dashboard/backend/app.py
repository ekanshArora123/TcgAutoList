import os
import sqlite3
import math
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_DB = os.path.normpath(os.path.join(_THIS_DIR, "..", "..", "services", "card_server", "data", "cards.db"))
_IMAGES_DIR = os.path.normpath(os.path.join(_THIS_DIR, "..", "..", "data", "card-images"))
DB_PATH = os.environ.get("DB_PATH", _DEFAULT_DB)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


# ── Collection browser ─────────────────────────────────────


@app.route("/api/cards")
def list_cards():
    """Paginated card list with search, filter, sort. Returns joined card+sku+price data."""
    q = request.args.get("q", "").strip()
    set_name = request.args.get("set_name", "").strip()
    era = request.args.get("era", "").strip()
    rarity = request.args.get("rarity", "").strip()
    condition = request.args.get("condition", "").strip()
    finish = request.args.get("finish", "").strip()
    status = request.args.get("status", "").strip()
    manual_check = request.args.get("manual_check", "").strip()
    sort = request.args.get("sort", "card_name").strip()
    order = request.args.get("order", "asc").strip().upper()
    page = max(int(request.args.get("page", 1)), 1)
    per_page = min(int(request.args.get("per_page", 50)), 200)

    allowed_sorts = {
        "card_name": "c.card_name",
        "set_name": "c.set_name",
        "era": "c.era",
        "rarity": "c.rarity",
        "condition": "s.condition",
        "estimated_price": "p.estimated_price",
        "confidence": "p.confidence_percent",
        "status": "i.status",
    }
    sort_col = allowed_sorts.get(sort, "c.card_name")
    order = order if order in ("ASC", "DESC") else "ASC"

    conditions = []
    params = []

    if q:
        conditions.append("c.card_name LIKE ?")
        params.append(f"%{q}%")
    if set_name:
        conditions.append("c.set_name = ?")
        params.append(set_name)
    if era:
        conditions.append("c.era = ?")
        params.append(era)
    if rarity:
        conditions.append("c.rarity = ?")
        params.append(rarity)
    if condition:
        conditions.append("s.condition = ?")
        params.append(condition)
    if finish:
        conditions.append("s.finish = ?")
        params.append(finish)
    if status:
        conditions.append("i.status = ?")
        params.append(status)
    if manual_check == "true":
        conditions.append("p.manual_check_necessary = 1 AND p.manually_checked = 0")

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    db = get_db()
    try:
        count_sql = f"""
            SELECT COUNT(*) as total
            FROM inventory i
            JOIN skus s ON i.sku_id = s.sku_id
            JOIN cards c ON s.card_id = c.id
            LEFT JOIN prices p ON COALESCE(i.pricing_sku_id, i.sku_id) = p.sku_id
                AND p.calculation_date = s.latest_calc_date
            {where}
        """
        total = db.execute(count_sql, params).fetchone()["total"]

        data_sql = f"""
            SELECT
                i.inventory_id, i.sku_id, i.qty, i.tags, i.status,
                i.front_photo_path, i.back_photo_path, i.ebay_listing_id,
                s.condition, s.finish, s.card_id, s.specialty_one, s.specialty_two,
                c.card_name, c.set_name, c.rarity, c.card_number, c.era,
                c.card_type, c.visual_layout,
                p.estimated_price, p.estimated_liquid_value,
                p.confidence_percent, p.manual_check_necessary,
                p.estimated_low_price, p.estimated_high_price,
                p.calculation_date, p.algorithm_version
            FROM inventory i
            JOIN skus s ON i.sku_id = s.sku_id
            JOIN cards c ON s.card_id = c.id
            LEFT JOIN prices p ON COALESCE(i.pricing_sku_id, i.sku_id) = p.sku_id
                AND p.calculation_date = s.latest_calc_date
            {where}
            ORDER BY {sort_col} {order}
            LIMIT ? OFFSET ?
        """
        rows = db.execute(data_sql, params + [per_page, (page - 1) * per_page]).fetchall()
        return jsonify(
            {
                "items": [dict(r) for r in rows],
                "total": total,
                "page": page,
                "per_page": per_page,
                "total_pages": math.ceil(total / per_page) if total else 0,
            }
        )
    finally:
        db.close()


@app.route("/api/filters")
def get_filters():
    """Return distinct values for filter dropdowns."""
    db = get_db()
    try:
        sets = [r["set_name"] for r in db.execute("SELECT DISTINCT set_name FROM cards WHERE set_name IS NOT NULL ORDER BY set_name").fetchall()]
        eras = [r["era"] for r in db.execute("SELECT DISTINCT era FROM cards WHERE era IS NOT NULL ORDER BY era").fetchall()]
        rarities = [r["rarity"] for r in db.execute("SELECT DISTINCT rarity FROM cards WHERE rarity IS NOT NULL ORDER BY rarity").fetchall()]
        conditions = [r["condition"] for r in db.execute("SELECT DISTINCT condition FROM skus ORDER BY condition").fetchall()]
        finishes = [r["finish"] for r in db.execute("SELECT DISTINCT finish FROM skus ORDER BY finish").fetchall()]
        statuses = [r["status"] for r in db.execute("SELECT DISTINCT status FROM inventory ORDER BY status").fetchall()]
        return jsonify({"sets": sets, "eras": eras, "rarities": rarities, "conditions": conditions, "finishes": finishes, "statuses": statuses})
    finally:
        db.close()


# ── Card images ────────────────────────────────────────────


@app.route("/api/images/<card_id>")
def card_image(card_id):
    """Serve card image by TCGplayer product ID."""
    filename = f"{card_id}.webp"
    filepath = os.path.join(_IMAGES_DIR, filename)
    if not os.path.exists(filepath):
        return "", 404
    return send_from_directory(_IMAGES_DIR, filename)


@app.route("/api/collection")
def collection_grid():
    """Card collection with images — supports all filters including price range."""
    q = request.args.get("q", "").strip()
    set_name = request.args.get("set_name", "").strip()
    era = request.args.get("era", "").strip()
    rarity = request.args.get("rarity", "").strip()
    condition = request.args.get("condition", "").strip()
    finish = request.args.get("finish", "").strip()
    status = request.args.get("status", "").strip()
    manual_check = request.args.get("manual_check", "").strip()
    specialty = request.args.get("specialty", "").strip()
    tags_contain = request.args.get("tags_contain", "").strip()
    price_min = request.args.get("price_min", "").strip()
    price_max = request.args.get("price_max", "").strip()
    confidence_min = request.args.get("confidence_min", "").strip()
    confidence_max = request.args.get("confidence_max", "").strip()
    has_image = request.args.get("has_image", "").strip()
    sort = request.args.get("sort", "card_name").strip()
    order = request.args.get("order", "asc").strip().upper()
    page = max(int(request.args.get("page", 1)), 1)
    per_page = min(int(request.args.get("per_page", 48)), 200)

    allowed_sorts = {
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
    sort_col = allowed_sorts.get(sort, "c.card_name")
    order = order if order in ("ASC", "DESC") else "ASC"

    conditions_sql = []
    params = []

    if q:
        conditions_sql.append("c.card_name LIKE ?")
        params.append(f"%{q}%")
    if set_name:
        conditions_sql.append("c.set_name = ?")
        params.append(set_name)
    if era:
        conditions_sql.append("c.era = ?")
        params.append(era)
    if rarity:
        conditions_sql.append("c.rarity = ?")
        params.append(rarity)
    if condition:
        conditions_sql.append("s.condition = ?")
        params.append(condition)
    if finish:
        conditions_sql.append("s.finish = ?")
        params.append(finish)
    if status:
        conditions_sql.append("i.status = ?")
        params.append(status)
    if manual_check == "true":
        conditions_sql.append("p.manual_check_necessary = 1 AND p.manually_checked = 0")
    if specialty:
        conditions_sql.append("s.specialty_one = ?")
        params.append(specialty)
    if tags_contain:
        conditions_sql.append("i.tags LIKE ?")
        params.append(f"%{tags_contain}%")
    if price_min:
        conditions_sql.append("p.estimated_price >= ?")
        params.append(float(price_min))
    if price_max:
        conditions_sql.append("p.estimated_price <= ?")
        params.append(float(price_max))
    if confidence_min:
        conditions_sql.append("p.confidence_percent >= ?")
        params.append(int(confidence_min))
    if confidence_max:
        conditions_sql.append("p.confidence_percent <= ?")
        params.append(int(confidence_max))

    where = f"WHERE {' AND '.join(conditions_sql)}" if conditions_sql else ""

    # Build list of card_ids that have images on disk
    image_ids = set()
    if has_image or True:  # always compute for the has_image field
        try:
            image_ids = {f.rsplit(".", 1)[0] for f in os.listdir(_IMAGES_DIR) if f.endswith(".webp")}
        except OSError:
            pass

    if has_image == "true":
        id_list = ",".join(f"'{cid}'" for cid in image_ids)
        conditions_sql.append(f"s.card_id IN ({id_list})" if id_list else "1=0")
        where = f"WHERE {' AND '.join(conditions_sql)}" if conditions_sql else ""
    elif has_image == "false":
        id_list = ",".join(f"'{cid}'" for cid in image_ids)
        conditions_sql.append(f"s.card_id NOT IN ({id_list})" if id_list else "1=1")
        where = f"WHERE {' AND '.join(conditions_sql)}" if conditions_sql else ""

    db = get_db()
    try:
        from_clause = """
            inventory i
            JOIN skus s ON i.sku_id = s.sku_id
            JOIN cards c ON s.card_id = c.id
            LEFT JOIN prices p ON COALESCE(i.pricing_sku_id, i.sku_id) = p.sku_id
                AND p.calculation_date = s.latest_calc_date
            LEFT JOIN market_snapshots ms ON s.card_id = ms.card_id
                AND s.condition = ms.condition AND s.finish = ms.finish
                AND ms.snapshot_date = (SELECT MAX(snapshot_date) FROM market_snapshots ms2 WHERE ms2.card_id = ms.card_id AND ms2.condition = ms.condition AND ms2.finish = ms.finish)
        """

        count_sql = f"SELECT COUNT(*) as total FROM {from_clause} {where}"
        total = db.execute(count_sql, params).fetchone()["total"]

        data_sql = f"""
            SELECT
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
            FROM {from_clause}
            {where}
            ORDER BY {sort_col} {order}
            LIMIT ? OFFSET ?
        """
        rows = db.execute(data_sql, params + [per_page, (page - 1) * per_page]).fetchall()

        items = []
        for r in rows:
            item = dict(r)
            item["has_image"] = item["card_id"] in image_ids
            items.append(item)

        return jsonify({
            "items": items,
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": math.ceil(total / per_page) if total else 0,
        })
    finally:
        db.close()


# ── Analytics ───────────────────────────────────────────────


@app.route("/api/analytics/summary")
def analytics_summary():
    """High-level collection stats."""
    db = get_db()
    try:
        total_cards = db.execute("SELECT COUNT(*) as c FROM inventory").fetchone()["c"]
        unique_cards = db.execute("SELECT COUNT(*) as c FROM cards").fetchone()["c"]
        total_skus = db.execute("SELECT COUNT(*) as c FROM skus").fetchone()["c"]

        value_row = db.execute("""
            SELECT
                SUM(p.estimated_price * i.qty) as total_value,
                SUM(p.estimated_liquid_value * i.qty) as total_liquid_value,
                AVG(p.estimated_price) as avg_price,
                MIN(p.estimated_price) as min_price,
                MAX(p.estimated_price) as max_price
            FROM inventory i
            JOIN skus s ON i.sku_id = s.sku_id
            LEFT JOIN prices p ON COALESCE(i.pricing_sku_id, i.sku_id) = p.sku_id
                AND p.calculation_date = s.latest_calc_date
            WHERE p.estimated_price IS NOT NULL
        """).fetchone()

        status_rows = db.execute("SELECT status, COUNT(*) as count FROM inventory GROUP BY status").fetchall()
        by_status = {r["status"]: r["count"] for r in status_rows}

        manual_checks = db.execute("""
            SELECT COUNT(*) as c FROM inventory i
            JOIN skus s ON i.sku_id = s.sku_id
            JOIN prices p ON COALESCE(i.pricing_sku_id, i.sku_id) = p.sku_id
                AND p.calculation_date = s.latest_calc_date
            WHERE p.manual_check_necessary = 1 AND p.manually_checked = 0
        """).fetchone()["c"]

        avg_confidence = db.execute("""
            SELECT AVG(p.confidence_percent) as avg_conf FROM inventory i
            JOIN skus s ON i.sku_id = s.sku_id
            JOIN prices p ON COALESCE(i.pricing_sku_id, i.sku_id) = p.sku_id
                AND p.calculation_date = s.latest_calc_date
            WHERE p.confidence_percent IS NOT NULL
        """).fetchone()["avg_conf"]

        return jsonify({
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
        })
    finally:
        db.close()


@app.route("/api/analytics/price-histogram")
def price_histogram():
    """Price distribution histogram with custom breakpoints.

    Query params:
      breaks: comma-separated breakpoints, e.g. "0,0.2,0.5,1,5,10,30,60,100"
              The last value is the upper cap; anything above goes into a ">" bucket.
    """
    breaks_str = request.args.get("breaks", "0,1,2,5,10,20,30,50,100")

    try:
        breaks = sorted(set(float(b) for b in breaks_str.split(",") if b.strip()))
    except ValueError:
        return jsonify({"error": "Invalid breaks parameter"}), 400

    if len(breaks) < 2:
        return jsonify({"error": "Need at least 2 breakpoints"}), 400

    db = get_db()
    try:
        rows = db.execute("""
            SELECT p.estimated_price
            FROM inventory i
            JOIN skus s ON i.sku_id = s.sku_id
            JOIN prices p ON COALESCE(i.pricing_sku_id, i.sku_id) = p.sku_id
                AND p.calculation_date = s.latest_calc_date
            WHERE p.estimated_price IS NOT NULL
        """).fetchall()

        # Build bin counts and value sums per bucket, plus overflow
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

        def fmt(v):
            return f"${v:g}"

        result = []
        for i in range(len(breaks) - 1):
            result.append({
                "range": f"{fmt(breaks[i])}-{fmt(breaks[i + 1])}",
                "count": bin_counts[i],
                "total_value": round(bin_values[i], 2),
            })
        if over_max_count:
            result.append({"range": f">{fmt(max_break)}", "count": over_max_count, "total_value": round(over_max_value, 2)})

        return jsonify(result)
    finally:
        db.close()


@app.route("/api/analytics/confidence-distribution")
def confidence_distribution():
    """Confidence percent distribution in 10% buckets."""
    db = get_db()
    try:
        rows = db.execute("""
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
            FROM inventory i
            JOIN skus s ON i.sku_id = s.sku_id
            LEFT JOIN prices p ON COALESCE(i.pricing_sku_id, i.sku_id) = p.sku_id
                AND p.calculation_date = s.latest_calc_date
            GROUP BY bucket
            ORDER BY bucket
        """).fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        db.close()


@app.route("/api/analytics/era-breakdown")
def era_breakdown():
    """Era breakdown by both quantity and total value."""
    db = get_db()
    try:
        rows = db.execute("""
            SELECT
                COALESCE(c.era, 'Unknown') as era,
                COUNT(*) as quantity,
                SUM(i.qty) as total_qty,
                SUM(CASE WHEN p.estimated_price IS NOT NULL THEN p.estimated_price * i.qty ELSE 0 END) as total_value,
                AVG(p.estimated_price) as avg_price,
                AVG(p.confidence_percent) as avg_confidence
            FROM inventory i
            JOIN skus s ON i.sku_id = s.sku_id
            JOIN cards c ON s.card_id = c.id
            LEFT JOIN prices p ON COALESCE(i.pricing_sku_id, i.sku_id) = p.sku_id
                AND p.calculation_date = s.latest_calc_date
            GROUP BY c.era
            ORDER BY total_value DESC
        """).fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        db.close()


@app.route("/api/analytics/condition-breakdown")
def condition_breakdown():
    """Breakdown by card condition."""
    db = get_db()
    try:
        rows = db.execute("""
            SELECT
                s.condition,
                COUNT(*) as quantity,
                AVG(p.estimated_price) as avg_price,
                SUM(CASE WHEN p.estimated_price IS NOT NULL THEN p.estimated_price * i.qty ELSE 0 END) as total_value
            FROM inventory i
            JOIN skus s ON i.sku_id = s.sku_id
            LEFT JOIN prices p ON COALESCE(i.pricing_sku_id, i.sku_id) = p.sku_id
                AND p.calculation_date = s.latest_calc_date
            GROUP BY s.condition
            ORDER BY quantity DESC
        """).fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        db.close()


@app.route("/api/analytics/rarity-breakdown")
def rarity_breakdown():
    """Breakdown by rarity."""
    db = get_db()
    try:
        rows = db.execute("""
            SELECT
                COALESCE(c.rarity, 'Unknown') as rarity,
                COUNT(*) as quantity,
                AVG(p.estimated_price) as avg_price,
                SUM(CASE WHEN p.estimated_price IS NOT NULL THEN p.estimated_price * i.qty ELSE 0 END) as total_value
            FROM inventory i
            JOIN skus s ON i.sku_id = s.sku_id
            JOIN cards c ON s.card_id = c.id
            LEFT JOIN prices p ON COALESCE(i.pricing_sku_id, i.sku_id) = p.sku_id
                AND p.calculation_date = s.latest_calc_date
            GROUP BY c.rarity
            ORDER BY total_value DESC
        """).fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        db.close()


@app.route("/api/analytics/top-cards")
def top_cards():
    """Top N most valuable cards."""
    n = min(int(request.args.get("n", 25)), 100)
    db = get_db()
    try:
        rows = db.execute("""
            SELECT
                c.card_name, c.set_name, c.era, c.rarity,
                s.condition, s.finish,
                p.estimated_price, p.estimated_liquid_value,
                p.confidence_percent,
                i.inventory_id, i.qty
            FROM inventory i
            JOIN skus s ON i.sku_id = s.sku_id
            JOIN cards c ON s.card_id = c.id
            LEFT JOIN prices p ON COALESCE(i.pricing_sku_id, i.sku_id) = p.sku_id
                AND p.calculation_date = s.latest_calc_date
            WHERE p.estimated_price IS NOT NULL
            ORDER BY p.estimated_price DESC
            LIMIT ?
        """, [n]).fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        db.close()


@app.route("/api/analytics/set-breakdown")
def set_breakdown():
    """Breakdown by set name - quantity and value."""
    db = get_db()
    try:
        rows = db.execute("""
            SELECT
                COALESCE(c.set_name, 'Unknown') as set_name,
                COUNT(*) as quantity,
                SUM(CASE WHEN p.estimated_price IS NOT NULL THEN p.estimated_price * i.qty ELSE 0 END) as total_value,
                AVG(p.estimated_price) as avg_price
            FROM inventory i
            JOIN skus s ON i.sku_id = s.sku_id
            JOIN cards c ON s.card_id = c.id
            LEFT JOIN prices p ON COALESCE(i.pricing_sku_id, i.sku_id) = p.sku_id
                AND p.calculation_date = s.latest_calc_date
            GROUP BY c.set_name
            ORDER BY total_value DESC
        """).fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        db.close()


if __name__ == "__main__":
    app.run(debug=True, port=5000, use_reloader=False)
