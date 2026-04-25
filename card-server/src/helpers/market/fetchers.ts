/**
 * Market Data Fetchers — All external API calls for market data collection.
 *
 * This is the single file for all web requests to external marketplaces.
 * To add a new data source (e.g., eBay), add fetch functions here and
 * wire them into the collector.
 *
 * Currently supported:
 *   - TCGplayer active listings (via mp-search-api)
 *   - TCGplayer sold listings (via mpapi)
 *   - TCGplayer card metadata + price snapshot (via mp-search-api)
 *   - TCGplayer set catalog (via mpapi)
 *
 * All TCGplayer logic is delegated to the existing tcgplayer/ helpers.
 * This file re-exports them and adds market-collection-oriented wrappers.
 */

import {
  fetchActiveListings,
  fetchSoldListings,
  fetchSetInfo,
  type TcgPlayerActiveListing,
  type TcgPlayerSoldListing,
  type TcgPlayerSetInfo,
} from '../tcgplayer/fetchPrices.js';

import {
  fetchCardInfo,
  fetchCardMetadata,
  fetchCardPriceInfo,
  type TcgPlayerCardMetadata,
  type TcgPlayerCardPriceInfo,
  type TcgPlayerCardFetchResult,
} from '../tcgplayer/fetchCardInfo.js';

// ─── Re-exports ─────────────────────────────────────────────
// Consumers of market data should import from this file.

export {
  fetchActiveListings,
  fetchSoldListings,
  fetchSetInfo,
  fetchCardInfo,
  fetchCardMetadata,
  fetchCardPriceInfo,
};

export type {
  TcgPlayerActiveListing,
  TcgPlayerSoldListing,
  TcgPlayerSetInfo,
  TcgPlayerCardMetadata,
  TcgPlayerCardPriceInfo,
  TcgPlayerCardFetchResult,
};

// ─── Combined Market Fetch ──────────────────────────────────

/** Everything we know about a card's market state at fetch time. */
export interface MarketFetchResult {
  cardId: string;
  condition: string;
  finish: string;
  source: 'tcgplayer';
  activeListings: TcgPlayerActiveListing[];
  soldListings: TcgPlayerSoldListing[];
}

/**
 * Fetch all market data for a single card across all conditions/finishes.
 * Returns one MarketFetchResult per condition+finish combo found.
 *
 * This is the primary entry point for the collector.
 */
export async function fetchMarketDataForCard(
  tcgplayerId: string,
): Promise<MarketFetchResult[]> {
  const [allListings, allSolds] = await Promise.all([
    fetchActiveListings(tcgplayerId),
    fetchSoldListings(tcgplayerId),
  ]);

  // Group by condition+finish
  const keyedListings = groupByConditionFinish(allListings, l => l.condition, l => l.finish);
  const keyedSolds = groupByConditionFinish(allSolds, s => s.condition, s => s.finish);

  const allKeys = new Set<string>();
  for (const key of keyedListings.keys()) allKeys.add(key);
  for (const key of keyedSolds.keys()) allKeys.add(key);

  const results: MarketFetchResult[] = [];
  for (const key of allKeys) {
    const [condition, finish] = key.split('|');
    results.push({
      cardId: tcgplayerId,
      condition,
      finish,
      source: 'tcgplayer',
      activeListings: keyedListings.get(key) ?? [],
      soldListings: keyedSolds.get(key) ?? [],
    });
  }

  return results;
}

/**
 * Fetch market data for a specific condition+finish of a card.
 * More efficient than fetchMarketDataForCard when you only need one variant.
 */
export async function fetchMarketDataForVariant(
  tcgplayerId: string,
  condition: string,
  finish: string,
): Promise<MarketFetchResult> {
  const [activeListings, soldListings] = await Promise.all([
    fetchActiveListings(tcgplayerId, condition, finish),
    fetchSoldListings(tcgplayerId, condition, finish),
  ]);

  return {
    cardId: tcgplayerId,
    condition,
    finish,
    source: 'tcgplayer',
    activeListings,
    soldListings,
  };
}

/**
 * Fetch market data for multiple cards sequentially with rate limiting.
 * Returns a flat array of MarketFetchResults (multiple per card).
 */
export async function fetchMarketDataBatch(
  tcgplayerIds: string[],
  delayMs = 500,
): Promise<MarketFetchResult[]> {
  const allResults: MarketFetchResult[] = [];

  for (let i = 0; i < tcgplayerIds.length; i++) {
    if (i > 0 && delayMs > 0) {
      await new Promise(resolve => setTimeout(resolve, delayMs));
    }
    const results = await fetchMarketDataForCard(tcgplayerIds[i]);
    allResults.push(...results);
  }

  return allResults;
}

// ─── Future: eBay ──────────────────────────────────────────
//
// To add eBay market data:
//   1. Add fetch functions here (e.g., fetchEbaySoldListings, fetchEbayActiveListings)
//   2. Define eBay-specific types (EbayActiveListing, EbaySoldListing)
//   3. Create a fetchEbayMarketDataForCard() wrapper
//   4. In collector.ts, call the eBay fetcher alongside TCGplayer
//   5. In aggregators.ts, add aggregation for eBay data (same stats shape)
//   6. Store with source='ebay' in market_snapshots

// ─── Helpers ────────────────────────────────────────────────

function groupByConditionFinish<T>(
  items: T[],
  getCondition: (item: T) => string,
  getFinish: (item: T) => string,
): Map<string, T[]> {
  const map = new Map<string, T[]>();
  for (const item of items) {
    const key = `${getCondition(item)}|${getFinish(item)}`;
    const group = map.get(key);
    if (group) group.push(item);
    else map.set(key, [item]);
  }
  return map;
}
