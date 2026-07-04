# Grading providers (graded cards)

Fetchers that pull graded-card data from grading companies' public APIs by cert
number, plus notes on how graded cards are modeled. Sibling to `tcgplayer/`; one
module per company (`psa.py` today), behind a small provider registry so more
companies drop in later. The graded DB chain (`graded_skus` / `graded_inventory`
/ `graded_prices`) lives in `helpers/crud/graded_*.py`; the write workflow is
`CollectionService.add_graded_by_cert(...)`.

## Design: identity is decoupled from the TCGplayer link

A graded card's **identity and population come from the grading company** (e.g.
PSA `GetByCertNumber`: year, set, number, subject, variety, grade, population).
This works for 100% of graded cards, in any language, including cards TCGplayer
doesn't carry.

The link to a TCGplayer `cards` row (`graded_skus.card_id`) is **optional and
nullable** — it exists only to enable raw-vs-graded price comparison later, and
is intentionally left `NULL` at add time for now.

Grader-identity columns on `graded_skus` are named **generically** (not
PSA-specific) so any grading company maps onto them — assume every company
exposes equivalents (`grader_spec_id`, `card_year`, `card_set`, `card_number`,
`card_subject`, `card_variety`, `card_language`, `grade_label`, `population`,
`population_higher`, `pop_fetched_at`).

## ⚠️ Future work — these features are DEFERRED and MUST be built

1. **Graded → raw mapper (the "comprehensive converter").** Resolve a TCGplayer
   `card_id` for a graded card so raw-vs-graded pricing (e.g. NM price vs PSA 8
   price) can work. Today `card_id` is left `NULL`; there is **no** auto-matcher
   and **no** manual card-picker yet. When built, this converter is the single
   place that populates `graded_skus.card_id` — any interim linking hook must be
   isolated and removable so this replaces it cleanly. Must handle: English cards
   with a TCGplayer equivalent, same-card different-language, and cards that exist
   in neither (leave unlinked).

2. **Comprehensive slab-image getter.** Fetch + store graded slab images by cert
   number. Blocked today: PSA's free API image endpoint is quota-gated (~1/day)
   and the website is Cloudflare-protected (plain GETs 403). Until this exists,
   graded cards have **no** slab image (the frontend falls back to the raw card
   image where a `card_id` link exists, else a placeholder). Possible approach:
   spend the 1/day image call to learn PSA's image-CDN URL pattern, then fetch
   images off the CDN directly; or a headless-browser scrape; or a paid tier.

Also deferred (owner will handle): the raw-card price estimate shown next to a
graded card.
