/**
 * Market Data Aggregators — Transform raw listing/sold arrays into aggregate stats.
 *
 * These functions take the raw data from fetchers.ts and produce the
 * statistical summaries stored in market_snapshots.
 *
 * All functions are pure (no side effects, no DB access).
 */

import type { TcgPlayerActiveListing, TcgPlayerSoldListing } from './fetchers.js';
import type { MarketFetchResult } from './fetchers.js';
import { CHEAP_CARD_THRESHOLD } from '../pricing/pricingConfig.js';

// ─── Types ──────────────────────────────────────────────────

export interface ListingAggregates {
  listing_count: number;
  lowest_listing_price: number | null;
  median_listing_price: number | null;
  mean_listing_price: number | null;
  p25_listing_price: number | null;
  p75_listing_price: number | null;
}

export interface SalesAggregates {
  recent_sales_count: number;
  avg_sale_price: number | null;
  median_sale_price: number | null;
  min_sale_price: number | null;
  max_sale_price: number | null;
  newest_sale_date: string | null;
  oldest_sale_date: string | null;
}

export interface MarketSnapshot {
  card_id: string;
  condition: string;
  finish: string;
  snapshot_date: string;
  source: string;
  listing_count: number;
  lowest_listing_price: number | null;
  median_listing_price: number | null;
  mean_listing_price: number | null;
  p25_listing_price: number | null;
  p75_listing_price: number | null;
  recent_sales_count: number;
  avg_sale_price: number | null;
  median_sale_price: number | null;
  min_sale_price: number | null;
  max_sale_price: number | null;
  newest_sale_date: string | null;
  oldest_sale_date: string | null;
}

// ─── Listing Aggregation ────────────────────────────────────

/**
 * Compute aggregate stats from an array of active listings.
 *
 * Uses the same cheap-card logic as the pricing algorithm:
 * cards under $5 use listed_price only (ignore shipping),
 * cards >= $5 use listed_price + shipping_price.
 */
export function aggregateListings(listings: TcgPlayerActiveListing[]): ListingAggregates {
  if (listings.length === 0) {
    return {
      listing_count: 0,
      lowest_listing_price: null,
      median_listing_price: null,
      mean_listing_price: null,
      p25_listing_price: null,
      p75_listing_price: null,
    };
  }

  const prices = listings
    .map(l => effectivePrice(l))
    .sort((a, b) => a - b);

  return {
    listing_count: prices.length,
    lowest_listing_price: round(prices[0]),
    median_listing_price: round(median(prices)),
    mean_listing_price: round(mean(prices)),
    p25_listing_price: round(percentile(prices, 25)),
    p75_listing_price: round(percentile(prices, 75)),
  };
}

/**
 * Get the effective price of a listing, accounting for cheap card shipping.
 */
function effectivePrice(listing: TcgPlayerActiveListing): number {
  return listing.listed_price < CHEAP_CARD_THRESHOLD
    ? listing.listed_price
    : listing.listed_price + listing.shipping_price;
}

// ─── Sales Aggregation ──────────────────────────────────────

/**
 * Compute aggregate stats from an array of sold listings.
 */
export function aggregateSales(solds: TcgPlayerSoldListing[]): SalesAggregates {
  if (solds.length === 0) {
    return {
      recent_sales_count: 0,
      avg_sale_price: null,
      median_sale_price: null,
      min_sale_price: null,
      max_sale_price: null,
      newest_sale_date: null,
      oldest_sale_date: null,
    };
  }

  const prices = solds.map(s => s.sold_price).sort((a, b) => a - b);

  const dates = solds
    .map(s => s.sold_date)
    .filter(d => d !== '')
    .sort();

  return {
    recent_sales_count: solds.length,
    avg_sale_price: round(mean(prices)),
    median_sale_price: round(median(prices)),
    min_sale_price: round(prices[0]),
    max_sale_price: round(prices[prices.length - 1]),
    newest_sale_date: dates.length > 0 ? dates[dates.length - 1] : null,
    oldest_sale_date: dates.length > 0 ? dates[0] : null,
  };
}

// ─── Combined Snapshot ──────────────────────────────────────

/**
 * Build a complete MarketSnapshot from a MarketFetchResult.
 * This is the main function the collector calls.
 */
export function buildSnapshot(
  fetchResult: MarketFetchResult,
  snapshotDate: string,
): MarketSnapshot {
  const listings = aggregateListings(fetchResult.activeListings);
  const sales = aggregateSales(fetchResult.soldListings);

  return {
    card_id: fetchResult.cardId,
    condition: fetchResult.condition,
    finish: fetchResult.finish,
    snapshot_date: snapshotDate,
    source: fetchResult.source,
    ...listings,
    ...sales,
  };
}

/**
 * Build snapshots for all variants returned by a multi-variant fetch.
 */
export function buildSnapshots(
  fetchResults: MarketFetchResult[],
  snapshotDate: string,
): MarketSnapshot[] {
  return fetchResults.map(r => buildSnapshot(r, snapshotDate));
}

// ─── Stats Helpers ──────────────────────────────────────────

function mean(sorted: number[]): number {
  const sum = sorted.reduce((a, b) => a + b, 0);
  return sum / sorted.length;
}

function median(sorted: number[]): number {
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0
    ? (sorted[mid - 1] + sorted[mid]) / 2
    : sorted[mid];
}

/**
 * Compute the p-th percentile using nearest-rank method.
 * Input must be sorted ascending.
 */
function percentile(sorted: number[], p: number): number {
  const index = Math.ceil((p / 100) * sorted.length) - 1;
  return sorted[Math.max(0, index)];
}

function round(n: number): number {
  return Math.round(n * 100) / 100;
}
