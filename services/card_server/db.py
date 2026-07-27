"""SQLite init/close — mirrors db.ts.

Uses Python's stdlib sqlite3 (synchronous, same as better-sqlite3). The
connection runs in autocommit mode (isolation_level=None) so individual
statements commit immediately like better-sqlite3; bulk operations open
explicit transactions with BEGIN/COMMIT.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Optional

_THIS_DIR = Path(__file__).resolve().parent

_db: Optional[sqlite3.Connection] = None


def init_database(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Open (and migrate) the SQLite database. Idempotent schema creation."""
    global _db
    resolved = Path(db_path) if db_path else _THIS_DIR / "data" / "cards.db"
    resolved.parent.mkdir(parents=True, exist_ok=True)

    _db = sqlite3.connect(str(resolved), isolation_level=None, check_same_thread=False)
    _db.row_factory = sqlite3.Row
    _db.execute("PRAGMA journal_mode = WAL")
    _db.execute("PRAGMA foreign_keys = ON")

    schema = (_THIS_DIR / "schema.sql").read_text(encoding="utf-8")
    _db.executescript(schema)

    # Migrations: add columns to existing tables (safe to re-run).
    migrations = [
        "ALTER TABLE inventory ADD COLUMN front_photo_path TEXT",
        "ALTER TABLE inventory ADD COLUMN back_photo_path TEXT",
        "ALTER TABLE sales ADD COLUMN has_image INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE prices ADD COLUMN reasoning TEXT",
    ]
    for sql in migrations:
        try:
            _db.execute(sql)
        except sqlite3.OperationalError:
            pass  # column already exists

    _upgrade_graded_skus(_db)
    _dedup_graded_inventory_certs(_db)
    _upgrade_market_snapshots(_db)
    _upgrade_graph_specialty(_db)

    return _db


def _upgrade_graph_specialty(db: sqlite3.Connection) -> None:
    """Add specialty_one to the two graph tables (`sales`, `market_price_history`).

    Both key a variant as card+condition+finish, which pools 1st Edition with
    Unlimited — the same conflation `_upgrade_market_snapshots` fixed on the
    pricing side. The printing is right there in each fetched row (the sales API
    reports it in `variant`); it was simply dropped for want of a column.

    A plain ADD COLUMN suffices — no rebuild, since nothing about the old
    uniqueness needs relaxing. Existing rows land on 'None', which is wrong for
    any 1st Edition history already collected; that self-heals on the next
    `collect_sales` run for a card, because each refresh replaces the card's rows
    wholesale. The stale (card, condition, finish, source) index is dropped and
    rebuilt with the new column, once, at the same time.
    """
    for table, index in (("sales", "idx_sales_variant"), ("market_price_history", "idx_mph_variant")):
        cols = {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}
        if not cols or "specialty_one" in cols:
            continue
        db.execute(f"ALTER TABLE {table} ADD COLUMN specialty_one TEXT NOT NULL DEFAULT 'None'")
        db.execute(f"DROP INDEX IF EXISTS {index}")
        db.execute(
            f"CREATE INDEX IF NOT EXISTS {index} "
            f"ON {table}(card_id, condition, finish, specialty_one, source)"
        )


# Column list for the current market_snapshots shape (must match schema.sql).
_MARKET_SNAPSHOT_COLUMNS = (
    "snapshot_id", "card_id", "condition", "finish", "specialty_one",
    "snapshot_date", "source",
    "listing_count", "lowest_listing_price", "median_listing_price",
    "mean_listing_price", "p25_listing_price", "p75_listing_price",
    "recent_sales_count", "avg_sale_price", "median_sale_price",
    "min_sale_price", "max_sale_price", "newest_sale_date", "oldest_sale_date",
)


def _upgrade_market_snapshots(db: sqlite3.Connection) -> None:
    """Add specialty_one to market_snapshots and re-key uniqueness on it.

    The legacy table was UNIQUE(card_id, condition, finish, snapshot_date,
    source) with no specialty column, so a card owned in both 1st Edition and
    Unlimited collided on one row per day and the last write silently won — the
    two are separate products at very different prices. SQLite can't drop an
    inline UNIQUE via ALTER, so this is a table rebuild; existing rows carry
    specialty_one='None', which preserves their uniqueness (a constant added to
    an already-unique key stays unique) and matches what the old collector
    actually fetched.

    Guarded/idempotent: a no-op once migrated, and on a fresh DB the column is
    already there so only the index creation runs.
    """
    cols = {r["name"] for r in db.execute("PRAGMA table_info(market_snapshots)")}
    if cols and "specialty_one" not in cols:
        db.execute("PRAGMA foreign_keys = OFF")
        db.execute("BEGIN")
        try:
            # Single-statement execute (NOT executescript, which would commit the
            # open transaction out from under us).
            db.execute(
                """
                CREATE TABLE market_snapshots_new (
                    snapshot_id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    card_id                 TEXT NOT NULL REFERENCES cards(id),
                    condition               TEXT NOT NULL,
                    finish                  TEXT NOT NULL DEFAULT 'Regular',
                    specialty_one           TEXT NOT NULL DEFAULT 'None',
                    snapshot_date           TEXT NOT NULL,
                    source                  TEXT NOT NULL DEFAULT 'tcgplayer',
                    listing_count           INTEGER,
                    lowest_listing_price    REAL,
                    median_listing_price    REAL,
                    mean_listing_price      REAL,
                    p25_listing_price       REAL,
                    p75_listing_price       REAL,
                    recent_sales_count      INTEGER,
                    avg_sale_price          REAL,
                    median_sale_price       REAL,
                    min_sale_price          REAL,
                    max_sale_price          REAL,
                    newest_sale_date        TEXT,
                    oldest_sale_date        TEXT
                )
                """
            )
            carry = [c for c in _MARKET_SNAPSHOT_COLUMNS if c in cols]
            collist = ", ".join(carry)
            db.execute(
                f"INSERT INTO market_snapshots_new ({collist}) SELECT {collist} FROM market_snapshots"
            )
            db.execute("DROP TABLE market_snapshots")
            db.execute("ALTER TABLE market_snapshots_new RENAME TO market_snapshots")
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            db.execute("PRAGMA foreign_keys = ON")
            raise
        db.execute("PRAGMA foreign_keys = ON")
        # The rebuild drops the table's indexes along with it.
        db.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_card ON market_snapshots(card_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_date ON market_snapshots(snapshot_date)")
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_snapshots_card_date "
            "ON market_snapshots(card_id, snapshot_date)"
        )

    db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_snapshots_variant ON market_snapshots"
        "(card_id, condition, finish, specialty_one, snapshot_date, source)"
    )


def _dedup_graded_inventory_certs(db: sqlite3.Connection) -> None:
    """A cert number is a single physical slab, so a cert must appear at most once
    in graded_inventory. Drop any duplicate rows (keep the earliest), refresh the
    affected graded_skus' aggregate qty, then enforce it with a partial unique
    index. Guarded/idempotent; the index is created here (not in schema.sql) so
    executescript never trips over pre-existing duplicates. NULL certs (cert-less
    manual adds) are exempt — they stay non-unique."""
    db.execute(
        """
        DELETE FROM graded_inventory
        WHERE cert_id IS NOT NULL AND graded_inventory_id NOT IN (
            SELECT MIN(graded_inventory_id) FROM graded_inventory
            WHERE cert_id IS NOT NULL GROUP BY cert_id
        )
        """
    )
    db.execute(
        """
        UPDATE graded_skus SET qty = (
            SELECT COALESCE(SUM(qty), 0) FROM graded_inventory
            WHERE graded_inventory.graded_sku_id = graded_skus.graded_sku_id
        )
        """
    )
    db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_graded_inventory_cert "
        "ON graded_inventory(cert_id) WHERE cert_id IS NOT NULL"
    )


# Column list for the current graded_skus shape (must match schema.sql).
_GRADED_SKUS_COLUMNS = (
    "graded_sku_id", "grading_company", "grade", "grade_label", "grader_spec_id",
    "card_year", "card_set", "card_category", "card_number", "card_subject",
    "card_variety", "card_language", "population", "population_higher", "pop_fetched_at",
    "card_id", "finish", "specialty_one", "qty", "latest_calc_date", "created_at",
)


def _upgrade_graded_skus(db: sqlite3.Connection) -> None:
    """Bring an existing graded_skus table to the decoupled shape: nullable
    card_id + generic grader-identity columns. Guarded/idempotent — a no-op once
    migrated or on a fresh DB (schema.sql already builds the new shape). SQLite
    can't relax card_id's NOT NULL via ALTER, so this is a table rebuild (the
    table is tiny). Then create the cert-identity unique index."""
    cols = {r["name"] for r in db.execute("PRAGMA table_info(graded_skus)")}
    if cols and "grader_spec_id" not in cols:
        old = cols  # columns present on the legacy table, to copy across
        db.execute("PRAGMA foreign_keys = OFF")
        db.execute("BEGIN")
        try:
            # Single-statement execute (NOT executescript, which would commit the
            # open transaction out from under us).
            db.execute(
                """
                CREATE TABLE graded_skus_new (
                    graded_sku_id    INTEGER PRIMARY KEY AUTOINCREMENT,
                    grading_company  TEXT NOT NULL,
                    grade            REAL NOT NULL,
                    grade_label      TEXT,
                    grader_spec_id   TEXT,
                    card_year        TEXT,
                    card_set         TEXT,
                    card_category    TEXT,
                    card_number      TEXT,
                    card_subject     TEXT,
                    card_variety     TEXT,
                    card_language    TEXT,
                    population        INTEGER,
                    population_higher INTEGER,
                    pop_fetched_at   TEXT,
                    card_id          TEXT REFERENCES cards(id),
                    finish           TEXT NOT NULL DEFAULT 'Regular',
                    specialty_one    TEXT NOT NULL DEFAULT 'None',
                    qty              INTEGER DEFAULT 0,
                    latest_calc_date TEXT,
                    created_at       TEXT DEFAULT (datetime('now')),
                    UNIQUE(card_id, finish, specialty_one, grading_company, grade)
                )
                """
            )
            carry = [c for c in _GRADED_SKUS_COLUMNS if c in old]
            collist = ", ".join(carry)
            db.execute(
                f"INSERT INTO graded_skus_new ({collist}) SELECT {collist} FROM graded_skus"
            )
            db.execute("DROP TABLE graded_skus")
            db.execute("ALTER TABLE graded_skus_new RENAME TO graded_skus")
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            db.execute("PRAGMA foreign_keys = ON")
            raise
        db.execute("PRAGMA foreign_keys = ON")

    db.execute("CREATE INDEX IF NOT EXISTS idx_graded_skus_card_id ON graded_skus(card_id)")
    db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_graded_skus_spec "
        "ON graded_skus(grading_company, grader_spec_id, grade) WHERE grader_spec_id IS NOT NULL"
    )


def get_db() -> sqlite3.Connection:
    if _db is None:
        raise RuntimeError("Database not initialized. Call init_database() first.")
    return _db


def close_database() -> None:
    global _db
    if _db is not None:
        _db.close()
        _db = None
