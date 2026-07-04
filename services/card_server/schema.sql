-- card-server schema
-- Combined collection + card info + pricing data service
-- Migrated from MySQL dump, redesigned for SQLite

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-----------------------------------------------------
-- cards: canonical card metadata from TCGplayer
-----------------------------------------------------
CREATE TABLE IF NOT EXISTS cards (
    id            TEXT PRIMARY KEY,            -- tcgplayer product ID (was cardinfo.ID)
    card_name     TEXT NOT NULL,
    set_name      TEXT,
    product_line  TEXT DEFAULT 'Pokemon',      -- Pokemon, Magic, Yu-Gi-Oh, etc.
    card_type     TEXT,                        -- e.g. "Pokemon", "Trainer", "Energy"
    visual_layout TEXT,                        -- e.g. "Full Art", "Half Art", "Standard" (unreliable — verify via TCGplayer)
    rarity        TEXT,                        -- e.g. "Rare Holo", "Common", "Ultra Rare"
    card_number   TEXT,                        -- e.g. "25/102"
    product_type  TEXT,                        -- e.g. "Cards", "Sealed Products"
    era           TEXT,                        -- e.g. "WOTC", "EX Era", "Modern"
    set_type      TEXT,                        -- e.g. "Base", "Expansion", "Promo"
    created_at    TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_cards_name ON cards(card_name);
CREATE INDEX IF NOT EXISTS idx_cards_set ON cards(set_name);

-----------------------------------------------------
-- skus: a specific variant of a card (condition + finish + specialties)
-- Replaces the old hashedSku approach with auto-increment + composite UNIQUE
-----------------------------------------------------
CREATE TABLE IF NOT EXISTS skus (
    sku_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id       TEXT NOT NULL REFERENCES cards(id),
    condition     TEXT NOT NULL,               -- NM, LP, MP, HP, DMG (also supports in-between: LP-NM, HP-MP, etc.)
    finish        TEXT NOT NULL DEFAULT 'Regular',  -- Regular, Holo, Reverse-Holo
    specialty_one TEXT NOT NULL DEFAULT 'None',     -- Reproducible TCGplayer variant: "First Edition", "World Championship", "None"
    specialty_two TEXT NOT NULL DEFAULT 'None',     -- Reproducible non-TCGplayer variant: "PSA 10", "CGC 9", specific errors, "None"
    qty           INTEGER DEFAULT 0,                -- aggregate count of cards with this exact SKU
    latest_calc_date TEXT,                          -- date of most recent price calculation for this SKU
    created_at    TEXT DEFAULT (datetime('now')),

    UNIQUE(card_id, condition, finish, specialty_one, specialty_two)
);

CREATE INDEX IF NOT EXISTS idx_skus_card_id ON skus(card_id);

-----------------------------------------------------
-- inventory: individual physical cards you own
-- Each row = one distinct physical card (or a qty from one source)
-----------------------------------------------------
CREATE TABLE IF NOT EXISTS inventory (
    inventory_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    sku_id        INTEGER NOT NULL REFERENCES skus(sku_id),       -- the actual condition/variant of this card
    pricing_sku_id INTEGER REFERENCES skus(sku_id),               -- optional: use a different SKU for pricing (e.g. card is borderline LP/NM, price as NM)
    qty           INTEGER DEFAULT 1,                              -- how many of this exact card from this source
    tags          TEXT,                                            -- comma-separated hidden details: "hidden crease", "surface marks", "trade", etc.
    status        TEXT DEFAULT 'unlisted',                         -- unlisted, photo_requested, listed, skipped, sold
    front_photo_path TEXT,                                         -- local path to front photo
    back_photo_path  TEXT,                                         -- local path to back photo
    ebay_listing_id TEXT,
    listed_at     TEXT,
    created_at    TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_inventory_sku ON inventory(sku_id);
CREATE INDEX IF NOT EXISTS idx_inventory_status ON inventory(status);

-----------------------------------------------------
-- market_snapshots: append-only historical market data
-- One row per card+condition+finish+source per collection date.
-- Stores aggregate stats from external marketplaces for trend analysis.
-----------------------------------------------------
CREATE TABLE IF NOT EXISTS market_snapshots (
    snapshot_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id                 TEXT NOT NULL REFERENCES cards(id),
    condition               TEXT NOT NULL,
    finish                  TEXT NOT NULL DEFAULT 'Regular',
    snapshot_date           TEXT NOT NULL,               -- ISO YYYY-MM-DD
    source                  TEXT NOT NULL DEFAULT 'tcgplayer',  -- 'tcgplayer', 'ebay', etc.

    -- Listing aggregates
    listing_count           INTEGER,
    lowest_listing_price    REAL,
    median_listing_price    REAL,
    mean_listing_price      REAL,
    p25_listing_price       REAL,
    p75_listing_price       REAL,

    -- Sales aggregates (from recent solds available at fetch time)
    recent_sales_count      INTEGER,
    avg_sale_price          REAL,
    median_sale_price       REAL,
    min_sale_price          REAL,
    max_sale_price          REAL,
    newest_sale_date        TEXT,
    oldest_sale_date        TEXT,

    UNIQUE(card_id, condition, finish, snapshot_date, source)
);

CREATE INDEX IF NOT EXISTS idx_snapshots_card ON market_snapshots(card_id);
CREATE INDEX IF NOT EXISTS idx_snapshots_date ON market_snapshots(snapshot_date);
CREATE INDEX IF NOT EXISTS idx_snapshots_card_date ON market_snapshots(card_id, snapshot_date);

-----------------------------------------------------
-- prices: historical price estimates per SKU
-- Composite PK on (sku_id, calculation_date) for time-series pricing
-----------------------------------------------------
CREATE TABLE IF NOT EXISTS prices (
    sku_id                          INTEGER NOT NULL REFERENCES skus(sku_id),
    calculation_date                TEXT NOT NULL,                  -- ISO date string YYYY-MM-DD
    estimated_price                 REAL,                          -- main price estimate
    estimated_liquid_value          REAL,                          -- quick-sale / liquid price
    confidence_percent              INTEGER,                       -- 0-100, how confident the algorithm is
    manual_check_necessary          INTEGER DEFAULT 0,             -- boolean: algorithm flagged for human review
    manually_checked                INTEGER DEFAULT 0,             -- boolean: human has reviewed
    algorithm_version               TEXT,                          -- e.g. "1.0.0"
    estimated_low_price             REAL,
    estimated_high_price            REAL,
    estimated_low_price_liquid      REAL,
    estimated_high_price_liquid     REAL,

    PRIMARY KEY (sku_id, calculation_date)
);

-----------------------------------------------------
-- sales: raw individual sold listings (one row per sale)
-- Comprehensive sales history fetched from TCGplayer (~1 year). Kept entirely
-- separate from the pricing path (prices / market_snapshots) — it feeds the
-- per-card sales graph only and never the pricing algorithm. Refreshed by
-- replacing all rows for a (card_id, source) on each comprehensive fetch.
-----------------------------------------------------
CREATE TABLE IF NOT EXISTS sales (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id        TEXT NOT NULL REFERENCES cards(id),
    condition      TEXT NOT NULL,
    finish         TEXT NOT NULL DEFAULT 'Regular',
    source         TEXT NOT NULL DEFAULT 'tcgplayer',
    order_date     TEXT NOT NULL,                 -- ISO datetime of the sale
    purchase_price REAL NOT NULL,                 -- card price (excl. shipping)
    shipping_price REAL DEFAULT 0,                -- shipping charged on the sale
    quantity       INTEGER DEFAULT 1,
    has_image      INTEGER NOT NULL DEFAULT 0,    -- 1 = photo/custom listing (seller-uploaded image)
    fetched_at     TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_sales_variant ON sales(card_id, condition, finish, source);
CREATE INDEX IF NOT EXISTS idx_sales_card_date ON sales(card_id, order_date);

-----------------------------------------------------
-- market_price_history: TCGplayer "market price" over time (weekly buckets)
-- Sourced from the Infinite price-history API (range=annual), one row per
-- card+condition+finish per week. Graph data only — like `sales`, fully
-- separate from the pricing algorithm. Refreshed by replacing a card's rows.
-----------------------------------------------------
CREATE TABLE IF NOT EXISTS market_price_history (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id           TEXT NOT NULL REFERENCES cards(id),
    condition         TEXT NOT NULL,
    finish            TEXT NOT NULL DEFAULT 'Regular',
    source            TEXT NOT NULL DEFAULT 'tcgplayer',
    bucket_date       TEXT NOT NULL,             -- ISO week-start date YYYY-MM-DD
    market_price      REAL,
    low_sale_price    REAL,
    high_sale_price   REAL,
    quantity_sold     INTEGER,
    transaction_count INTEGER,
    fetched_at        TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_mph_variant ON market_price_history(card_id, condition, finish, source);
CREATE INDEX IF NOT EXISTS idx_mph_card_date ON market_price_history(card_id, bucket_date);

-----------------------------------------------------
-- GRADED CARDS (parallel to skus/inventory/prices)
-- Graded slabs are modeled as their own parallel chain rather than mixed into
-- the raw tables, so the raw path is untouched. A future kind (e.g. sealed)
-- would follow the same parallel pattern. All three reference the SHARED
-- `cards` row (same TCGplayer product id), so a slab relates to its raw version
-- by a plain card_id join.
-----------------------------------------------------

-- graded_skus: a graded *variant* of a card. Differentiated by grading_company
-- + numeric grade. Identity + population come from the grading company (per cert
-- lookup); the grader-identity columns are named GENERICALLY so any company maps
-- onto them. The TCGplayer `card_id` link is OPTIONAL/nullable (deferred; only
-- used for raw-vs-graded pricing, filled later by the graded->raw converter).
CREATE TABLE IF NOT EXISTS graded_skus (
    graded_sku_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    grading_company  TEXT NOT NULL,                        -- PSA, BGS, CGC, SGC, ACE, TAG, Other
    grade            REAL NOT NULL,                        -- numeric grade (10, 9.5, 1, ...)
    grade_label      TEXT,                                 -- grader's exact label ("GEM-MT 10", "PR 1")
    -- Generic grader-supplied identity (works for any grading company)
    grader_spec_id   TEXT,                                 -- grader's internal card/spec id (e.g. PSA SpecID) — identity key
    card_year        TEXT,
    card_set         TEXT,                                 -- grader's set/brand (e.g. "POKEMON EX DEOXYS")
    card_category    TEXT,
    card_number      TEXT,
    card_subject     TEXT,                                 -- card name/subject per grader
    card_variety     TEXT,                                 -- variety (holo, 1st ed, language, ...)
    card_language    TEXT,
    population        INTEGER,                             -- grader population at this grade
    population_higher INTEGER,                             -- count graded higher
    pop_fetched_at   TEXT,                                 -- when pop/identity was last fetched
    -- Optional, DEFERRED link to the TCGplayer card (for raw-vs-graded pricing)
    card_id          TEXT REFERENCES cards(id),            -- NULLABLE — filled later by the graded->raw converter
    finish           TEXT NOT NULL DEFAULT 'Regular',      -- retained for the eventual TCGplayer mapping
    specialty_one    TEXT NOT NULL DEFAULT 'None',
    qty              INTEGER DEFAULT 0,                    -- aggregate count of slabs with this exact graded SKU
    latest_calc_date TEXT,                                 -- date of most recent price calculation
    created_at       TEXT DEFAULT (datetime('now')),

    -- legacy/manual rows (no grader spec) dedup on the card-based key
    UNIQUE(card_id, finish, specialty_one, grading_company, grade)
);

CREATE INDEX IF NOT EXISTS idx_graded_skus_card_id ON graded_skus(card_id);
-- idx_graded_skus_spec (unique, cert-sourced identity) is created in db.py after
-- the graded_skus rebuild, so executescript never references grader_spec_id on a
-- not-yet-migrated table.

-- graded_inventory: individual physical slabs you own. The graded analog of
-- `inventory`; each row references a graded_sku and carries its cert/serial id.
CREATE TABLE IF NOT EXISTS graded_inventory (
    graded_inventory_id INTEGER PRIMARY KEY AUTOINCREMENT,
    graded_sku_id       INTEGER NOT NULL REFERENCES graded_skus(graded_sku_id),
    cert_id             TEXT,                               -- grading cert / slab serial number
    qty                 INTEGER DEFAULT 1,
    tags                TEXT,                               -- comma-separated notes
    status              TEXT DEFAULT 'unlisted',            -- unlisted, photo_requested, listed, skipped, sold
    front_photo_path    TEXT,
    back_photo_path     TEXT,
    ebay_listing_id     TEXT,
    listed_at           TEXT,
    created_at          TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_graded_inventory_sku ON graded_inventory(graded_sku_id);
CREATE INDEX IF NOT EXISTS idx_graded_inventory_status ON graded_inventory(status);

-- graded_prices: historical price estimates per graded SKU (mirror of `prices`).
-- Graded pricing is manual today (TCGplayer has no graded data).
CREATE TABLE IF NOT EXISTS graded_prices (
    graded_sku_id                   INTEGER NOT NULL REFERENCES graded_skus(graded_sku_id),
    calculation_date                TEXT NOT NULL,                  -- ISO date string YYYY-MM-DD
    estimated_price                 REAL,
    estimated_liquid_value          REAL,
    confidence_percent              INTEGER,
    manual_check_necessary          INTEGER DEFAULT 0,
    manually_checked                INTEGER DEFAULT 0,
    algorithm_version               TEXT,
    estimated_low_price             REAL,
    estimated_high_price            REAL,
    estimated_low_price_liquid      REAL,
    estimated_high_price_liquid     REAL,

    PRIMARY KEY (graded_sku_id, calculation_date)
);
