# Pricing Algorithm — Full Reference

The algorithm lives in `services/card_server/helpers/pricing/`:

| File | Role |
|---|---|
| `algorithm.py` | The decision tree. Pure — takes inputs, returns a result, touches nothing. |
| `inputs.py` | `PricingInputs`: the handful of scalars a price is computed from, and the two builders that produce them (from raw rows, or from a stored snapshot). |
| `config.py` | Every tunable, as module constants **and** as a `PricingConfig` the algorithm actually reads. |
| `repricer.py` | The only writer of `prices`. Reads stored inputs, runs the algorithm, persists. |
| `conditions.py` | The condition vocabulary (primary / in-between / alias). |

Current `ALGORITHM_VERSION`: **`weighted-blend-v3`**.

## Collection and pricing are separate

A `collect` run does two things per card, and they are deliberately decoupled:

1. **Collect** — fetch active listings and sold listings, aggregate them, write
   `market_snapshots`.
2. **Price** — `Repricer.price_card` reads that stored state back and writes
   `prices`.

The collector never runs the algorithm itself. Because step 2's input is the
database rather than the fetch, it can run alone:

```bash
collect.bat --reprice-only          # regenerate every owned card's price, no network
collect.bat --reprice-only --cards 88075
```

That is the cheap path for tuning: change a constant, reprice the whole
collection in seconds, without spending a single request against TCGplayer's
rate limit. It is also why the collector and the on-demand seller path cannot
drift — both build a `PricingInputs`, both call `compute_price`, both write
through `repricer.store_price`.

### The inputs a price is a function of

```
lowest_listing_price     cheapest listing by effective price
listing_count            stored and reported; does NOT affect the price today
divergence_sale_price    plain mean of the last DIVERGENCE_SALE_COUNT (5) sales
weighted_sale_price      age-weighted mean over ≤ MAX_SALES_CONSIDERED (25) sales
sale_count, min/max_sale_price, newest_sale_date
```

All of them live on `market_snapshots`, so nothing else needs to be kept. Raw
listings are never persisted anywhere — they are a live snapshot, and only their
order statistics matter.

## Decision Tree

**1. Lowest active listing exists?** → anchor on it, confidence **70**.

The anchor is the cheapest listing by *effective* price: a listing under $5
counts its `listed_price` alone, one at $5+ counts `listed_price +
shipping_price`. The test is per listing, not per card. Low-reputation sellers
were already filtered out at fetch time (`MIN_SELLER_RATING` 80,
`MIN_SELLER_SALES` 30).

**2. Is the anchor under `CHEAP_CARD_THRESHOLD` ($5)?** → it stands, full stop.
Sold prices at that level are shipping noise, so they are ignored outright — no
divergence test, no blend, however far the sales disagree. Confidence **75**.

**3. Otherwise, compare the anchor to `divergence_sale_price`:**

```
divergence = |lowest_listing - divergence_sale_price| / divergence_sale_price
```

| Case | Price | Confidence |
|---|---|---|
| Divergence ≤ 30% | The listing, unmodified | **85** |
| Divergence > 30% | **Blend** (below) | **70** listing-high / **75** listing-low |
| No sold data | The listing, unmodified | **50** |

Then **≥ 3 sales → +10**, capped at 95.

**4. No active listings** → the age-weighted sold mean, confidence **55**; under
`MIN_SOLDS_FOR_CONFIDENCE` (3) sales it drops to **40** and is flagged. No
listings *and* no sales → null price, confidence **0**, flagged.

**5. Review flags.** Price > `HIGH_VALUE_THRESHOLD` ($50) → manual review. An
error specialty → manual review (see [Error variants](#error-variants)).

## The Blend

When divergence exceeds the threshold the price stops being the listing and
becomes a weighted combination of the listing and the age-weighted sold mean:

```
price = w × weighted_sale_price + (1 - w) × lowest_listing_price
```

`w` — the sold side's share — depends on direction:

**Listing above the sold signal** (a possible moon price): `w` ramps linearly
with how far apart they are.

```
w = clamp(SALES_WEIGHT_MIN + (divergence - 0.30) × slope,  0.30,  0.70)
```

| Divergence | Sold weight |
|---|---|
| 0.30 (just tripped) | 0.30 |
| 0.75 | 0.60 |
| ≥ 0.90 | 0.70 (clamped) |

**Listing below the sold signal** (an undercut or a lowball): a flat
`SALES_WEIGHT_LISTING_BELOW` (0.70). There is no "how far below" reading that
makes a below-market listing more trustworthy, so there is nothing to ramp on.

The listing always keeps at least 30% of the price, no matter how extreme the
gap — a lone absurd listing is pulled toward the sales, never discarded.

**Worked example.** Listing $350, last-5 sold mean $200, age-weighted sold mean
$175:

```
divergence = |350 - 200| / 200        = 0.75
w          = 0.30 + 0.45 × 0.667      = 0.60
price      = 0.60 × 175 + 0.40 × 350  = $245
```

### Age weighting

Within the sold side, each sale is weighted by a half-life on its age:

```
weight_i = 0.5 ** (age_days / SOLD_WEIGHT_HALF_LIFE_DAYS)     # 30 days
```

| Age | Weight |
|---|---|
| today | 1.00 |
| 30 days | 0.50 |
| 90 days | 0.125 |
| 180 days | 0.016 |
| 1 year | 0.0002 |

Old sales fade out rather than being cut off at a cliff, so a card that last
sold months ago still gets corrected — just gently. Sales with no usable date are
dropped (they can be neither ordered nor aged), future-dated rows are clamped to
age 0, and only the `MAX_SALES_CONSIDERED` (25) most recent enter the mean.

> **This mean does not drift with time.** Ageing every sale by the same interval
> scales every weight by the same factor, which cancels in the ratio. A stored
> `weighted_sale_price` is therefore still correct weeks later — it only moves
> when new sales arrive. The one exception is the half-life itself: changing it
> invalidates stored values, because it changes the *relative* weights. Every
> other constant reprices for free; re-tuning the half-life means re-aggregating,
> i.e. a collect run.

### Cheap Card Shipping Model (< $5)

TCGplayer gives free shipping at $5+ from one seller, and buyers bundle cheap
cards to reach it.

- **Listings**: the listed price is the real price — `shipping_price` is ignored.
- **Solds**: ignored entirely for pricing. Not blended, not compared.
- **Liquid value**: seller shipping cost $0.

The cheap test reads the *raw* lowest listing, deliberately: it decides whether
the sold signal is trustworthy, so it must not itself depend on a price that the
sold signal helped produce.

## Derived Values

**Low / high band.** When both signals exist the band is simply the two of them,
lower first:

```
low  = min(lowest_listing, weighted_sale_price)
high = max(lowest_listing, weighted_sale_price)
```

A blended price is a convex combination of exactly those two numbers, so **the
estimate always lands inside its own band**. With only one signal there is
nothing to bracket against, so the band falls back to the sold min/max, or to
`lowest_listing × 0.9 / × 1.15`.

**Liquid value.**

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

TCGplayer has data for five tiers (`NM, LP, MP, HP, DMG`), so three kinds of SKU
have no snapshot of their own and are derived from the ones that do. All three
derive strictly within the same `(finish, specialty_one)` — a plain SKU must
never inherit a 1st Edition price.

This is also why `price_sku` is not simply "price one row": pricing an `MP-LP`
means loading and pricing its `MP` and `LP` neighbours first.

**Aliases.** `MINT` → NM, `DM` → DMG. The same physical tier under another name,
so the estimate carries over verbatim and only the reasoning changes.

**Interpolation (preferred).** An in-between grade whose *both* neighbours priced
this run is the midpoint of two measured prices: `MP-LP = (LP + MP) / 2`.
`_plan_variants` plans both neighbour fetches in the same card visit, so they can
never be at different ages. Confidence is the lower of the two; review flags
carry through, but interpolation adds none of its own — it is a measurement, not
a guess.

**Extrapolation (fallback).** With only one neighbour, the price is projected
across tiers instead:

- 30% discount per full tier, compounding: NM $10 → LP $7.00 → MP $4.90 → HP $3.43
- Bidirectional (worse→better divides, better→worse multiplies)
- **Always flagged** for review — a guess about a card nobody is selling

Either way a derived price over `HIGH_VALUE_THRESHOLD` is flagged like a direct
one, and derived low/high is the mean of the sources' low/high. If **neither**
neighbour produced a price, no row is written — the SKU keeps its previous
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
SKUs inherit the base variant's price **with manual review forced on**, via
`flag_error_variant`.

`compute_price`'s own `has_manual_review_specialty` parameter cannot cover this:
every caller resolves its SKU with `specialty_two='None'` (it is part of the
composite key), so the SKU priced directly is always the plain one. The flag is
applied in the derived pass, where the error SKUs actually are. The seller
pipeline covers the same ground separately, in the tier router.

## Confidence Scoring

| Confidence | Scenario |
|---|---|
| 85–95% | Listing and sales agree (≤ 30% divergence) |
| 85% | Blended upward from a below-market listing, good volume |
| 80% | Blended down from a listing above the sales, good volume |
| 75% | Cheap card (< $5) — listing-only by design |
| 55% | Sales only, no listings |
| 50% | Listing only, no sold data |
| 40% | Fewer than 3 sales and no listings — flagged |
| 35% | Extrapolated from another condition's *stored* price (seller pipeline only) |
| 0% | No data at all — unpriceable, flagged |

Confidence drives tier routing (`orchestrator/tier_router.py`): ≥ 80 → Tier 1,
40–79 → Tier 2, < 40 → Tier 3, with high value or a review flag promoting a
Tier-1 card to Tier 2.

## Entry points

| Caller | Entry point | What it prices |
|---|---|---|
| **Market collector** (`collect.py`) | `Repricer.price_card` | Every SKU of a card, from the snapshot just written. The real pricing run. |
| **Reprice-only** (`collect.py --reprice-only`) | `Repricer.price_card` | The same, from whatever is already stored. No network. |
| **Dashboard / orchestrator** | `PricingService.reprice_card` / `reprice_sku` | On demand, from stored data. |
| **Seller pipeline** (`/next`) | `PricingService.compute_and_store_price` | One condition+finish, fetched live. |

The first three are the same code path. The seller pipeline still differs, and
only in the live-fetch case:

|  | Repricer | `PricingService.compute_and_store_price` |
|---|---|---|
| Variants | Every SKU of the card, in-between grades resolved from both neighbours | The single requested condition+finish |
| In-between grades | Interpolated | **Not interpolated** |
| Fallback when unpriceable | None — no row is written | `_try_extrapolate_from_other_conditions`: walks `NM, LP-NM, LP, MP-LP, MP, HP-MP, HP, DMG, DM` against **previously stored** prices (any date, possibly stale) at the same finish and printing, extrapolates, clamps confidence to ≤ 35, flags review |

So an `MP-LP` card met through `/next` is extrapolated from whatever is in the
DB, not interpolated from fresh neighbours. Making the seller path call
`reprice_sku` after its fetch is open work.

## What gets stored

A collect run writes four tables and keeps **no raw listing or sold rows**.

- `cards` — only when the card is missing.
- `skus` — `get_or_create` per priced variant; `latest_calc_date` bumped.
- `market_snapshots` — one row per **fetched** variant per day: the listing
  aggregates (count / lowest / median / mean / p25 / p75), the sale aggregates
  (count / avg / median / min / max / newest / oldest), and the two pricing
  inputs (`divergence_sale_price`, `weighted_sale_price`). Derived SKUs get no
  snapshot: this table records market observations, not estimates.
- `prices` — one row per SKU per day, including `reasoning`, the algorithm's own
  account of which branch fired.

Both are upserts keyed by date, so a same-day re-run (or `--force`) replaces in
place.

Rows written before the pricing-input columns existed carry NULL there; such a
variant reads as having no sold signal and prices off its listings alone until
the next collect run refreshes it.

The raw sold rows that *are* kept come from a separate collector
(`collect_sales.py` → `sales`, `market_price_history`), which feeds the per-card
graph, and nothing in the pricing path reads it. It remains the natural source
for calibrating the half-life offline — it holds the same sales keyed the same
way — but no tool does that today.

## Future Versions

- **Listing count in the price.** It is collected and stored but deliberately
  unused: a lone listing sets the price exactly as forty do. Damping the anchor
  when there are few listings is the obvious next step.
- **Sales velocity** — sales per 48hr window; fast-selling cards can be priced
  above the lowest listing.
- **LLM sampling** — TCGplayer solds/listings and eBay solds/listings as separate
  tools an LLM reasons over for edge cases (Tier 2/3; see
  `dashboard/backend/orchestrator/PIPELINE.md`).
