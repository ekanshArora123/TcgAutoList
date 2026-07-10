"""Flask REST API for the dashboard.

Thin HTTP layer: parse query params, delegate to card-server's ReportingService,
serialize the result. It holds no SQL and opens no DB connection of its own —
card-server owns all database access (see services/card_server). The only thing
that lives here is HTTP concerns and the on-disk card-image lookup, which is a
filesystem concern the dashboard owns.
"""

import asyncio
import os
import sys
from pathlib import Path

# Make the repo root importable so `services.*` resolves even when this file is
# launched as a plain script (python dashboard/backend/app.py, as run.bat/run.sh
# do) rather than as a module. The repo root is two levels up from this file.
# Harmless when already run from the repo root via `python -m`.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

from services.card_server.db import init_database
from services.card_server.helpers.tcgplayer.transport import RateLimited
from services.card_server.services.collection_service import CollectionService
from services.card_server.services.reporting_service import ReportingService

app = Flask(__name__)
CORS(app)

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_IMAGES_DIR = os.path.normpath(os.path.join(_THIS_DIR, "..", "..", "data", "card-images"))

# One shared connection (check_same_thread=False) owned by card-server.
_db = init_database(os.environ.get("DB_PATH"))
reporting = ReportingService(_db)
# Write path (the dashboard's first): adding graded cards by cert number.
collection = CollectionService(_db)


def _image_card_ids() -> set[str]:
    """Card IDs (TCGplayer product IDs) that have an image on disk."""
    try:
        return {f.rsplit(".", 1)[0] for f in os.listdir(_IMAGES_DIR) if f.endswith(".webp")}
    except OSError:
        return set()


# ── Collection browser ─────────────────────────────────────


@app.route("/api/cards")
def list_cards():
    """Paginated card list with search, filter, sort."""
    return jsonify(reporting.browse_cards(request.args.to_dict()))


@app.route("/api/filters")
def get_filters():
    """Distinct values for filter dropdowns."""
    return jsonify(reporting.filter_options())


# ── Card images ────────────────────────────────────────────


@app.route("/api/images/<card_id>")
def card_image(card_id):
    """Serve a card image by TCGplayer product ID.

    Fetch-on-demand: if we don't have the image yet, pull it from TCGplayer once
    (fail-fast — retries=1, no rate-limit sleep — so the request never hangs) and
    then serve it. This is what makes a card with a TCGplayer id but no image on
    disk (e.g. a graded slab linked to a card_id) show a picture with no reload —
    the browser's <img> request materializes it. Bulk backfills still use the
    `scripts/fetch_images.py` CLI (which paces itself)."""
    filename = f"{card_id}.webp"
    if not os.path.exists(os.path.join(_IMAGES_DIR, filename)):
        if card_id.isdigit():  # TCGplayer product ids are numeric
            try:
                # Lazy import: a missing image dep (Pillow/httpx) degrades to a
                # placeholder rather than breaking dashboard startup.
                from services.card_server.scripts.fetch_images import fetch_card_image

                fetch_card_image(card_id, _IMAGES_DIR, retries=1, rate_limit_pause=0)
            except Exception:
                pass
        if not os.path.exists(os.path.join(_IMAGES_DIR, filename)):
            return "", 404
    return send_from_directory(_IMAGES_DIR, filename)


@app.route("/api/card/<card_id>")
def card_detail(card_id):
    """One card's detail page: metadata + per-condition qty rollup.

    Identity is the SKU minus condition; the variant axes come in as query
    params (default to the plain variant). 404 if the card_id is unknown.
    """
    detail = reporting.card_detail(
        card_id,
        finish=request.args.get("finish", "Regular"),
        specialty_one=request.args.get("specialty_one", "None"),
        specialty_two=request.args.get("specialty_two", "None"),
        image_card_ids=list(_image_card_ids()),
    )
    if detail is None:
        return "", 404
    return jsonify(detail)


@app.route("/api/card/<card_id>/sales")
def card_sales(card_id):
    """Daily sales rollup (one series per condition) for the per-card graph."""
    try:
        days = int(request.args.get("days", 365))
    except ValueError:
        days = 365
    return jsonify(
        reporting.card_sales_history(
            card_id,
            finish=request.args.get("finish", "Regular"),
            days=days,
            include_images=request.args.get("include_images") == "true",
        )
    )


@app.route("/api/card/<card_id>/sales-points")
def card_sales_points(card_id):
    """Individual sales as graph points (one per unit) for the scatter view."""
    try:
        days = int(request.args.get("days", 365))
    except ValueError:
        days = 365
    return jsonify(
        reporting.card_sales_points(
            card_id,
            finish=request.args.get("finish", "Regular"),
            days=days,
            include_images=request.args.get("include_images") == "true",
        )
    )


@app.route("/api/card/<card_id>/price-history")
def card_price_history(card_id):
    """TCGplayer market-price history (weekly), one series per condition."""
    try:
        days = int(request.args.get("days", 365))
    except ValueError:
        days = 365
    return jsonify(
        reporting.card_price_history(
            card_id,
            finish=request.args.get("finish", "Regular"),
            days=days,
        )
    )


@app.route("/api/collection")
def collection_grid():
    """Card collection with images — all filters plus price/confidence ranges."""
    has_image = request.args.get("has_image", "").strip()
    return jsonify(
        reporting.browse_collection(
            request.args.to_dict(),
            image_card_ids=list(_image_card_ids()),
            has_image=has_image,
        )
    )


# ── Graded cards (parallel read endpoints) ──────────────────


@app.route("/api/graded")
def graded_grid():
    """Owned graded slabs — the graded collection view."""
    return jsonify(reporting.browse_graded(request.args.to_dict()))


@app.route("/api/graded", methods=["POST"])
def add_graded():
    """Add a graded slab by cert number (fetches identity + pop from the grader).

    Body: { cert_id, grading_company?="PSA", card_id? }. `card_id` is the optional,
    manually-entered TCGplayer id. The dashboard's first write endpoint.
    """
    body = request.get_json(silent=True) or {}
    cert_id = str(body.get("cert_id") or "").strip()
    if not cert_id:
        return jsonify({"error": "cert_id is required"}), 400
    company = str(body.get("grading_company") or "PSA").strip()
    card_id = str(body.get("card_id") or "").strip() or None

    try:
        result = asyncio.run(collection.add_graded_by_cert(cert_id, company, card_id))
    except ValueError as e:  # unsupported company / bad input
        return jsonify({"error": str(e)}), 400
    except RateLimited:  # grader API daily quota exhausted
        return jsonify({"error": f"{company} API rate limit reached — try again later."}), 429
    except Exception as e:  # token missing / network / parse error
        return jsonify({"error": f"grader lookup failed: {e}"}), 502

    if result is None:
        return jsonify({"error": f"cert {cert_id} not found at {company}"}), 404
    return jsonify(result), 201


@app.route("/api/graded/companies")
def graded_companies():
    """Filter dropdown values for the graded view (companies + statuses)."""
    return jsonify(reporting.graded_filter_options())


@app.route("/api/graded/<card_id>")
def graded_card_detail(card_id):
    """One graded variant's detail (company+grade) + raw-vs-graded comparison."""
    grade_arg = request.args.get("grade")
    try:
        grade = float(grade_arg) if grade_arg not in (None, "") else None
    except ValueError:
        grade = None
    detail = reporting.graded_card_detail(
        card_id,
        finish=request.args.get("finish", "Regular"),
        specialty_one=request.args.get("specialty_one", "None"),
        grading_company=request.args.get("grading_company", ""),
        grade=grade,
        image_card_ids=list(_image_card_ids()),
    )
    if detail is None:
        return "", 404
    return jsonify(detail)


# ── Analytics ───────────────────────────────────────────────


@app.route("/api/analytics/summary")
def analytics_summary():
    return jsonify(reporting.analytics_summary())


@app.route("/api/analytics/price-histogram")
def price_histogram():
    """Price distribution histogram with custom breakpoints.

    Query params:
      breaks: comma-separated breakpoints, e.g. "0,0.2,0.5,1,5,10,30,60,100".
              The last value is the upper cap; anything above goes into a ">" bucket.
      set_name / era / condition / ...: optional collection filters (same
              vocabulary as the browse endpoints) to scope the distribution.
      eras / sets / conditions: repeatable multi-select variants (IN filter),
              e.g. "?eras=Vintage&eras=Modern".
    """
    breaks_str = request.args.get("breaks", "0,1,2,5,10,20,30,50,100")
    filters = {k: v for k, v in request.args.to_dict().items() if k != "breaks"}
    # Repeatable params collapse to their first value in to_dict(); pull the
    # full list for the multi-select keys.
    for plural in ("eras", "sets", "conditions"):
        values = request.args.getlist(plural)
        if values:
            filters[plural] = values
    try:
        breaks = [float(b) for b in breaks_str.split(",") if b.strip()]
        return jsonify(reporting.price_histogram(breaks, filters))
    except ValueError as e:
        return jsonify({"error": str(e) or "Invalid breaks parameter"}), 400


@app.route("/api/analytics/confidence-distribution")
def confidence_distribution():
    return jsonify(reporting.confidence_distribution())


@app.route("/api/analytics/era-breakdown")
def era_breakdown():
    return jsonify(reporting.breakdown("era"))


@app.route("/api/analytics/condition-breakdown")
def condition_breakdown():
    return jsonify(reporting.breakdown("condition"))


@app.route("/api/analytics/rarity-breakdown")
def rarity_breakdown():
    return jsonify(reporting.breakdown("rarity"))


@app.route("/api/analytics/set-breakdown")
def set_breakdown():
    return jsonify(reporting.breakdown("set"))


@app.route("/api/analytics/top-cards")
def top_cards():
    return jsonify(reporting.top_cards(request.args.get("n", 25)))


if __name__ == "__main__":
    app.run(debug=True, port=5000, use_reloader=False)
