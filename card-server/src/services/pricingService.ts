import type Database from 'better-sqlite3';
import { SkusHelper } from '../helpers/crud/skus.js';
import { PricesHelper } from '../helpers/crud/prices.js';
import { computePrice, extrapolateAcrossConditions, computeLiquidValue } from '../helpers/pricing/algorithm.js';
import type { PricingResult } from '../helpers/pricing/algorithm.js';
import { fetchSoldListings, fetchActiveListings } from '../helpers/tcgplayer/fetchPrices.js';
import type { Price, CreatePrice } from '../types.js';

/**
 * PricingService — High-level operations the agent calls for pricing cards.
 *
 * Composes price CRUD helpers + TCGplayer price fetchers + pricing algorithm
 * into workflow-level operations.
 *
 * Flow: fetch external data → run algorithm → store results → return to agent.
 */
export class PricingService {
  private skus: SkusHelper;
  private prices: PricesHelper;

  constructor(private db: Database.Database) {
    this.skus = new SkusHelper(db);
    this.prices = new PricesHelper(db);
  }

  // ─── Price Lookups (DB only) ───────────────────────────────

  /** Get the latest price for a SKU from the local DB. */
  getLatestPrice(skuId: number): Price | null {
    return this.prices.getLatest(skuId);
  }

  /** Get latest price for an inventory item (respects pricing_sku_id override). */
  getLatestPriceForItem(inventoryId: number): Price | null {
    return this.prices.getLatestForInventoryItem(inventoryId);
  }

  /** Get price history for a SKU. */
  getPriceHistory(skuId: number, limit?: number, offset?: number): Price[] {
    return this.prices.getHistory(skuId, limit, offset);
  }

  /** Search prices with filters. */
  searchPrices(filters: {
    sku_id?: number;
    card_id?: string;
    date_from?: string;
    date_to?: string;
    manual_check_only?: boolean;
    limit?: number;
    offset?: number;
  }): Price[] {
    return this.prices.search(filters);
  }

  /** Compare prices for a card across all its conditions (NM, LP, MP, etc.). */
  comparePricesAcrossConditions(cardId: string): {
    condition: string;
    finish: string;
    estimated_price: number | null;
    estimated_liquid_value: number | null;
  }[] {
    return this.prices.getPricesByCardAcrossConditions(cardId);
  }

  /** Get all prices needing manual review. */
  getPendingManualChecks(): (Price & { card_name: string; condition: string })[] {
    return this.prices.getPendingManualChecks();
  }

  /** Mark a price as manually verified. */
  markPriceChecked(skuId: number, calculationDate: string): boolean {
    return this.prices.markAsChecked(skuId, calculationDate);
  }

  // ─── Price Storage (manual / bulk import) ──────────────────

  /** Store a single price record. */
  recordPrice(input: CreatePrice): Price {
    return this.prices.upsert(input);
  }

  /** Bulk import price records. */
  bulkImportPrices(prices: CreatePrice[]): number {
    return this.prices.bulkUpsert(prices);
  }

  // ─── Raw Data Fetching (for agent / LLM inspection) ────────

  /** Fetch sold listings from TCGplayer. Returns raw data. */
  async fetchSoldListingsData(tcgplayerId: string, condition?: string, finish?: string) {
    return fetchSoldListings(tcgplayerId, condition, finish);
  }

  /** Fetch active for-sale listings from TCGplayer. Returns raw data. */
  async fetchActiveListingsData(tcgplayerId: string, condition?: string, finish?: string) {
    return fetchActiveListings(tcgplayerId, condition, finish);
  }

  // ─── Pricing Workflows (fetch + algorithm + store) ─────────

  /**
   * Fetch all available TCGplayer data for a card and price every condition/finish combo.
   * This is the primary "refresh prices" operation.
   *
   * For each condition/finish variant:
   *   1. Fetch active listings (lowest listing = primary anchor)
   *   2. Fetch sold listings (sanity check + fallback)
   *   3. Run pricing algorithm → get estimated price + confidence + reasoning
   *   4. Store in DB
   */
  async fetchAndStorePrices(tcgplayerId: string): Promise<(Price & { reasoning: string })[]> {
    const [allSolds, allListings] = await Promise.all([
      fetchSoldListings(tcgplayerId),
      fetchActiveListings(tcgplayerId),
    ]);

    const today = new Date().toISOString().split('T')[0];
    const results: (Price & { reasoning: string })[] = [];

    // Group solds and listings by condition+finish
    const soldsByKey = groupBy(allSolds, s => `${s.condition}|${s.finish}`);
    const listingsByKey = groupBy(allListings, l => `${l.condition}|${l.finish}`);

    // Collect all unique condition+finish combos from all data sources
    const allKeys = new Set<string>();
    for (const key of soldsByKey.keys()) allKeys.add(key);
    for (const key of listingsByKey.keys()) allKeys.add(key);

    for (const key of allKeys) {
      const [condition, finish] = key.split('|');

      const sku = this.skus.getOrCreate({
        card_id: tcgplayerId,
        condition: condition as any,
        finish: finish as any,
        specialty_one: 'None',
        specialty_two: 'None',
        qty: 0,
      });

      // specialty_one is for TCGplayer-level variants (1st Ed, etc.) — NOT manual review.
      // specialty_two is for things like graded/errors — DOES trigger manual review.
      const hasManualReviewSpecialty = sku.specialty_two !== 'None';

      // Run the pricing algorithm
      const result = computePrice({
        activeListings: listingsByKey.get(key) ?? [],
        soldListings: soldsByKey.get(key) ?? [],
        condition,
        finish,
        hasManualReviewSpecialty,
      });

      // Store in DB
      const price = this.prices.upsert({
        sku_id: sku.sku_id,
        calculation_date: today,
        estimated_price: result.estimated_price,
        estimated_liquid_value: result.estimated_liquid_value,
        confidence_percent: result.confidence_percent,
        manual_check_necessary: result.manual_check_necessary,
        manually_checked: false,
        algorithm_version: result.algorithm_version,
        estimated_low_price: result.estimated_low_price,
        estimated_high_price: result.estimated_high_price,
        estimated_low_price_liquid: result.estimated_low_price_liquid,
        estimated_high_price_liquid: result.estimated_high_price_liquid,
      });

      results.push({ ...price, reasoning: result.reasoning });
    }

    return results;
  }

  /**
   * Price a specific condition+finish for a card.
   * Fetches targeted data (only for that condition) and runs the algorithm.
   *
   * More efficient than fetchAndStorePrices when you only need one variant.
   */
  async computeAndStorePrice(
    tcgplayerId: string,
    condition: string,
    finish: string,
  ): Promise<(Price & { reasoning: string }) | null> {
    const [solds, listings] = await Promise.all([
      fetchSoldListings(tcgplayerId, condition, finish),
      fetchActiveListings(tcgplayerId, condition, finish),
    ]);

    const sku = this.skus.getOrCreate({
      card_id: tcgplayerId,
      condition: condition as any,
      finish: finish as any,
      specialty_one: 'None',
      specialty_two: 'None',
      qty: 0,
    });

    const hasManualReviewSpecialty = sku.specialty_two !== 'None';

    const result = computePrice({
      activeListings: listings,
      soldListings: solds,
      condition,
      finish,
      hasManualReviewSpecialty,
    });

    // If algorithm returned null AND we have no data, try cross-condition extrapolation
    if (result.estimated_price === null) {
      const extrapolated = await this.tryExtrapolateFromOtherConditions(tcgplayerId, condition, finish);
      if (extrapolated) {
        result.estimated_price = extrapolated.price;
        result.estimated_liquid_value = extrapolated.liquidValue;
        result.confidence_percent = Math.min(result.confidence_percent, 35);
        result.manual_check_necessary = true;
        result.reasoning += ` Extrapolated from ${extrapolated.sourceCondition} data (${extrapolated.sourcePrice.toFixed(2)}).`;
      }
    }

    const today = new Date().toISOString().split('T')[0];
    const price = this.prices.upsert({
      sku_id: sku.sku_id,
      calculation_date: today,
      estimated_price: result.estimated_price,
      estimated_liquid_value: result.estimated_liquid_value,
      confidence_percent: result.confidence_percent,
      manual_check_necessary: result.manual_check_necessary,
      manually_checked: false,
      algorithm_version: result.algorithm_version,
      estimated_low_price: result.estimated_low_price,
      estimated_high_price: result.estimated_high_price,
      estimated_low_price_liquid: result.estimated_low_price_liquid,
      estimated_high_price_liquid: result.estimated_high_price_liquid,
    });

    return { ...price, reasoning: result.reasoning };
  }

  /**
   * When no data exists for a specific condition, look at other conditions
   * of the same card and extrapolate using the condition tier discount.
   *
   * Tries NM first (most likely to have data), then walks outward.
   */
  private async tryExtrapolateFromOtherConditions(
    tcgplayerId: string,
    targetCondition: string,
    targetFinish: string,
  ): Promise<{ price: number; liquidValue: number; sourceCondition: string; sourcePrice: number } | null> {
    // Check DB for existing prices on other conditions of same card+finish
    const otherSkus = this.skus.search({ card_id: tcgplayerId });

    // Prefer NM as source, then LP, then MP — most common conditions with data
    const preferredSources = ['NM', 'LP-NM', 'LP', 'MP-LP', 'MP', 'HP-MP', 'HP'];

    for (const sourceCondition of preferredSources) {
      if (sourceCondition === targetCondition) continue;

      const sourceSku = otherSkus.find(s => s.condition === sourceCondition && s.finish === targetFinish);
      if (!sourceSku) continue;

      const sourcePrice = this.prices.getLatest(sourceSku.sku_id);
      if (!sourcePrice?.estimated_price) continue;

      const extrapolated = extrapolateAcrossConditions(sourcePrice.estimated_price, sourceCondition, targetCondition);
      if (extrapolated === null) continue;

      return {
        price: extrapolated,
        liquidValue: computeLiquidValue(extrapolated),
        sourceCondition,
        sourcePrice: sourcePrice.estimated_price,
      };
    }

    return null;
  }

  // ─── Maintenance ───────────────────────────────────────────

  /** Delete all prices for a SKU. */
  deletePricesForSku(skuId: number): number {
    return this.prices.deleteForSku(skuId);
  }

  /** Prune old price history before a date. */
  pruneOldPrices(beforeDate: string): number {
    return this.prices.deleteOlderThan(beforeDate);
  }
}

// ─── Utility ─────────────────────────────────────────────────

function groupBy<T>(items: T[], keyFn: (item: T) => string): Map<string, T[]> {
  const map = new Map<string, T[]>();
  for (const item of items) {
    const key = keyFn(item);
    const group = map.get(key);
    if (group) group.push(item);
    else map.set(key, [item]);
  }
  return map;
}
