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

    return _db


def get_db() -> sqlite3.Connection:
    if _db is None:
        raise RuntimeError("Database not initialized. Call init_database() first.")
    return _db


def close_database() -> None:
    global _db
    if _db is not None:
        _db.close()
        _db = None
