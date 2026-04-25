/**
 * Market Data Module — Longitudinal market data collection and analysis.
 *
 * Architecture:
 *   fetchers.ts    — All external API calls (TCGplayer, future eBay)
 *   aggregators.ts — Raw data → aggregate stats (pure functions)
 *   snapshots.ts   — DB read/write for market_snapshots table
 *   collector.ts   — Orchestration: select cards, fetch, aggregate, store
 *
 * Usage:
 *   import { MarketCollector } from './helpers/market/index.js';
 *   const collector = new MarketCollector(db);
 *   await collector.collectOwned();           // all owned cards
 *   await collector.collectStale(7);          // cards not collected in 7+ days
 *   await collector.collectCohort();          // same-set neighbors
 *   await collector.collectCards(['12345']);   // specific card IDs
 */

export { MarketCollector, type CollectionReport, type CollectorOptions } from './collector.js';
export { SnapshotStore, type MarketSnapshotRow, type SnapshotQuery } from './snapshots.js';
export {
  aggregateListings, aggregateSales, buildSnapshot, buildSnapshots,
  type ListingAggregates, type SalesAggregates, type MarketSnapshot,
} from './aggregators.js';
export {
  fetchMarketDataForCard, fetchMarketDataForVariant, fetchMarketDataBatch,
  fetchActiveListings, fetchSoldListings, fetchCardInfo, fetchCardMetadata,
  fetchCardPriceInfo, fetchSetInfo,
  type MarketFetchResult,
  type TcgPlayerActiveListing, type TcgPlayerSoldListing,
  type TcgPlayerCardMetadata, type TcgPlayerCardPriceInfo,
} from './fetchers.js';
