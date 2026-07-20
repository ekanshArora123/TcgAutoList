// Price -> liquid value, mirroring the backend macro so the graded editor's live
// preview matches what the server stores. The BACKEND is the source of truth:
// services/card_server/helpers/pricing/algorithm.py `compute_liquid_value` with
// constants in pricing/config.py — on save the server derives and persists the
// liquid value; this is only the client-side preview. Keep the constants in sync
// with config.py (one place each side) so there's no variance.
export const FEE_RATE = 0.15; // platform fee (~15%, TCGplayer/eBay)
export const CHEAP_CARD_THRESHOLD = 5; // < this: $0 shipping (buyers bundle)
export const SHIPPING_THRESHOLD = 25; // > this: tracked shipping
export const SHIPPING_COST_LOW = 1; // $5-$25: PWE
export const SHIPPING_COST_HIGH = 5; // > $25: tracked

/** liquid = max(price*(1-FEE_RATE) - shipping, 0), shipping tiered by price. */
export function computeLiquidValue(sellPrice: number): number {
  const shipping =
    sellPrice < CHEAP_CARD_THRESHOLD
      ? 0
      : sellPrice > SHIPPING_THRESHOLD
        ? SHIPPING_COST_HIGH
        : SHIPPING_COST_LOW;
  const afterFees = sellPrice * (1 - FEE_RATE);
  return Math.max(Math.round((afterFees - shipping) * 100) / 100, 0);
}
