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
    ]
    for sql in migrations:
        try:
            _db.execute(sql)
        except sqlite3.OperationalError:
            pass  # column already exists

    _upgrade_graded_skus(_db)

    return _db


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
