# Pricing Algorithm — Full Reference

The algorithm lives in `services/card_server/helpers/pricing/algorithm.py`; every
tunable constant is in `config.py` beside it, and the condition vocabulary
(primary / in-between / alias) is in `conditions.py`. Two callers run it:

| Caller | Entry point | What it prices |
|--------|-------------|----------------|
| **Market collector** (`collect.py`) | `MarketCollector._compute_and_store_prices` | The whole owned collection, per variant, once a day. This is the real pricing run. |
| **Seller pipeline** (`/next`) | `PricingService.compute_and_store_price` | One condition+finish on demand, when the stored price is missing or stale. |

They do **not** behave identically — see [Two entry points](#two-entry-points).

Current `ALGORITHM_VERSION`: **`blended-v2`**.

## Decision Tree

Inputs: the active listings and sold listings for one variant
(card + condition + finish + `specialty_one`), plus whether the SKU carries an
error specialty.

**1. Lowest active listing exists?** → anchor on it, confidence **70**.

The anchor is the cheapest listing by *effective* price: a listing under $5 counts
its `listed_price` alone, one at $5+ counts `listed_price + shipping_price`. The
test is per listing, not per card. Low-reputation sellers were already filtered out
at fetch time (`MIN_SELLER_RATING` 80, `MIN_SELLER_SALES` 30).

**2. Sold data exists?** Then sanity-check the anchor against
`effective_sale_price` — the average of whichever group is larger, the last
`DIVERGENCE_RECENT_DAYS` (7) of sales or the 3 most recent sales. *The 3-sale
fallback has no age limit,* so this can be measured off very old sales.

| Case | Result | Confidence |
|---|---|---|
| Divergence ≤ 30% | Solds confirm the listing | **85** |
| Divergence > 30%, card < $5 | Listing wins — sold prices are shipping noise | **75** |
| Divergence > 30%, solds **higher** | Someone is undercutting; the lowest listing is a competitive price | **75** |
| Divergence > 30%, listing higher, sales within 30 days | **Blend**: `0.5 × listing + 0.5 × 30-day sold average` | **70** |
| Divergence > 30%, listing higher, **no** sales within 30 days | Nothing fresh to correct against — keep the listing price | **60** |
| No sold data at all | Listings only | **50** |

Then: **≥ 3 recent solds → +10**, capped at 95. Fewer than 3 costs nothing here;
it only lowers confidence in the no-listings branch below.

The last two rows differ only in whether a correction was *possible*. Scoring
them apart is deliberate: at equal confidence, nothing downstream could tell a
price corrected against fresh sales from one that wanted correcting and had no
data.

**3. No active listings** → fall back to the recent-window sold average,
confidence **55**; under `MIN_SOLDS_FOR_CONFIDENCE` (3) solds it drops to **40**
and is flagged for review. No listings *and* no solds → null price, confidence
**0**, flagged.

**4. Review flags.** Price > `HIGH_VALUE_THRESHOLD` ($50) → manual review. An
error specialty → manual review (applied by the collector's derived pass; see
[Error variants](#error-variants)).

**5. Derived values.**

- Low / high estimate: the recent-window sold **min / max** when solds exist,
  otherwise `lowest_listing × 0.9` and `× 1.15`.
- Liquid value for every price point.

Low/high is the observed sold *range*, not a confidence interval around the
estimate — with a blended or listing-anchored price it can sit outside that
range, which is expected.

### Recent-Sales Window

Sold **stats** (fallback average, low/high range, the volume bonus) use only a
recent slice: whichever group is larger, sales within `RECENT_SOLDS_DAYS` (5) or
the `RECENT_SOLDS_MAX_COUNT` (25) most recent sales. Fast-moving cards stay on
genuinely fresh data; illiquid ones still get a floor of comps.

This window is separate from the divergence window (`max(7 days, 3 sales)`) and
the blend window (30 days), both of which run over the full sold list.

> **Today it rarely binds.** `fetch_sold_listings` caps a card at 25 sold rows
> (`max_results`), which equals `RECENT_SOLDS_MAX_COUNT`, so the count branch
> almost always selects everything fetched. It is a safety net that starts doing
> real work if the fetch cap is raised. With `TCGPLAYER_AUTH_COOKIE` set, those
> 25 rows are shared across *all* conditions and finishes of the card, so a
> variant with little volume can come back with no solds at all.

### Cheap Card Shipping Model (< $5)

TCGplayer gives free shipping at $5+ from one seller, and buyers bundle cheap
cards to reach it.

- **Listings**: the listed price is the real price — `shipping_price` is ignored.
- **Solds**: noisy (some include $1 shipping, some don't), so on divergence the
  listing always wins.
- **Liquid value**: seller shipping cost $0.

### Liquid Value

```
liquid_value = max(sell_price × (1 - FEE_RATE) - shipping_cost, 0)
```

| Price | Shipping | Method |
|---|---|---|
| < $5 | $0 | Buyer bundles for free shipping |
| $5 – $25 | $1 | PWE (plain white envelope) |
| > $25 | $5 | Tracked bubble mailer |

Boundaries fall in the middle tier: exactly $5 and exactly $25 both cost $1. The
frontend mirrors this in `dashboard/frontend/src/pricing.ts` for live previews;
the backend remains the source of truth.

## Deriving What TCGplayer Doesn't Sell

TCGplayer has data for five tiers (`NM, LP, MP, HP, DMG`). Three kinds of SKU are
derived from those by the collector's second pass, after the direct pass has
priced every fetched variant. All three derive strictly within the same
`(finish, specialty_one)` — a plain SKU must never inherit a 1st Edition price.

**Aliases.** `MINT` → NM, `DM` → DMG. The same physical tier under another name,
so the estimate carries over verbatim and only the reasoning changes.

**Interpolation (preferred).** An in-between grade whose *both* neighbors priced
from live data this run is the midpoint of two measured prices:
`MP-LP = (LP + MP) / 2`. `_plan_variants` plans both neighbor fetches in the same
card visit, so they can never be at different ages. Confidence is the lower of
the two; review flags carry through from the neighbors, but interpolation adds
none of its own — it is a measurement, not a guess.

**Extrapolation (fallback).** With only one neighbor, the price is projected
across tiers instead:

- 30% discount per full tier, compounding: NM $10 → LP $7.00 → MP $4.90 → HP $3.43
- Bidirectional (worse→better divides, better→worse multiplies)
- **Always flagged** for review — a guess about a card nobody is selling

Either way a derived price over `HIGH_VALUE_THRESHOLD` is flagged like a direct
one, and derived low/high is the mean of the sources' low/high. If **neither**
neighbor produced a price, no row is written at all — the SKU keeps its previous
estimate rather than gaining a fabricated one.

```
Primary (best → worst):  MINT, NM, LP, MP, HP, DMG
In-between:              LP-NM = [NM, LP]   MP-LP = [LP, MP]
                         HP-MP = [MP, HP]   DM-HP = [HP, DMG]
Aliases:                 MINT → NM          DM → DMG
```

`conditions.py` owns this vocabulary and every consumer resolves through it
(`normalize_condition`, `is_primary`, `primary_neighbors`, `expand_to_primaries`),
including both ends of `extrapolate_across_conditions` — so alternate spellings
and stray whitespace resolve rather than falling off the ladder.

### Error variants

`specialty_two` (miscuts, holo bleeds, …) is an off-TCGplayer attribute, so no
tier exists for it and the only available estimate is the plain card's. Error
SKUs therefore inherit the base variant's price **with manual review forced on**,
via `flag_error_variant`.

Note that `compute_price`'s own `has_manual_review_specialty` parameter cannot
cover this: every caller resolves its SKU with `specialty_two='None'` (it is part
of the composite key), so the SKU being priced directly is always the plain one.
The flag is applied in the derived pass, where the error SKUs actually are. The
seller pipeline covers the same ground separately, in the tier router.

## Confidence Scoring

| Confidence | Scenario |
|---|---|
| 85–95% | Listing and solds agree (< 30% divergence), good volume |
| 70–80% | Listing anchored: solds diverge, are absent, or the price was blended |
| 60% | Listing diverges above the solds but no recent comps to blend with |
| 55% | Solds only, no listings |
| 40% | Fewer than 3 solds and no listings — flagged |
| 35% | Extrapolated from another condition's *stored* price (seller pipeline only) |
| 0% | No data at all — unpriceable, flagged |

Confidence drives tier routing (`orchestrator/tier_router.py`): ≥ 80 → Tier 1,
40–79 → Tier 2, < 40 → Tier 3, with high value or a review flag promoting a
Tier-1 card to Tier 2.

## Two entry points

The collector and the seller pipeline diverge, and only the collector runs the
model described above in full:

|  | Collector | `PricingService.compute_and_store_price` |
|---|---|---|
| Variants | Every owned variant, both neighbors of an in-between planned together | The single requested condition+finish |
| In-between grades | Interpolated from neighbors priced this run | **Not interpolated** |
| Aliases / error variants | Derived pass handles them | Prices the plain SKU as requested |
| Fallback when unpriceable | None — no row is written | `_try_extrapolate_from_other_conditions`: walks `NM, LP-NM, LP, MP-LP, MP, HP-MP, HP` against **previously stored** prices (any date, possibly stale), extrapolates, clamps confidence to ≤ 35, flags review |

So an `MP-LP` card met through `/next` is extrapolated from whatever is in the
DB, not interpolated from fresh neighbors. Making the seller path reuse the
collector's derivation is open work.

## What gets stored

A collector run writes to four tables and keeps **no raw listing or sold rows** —
the individual listings and sales are aggregated and discarded.

- `cards` — only when the card is missing.
- `skus` — `get_or_create` per priced variant; `latest_calc_date` bumped.
- `market_snapshots` — one row per **fetched** variant per day (listing count /
  lowest / median / mean / p25 / p75, and sale count / avg / median / min / max /
  newest / oldest). Derived SKUs get no snapshot: this table records market
  observations, not estimates.
- `prices` — one row per SKU per day, including `reasoning`, the algorithm's own
  account of which branch fired.

Both are upserts keyed by date, so a same-day re-run (or `--force`) replaces in
place. Snapshot sale aggregates are computed over the full fetched sold list,
while the algorithm uses the recent window — they can differ if the fetch cap is
ever raised.

The raw sold rows that *are* kept come from a separate collector
(`collect_sales.py` → `sales`, `market_price_history`), which feeds the per-card
graph and is never read by this algorithm. Active listings are never persisted in
raw form anywhere.

## Future Versions

- **Sales velocity** — sales per 48hr window; fast-selling cards can be priced
  above the lowest listing.
- **LLM sampling** — TCGplayer solds/listings and eBay solds/listings as separate
  tools an LLM reasons over for edge cases (Tier 2/3; see
  `dashboard/backend/orchestrator/PIPELINE.md`).
