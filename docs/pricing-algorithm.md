# Pricing Algorithm — Full Reference

All pricing logic lives in `card-server/src/helpers/pricing/algorithm.ts`. All configurable constants live in `pricingConfig.ts` (same directory).

## Algorithm v1: Lowest Listing Anchor

### Decision Tree

1. **Active listings exist?**
   - YES -> Use lowest listing as anchor. Confidence: 70%.
   - NO -> Go to step 3.

2. **Sold data exists? (sanity check)**
   - Divergence > 30% AND cheap card (< $5) -> Listings win, ignore solds (shipping noise). Confidence: 75%.
   - Divergence > 30% AND solds lower -> Listings may be stale. Use sold average instead. Flag for review. Confidence: 60%.
   - Divergence > 30% AND solds higher -> Someone is undercutting. Listing price is fine. Confidence: 75%.
   - Divergence <= 30% -> Solds confirm listing price. Confidence: 85%.
   - Bonus: >= 3 solds adds +10 confidence (capped at 95%).
   - No solds -> Listings only, lower confidence: 50%.

3. **No active listings, solds exist?**
   - YES -> Use sold average. Confidence: 55% (or 40% if < 3 solds, flagged for review).
   - NO -> Unpriceable. Null price, confidence 0%, flagged.

4. **Edge case flags:**
   - Price > `HIGH_VALUE_THRESHOLD` ($50) -> manual review
   - `hasManualReviewSpecialty` (graded, errors) -> manual review
   - Fewer than `MIN_SOLDS_FOR_CONFIDENCE` (3) solds -> lower confidence

5. **Derived values:**
   - Low estimate: sold min, or `lowest_listing * 0.9` if no solds
   - High estimate: sold max, or `lowest_listing * 1.15` if no solds
   - Liquid value for all price points

### Recent-Sales Window

The sold-listings fetch can return a card's full sales history (hundreds of rows). Feeding all of it into the sold stats would average in stale prices and produce an all-time min/max range. So the sold **stats** (fallback average, low/high range, data-volume bonus) use only a recent window:

- **Window = whichever group is larger:** all sales within the last `RECENT_SOLDS_DAYS` (5), or the `RECENT_SOLDS_MAX_COUNT` (25) most recent sales.
- Fast-moving cards stay on truly fresh data (the 5-day branch); illiquid cards still get a floor of 25 recent comps.
- This is **separate** from the divergence window (`max(7 days, 3 sales)`) and the blend window (`30 days`), which operate over the full sold list with their own bounds.
- Implemented in `_recent_solds()`; constants in `config.py`.

### Cheap Card Shipping Model (< $5)

TCGplayer offers free shipping at $5+ with one seller. Most buyers bundle cheap cards.

- **Listing prices assume free shipping** — the listed price IS the real price.
- **Sold prices are noisy** — some include $1 shipping (single-card purchase), some don't (bundled).
- **For pricing:** Use `listed_price` only. Ignore `shipping_price` from API.
- **For divergence:** Listings always win. Don't flag or switch to solds.
- **For liquid value:** Seller shipping cost = $0.

### Liquid Value Formula

```
liquid_value = (sell_price * (1 - FEE_RATE)) - shipping_cost
```

| Price Range | Shipping Cost | Method |
|-------------|---------------|--------|
| < $5 | $0 | Buyer bundles for free shipping |
| $5 - $25 | $1 | PWE (plain white envelope) |
| > $25 | $5 | Tracked bubble mailer |

### Cross-Condition Extrapolation

When no data exists for a specific condition, extrapolate from another condition:

- **30% discount per full condition tier, compounding:** NM $10 -> LP $7.00 -> MP $4.90 -> HP $3.43
- **In-between conditions = average of neighbors:** LP-NM = (NM + LP) / 2
- **Bidirectional:** Can go worse->better (divide by multiplier) or better->worse (multiply)
- **Always flagged** for manual review with max 35% confidence

Primary conditions (best to worst): `MINT, NM, LP, MP, HP, DMG`
In-between: `LP-NM` = [NM, LP], `MP-LP` = [LP, MP], `HP-MP` = [MP, HP]

All extrapolation logic is in `extrapolateAcrossConditions()` — the single function to edit.

### Confidence Scoring Summary

| Confidence | Scenario |
|------------|----------|
| 85-95% | Listing + solds agree (< 30% divergence), good data volume |
| 70-75% | Listing exists, solds diverge or missing (cheap card exception) |
| 55-60% | Solds only (no listings), or stale listings overridden by solds |
| 40-50% | Limited data (few solds, no listings) |
| 0% | No data at all |

### Future Versions

- **v2:** Sales velocity (sales per 48hr window). Fast-selling cards can be priced above lowest listing.
- **v3 (LLM sampling):** TCGplayer solds, TCGplayer listings, eBay solds, eBay listings as separate tools the LLM reasons over for edge cases.
