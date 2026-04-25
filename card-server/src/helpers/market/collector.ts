/**
 * Market Data Collector — Orchestrates periodic market data collection.
 *
 * Responsibilities:
 *   1. Determine which cards need data collection (owned cards, cohort neighbors)
 *   2. Fetch market data from external sources (via fetchers.ts)
 *   3. Aggregate raw data into snapshot stats (via aggregators.ts)
 *   4. Store snapshots in the DB (via snapshots.ts)
 *   5. Run the pricing algorithm and store results in the prices table
 *
 * Designed to be called from a cron job, Task Scheduler, or node-cron.
 * Each run is idempotent for a given date (upserts on the unique constraint).
 *
 * Usage:
 *   const collector = new MarketCollector(db);
 *   const report = await collector.collectOwned();
 */

import type Database from 'better-sqlite3';
import { fetchMarketDataForCard, fetchCardInfo, type MarketFetchResult } from './fetchers.js';
import { buildSnapshots, type MarketSnapshot } from './aggregators.js';
import { SnapshotStore } from './snapshots.js';
import { computePrice } from '../pricing/algorithm.js';
import { CardsHelper } from '../crud/cards.js';
import { SkusHelper } from '../crud/skus.js';
import { PricesHelper } from '../crud/prices.js';

// ─── Types ──────────────────────────────────────────────────

export interface CollectionReport {
  date: string;
  cards_processed: number;
  snapshots_written: number;
  prices_written: number;
  errors: { card_id: string; error: string }[];
  duration_ms: number;
}

export interface CollectorOptions {
  /** Delay between API calls in ms. Default 500. */
  delayMs?: number;
  /** Log progress to console. Default true. */
  verbose?: boolean;
}

// ─── Collector ──────────────────────────────────────────────

export class MarketCollector {
  private snapshotStore: SnapshotStore;
  private cards: CardsHelper;
  private skus: SkusHelper;
  private prices: PricesHelper;

  constructor(private db: Database.Database) {
    this.snapshotStore = new SnapshotStore(db);
    this.cards = new CardsHelper(db);
    this.skus = new SkusHelper(db);
    this.prices = new PricesHelper(db);
  }

  /**
   * Collect market data for all cards the user owns (has inventory).
   * This is the primary collection mode.
   */
  async collectOwned(options: CollectorOptions = {}): Promise<CollectionReport> {
    const cardIds = this.getOwnedCardIds();
    return this.collectCards(cardIds, options);
  }

  /**
   * Collect market data for a specific list of card IDs.
   * Use this for targeted collection or cohort/neighbor collection.
   */
  async collectCards(
    cardIds: string[],
    options: CollectorOptions = {},
  ): Promise<CollectionReport> {
    const delayMs = options.delayMs ?? 500;
    const verbose = options.verbose ?? true;
    const today = new Date().toISOString().split('T')[0];
    const start = Date.now();

    // Filter out cards already collected today
    const alreadyCollected = new Set(this.getCardIdsCollectedOnDate(today));
    const remaining = cardIds.filter(id => !alreadyCollected.has(id));

    if (verbose && alreadyCollected.size > 0) {
      const skipped = cardIds.length - remaining.length;
      console.log(`Skipping ${skipped} cards already collected today. ${remaining.length} remaining.\n`);
    }

    const report: CollectionReport = {
      date: today,
      cards_processed: 0,
      snapshots_written: 0,
      prices_written: 0,
      errors: [],
      duration_ms: 0,
    };

    for (let i = 0; i < remaining.length; i++) {
      const cardId = remaining[i];

      if (i > 0 && delayMs > 0) {
        await new Promise(resolve => setTimeout(resolve, delayMs));
      }

      try {
        await this.ensureCardExists(cardId);
        const result = await this.collectSingleCard(cardId, today);
        report.snapshots_written += result.snapshots;
        report.prices_written += result.prices;
        report.cards_processed++;

        if (verbose) {
          const card = this.cards.getById(cardId);
          const name = card?.card_name ?? cardId;
          console.log(
            `[${i + 1}/${remaining.length}] ${name}: ${result.snapshots} snapshots, ${result.prices} prices`,
          );
        }
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        report.errors.push({ card_id: cardId, error: message });
        if (verbose) {
          console.error(`[${i + 1}/${cardIds.length}] ERROR ${cardId}: ${message}`);
        }
      }
    }

    report.duration_ms = Date.now() - start;

    if (verbose) {
      console.log(
        `\nCollection complete: ${report.cards_processed} cards, ` +
        `${report.snapshots_written} snapshots, ${report.prices_written} prices, ` +
        `${report.errors.length} errors, ${(report.duration_ms / 1000).toFixed(1)}s`,
      );
    }

    return report;
  }

  /**
   * Collect market data for cards in the same set as owned cards.
   * These are "cohort neighbors" used for comparative analysis.
   *
   * Only collects cards not already owned (to avoid double-fetching).
   */
  async collectCohort(options: CollectorOptions = {}): Promise<CollectionReport> {
    const cohortIds = this.getCohortCardIds();
    return this.collectCards(cohortIds, options);
  }

  /**
   * Collect stale cards — cards whose latest snapshot is older than `maxAgeDays`.
   */
  async collectStale(
    maxAgeDays = 7,
    options: CollectorOptions = {},
  ): Promise<CollectionReport> {
    const staleIds = this.getStaleCardIds(maxAgeDays);
    return this.collectCards(staleIds, options);
  }

  // ─── Single Card Collection ───────────────────────────────

  /**
   * Fetch, aggregate, and store market data + pricing for one card.
   */
  private async collectSingleCard(
    cardId: string,
    date: string,
  ): Promise<{ snapshots: number; prices: number }> {
    // Fetch all market data for this card
    const fetchResults = await fetchMarketDataForCard(cardId);

    if (fetchResults.length === 0) {
      return { snapshots: 0, prices: 0 };
    }

    // Aggregate into snapshot rows
    const snapshots = buildSnapshots(fetchResults, date);

    // Write snapshots to DB
    this.snapshotStore.upsertBatch(snapshots);

    // Run pricing algorithm and store prices
    const pricesWritten = this.computeAndStorePrices(cardId, fetchResults, date);

    return { snapshots: snapshots.length, prices: pricesWritten };
  }

  /**
   * Run the pricing algorithm for each condition+finish variant and store results.
   * Reuses the raw fetch data so we don't make duplicate API calls.
   */
  private computeAndStorePrices(
    cardId: string,
    fetchResults: MarketFetchResult[],
    date: string,
  ): number {
    let count = 0;

    for (const result of fetchResults) {
      const sku = this.skus.getOrCreate({
        card_id: cardId,
        condition: result.condition as any,
        finish: result.finish as any,
        specialty_one: 'None',
        specialty_two: 'None',
        qty: 0,
      });

      const hasManualReviewSpecialty = sku.specialty_two !== 'None';

      const priceResult = computePrice({
        activeListings: result.activeListings,
        soldListings: result.soldListings,
        condition: result.condition,
        finish: result.finish,
        hasManualReviewSpecialty,
      });

      this.prices.upsert({
        sku_id: sku.sku_id,
        calculation_date: date,
        estimated_price: priceResult.estimated_price,
        estimated_liquid_value: priceResult.estimated_liquid_value,
        confidence_percent: priceResult.confidence_percent,
        manual_check_necessary: priceResult.manual_check_necessary,
        manually_checked: false,
        algorithm_version: priceResult.algorithm_version,
        estimated_low_price: priceResult.estimated_low_price,
        estimated_high_price: priceResult.estimated_high_price,
        estimated_low_price_liquid: priceResult.estimated_low_price_liquid,
        estimated_high_price_liquid: priceResult.estimated_high_price_liquid,
      });

      count++;
    }

    return count;
  }

  // ─── Card Selection Queries ───────────────────────────────

  /** Get all unique card IDs that have inventory (cards the user owns). */
  private getOwnedCardIds(): string[] {
    const rows = this.db.prepare(`
      SELECT DISTINCT s.card_id
      FROM inventory i
      JOIN skus s ON i.sku_id = s.sku_id
      WHERE i.status NOT IN ('sold')
    `).all() as { card_id: string }[];
    return rows.map(r => r.card_id);
  }

  /**
   * Get card IDs in the same sets as owned cards, excluding already-owned cards.
   * These are cohort neighbors for comparative analysis.
   */
  private getCohortCardIds(): string[] {
    const rows = this.db.prepare(`
      SELECT DISTINCT c2.id
      FROM cards c2
      WHERE c2.set_name IN (
        SELECT DISTINCT c.set_name
        FROM inventory i
        JOIN skus s ON i.sku_id = s.sku_id
        JOIN cards c ON s.card_id = c.id
        WHERE i.status NOT IN ('sold') AND c.set_name IS NOT NULL
      )
      AND c2.id NOT IN (
        SELECT DISTINCT s.card_id
        FROM inventory i
        JOIN skus s ON i.sku_id = s.sku_id
        WHERE i.status NOT IN ('sold')
      )
    `).all() as { id: string }[];
    return rows.map(r => r.id);
  }

  /**
   * Get owned card IDs whose latest snapshot is older than maxAgeDays.
   * Cards with no snapshots at all are always included.
   */
  private getStaleCardIds(maxAgeDays: number): string[] {
    const cutoff = new Date();
    cutoff.setDate(cutoff.getDate() - maxAgeDays);
    const cutoffStr = cutoff.toISOString().split('T')[0];

    const rows = this.db.prepare(`
      SELECT DISTINCT s.card_id
      FROM inventory i
      JOIN skus s ON i.sku_id = s.sku_id
      WHERE i.status NOT IN ('sold')
      AND s.card_id NOT IN (
        SELECT card_id FROM market_snapshots
        WHERE snapshot_date >= ?
      )
    `).all(cutoffStr) as { card_id: string }[];
    return rows.map(r => r.card_id);
  }

  /** Get card IDs that already have snapshots for a given date. */
  private getCardIdsCollectedOnDate(date: string): string[] {
    const rows = this.db.prepare(
      'SELECT DISTINCT card_id FROM market_snapshots WHERE snapshot_date = ?',
    ).all(date) as { card_id: string }[];
    return rows.map(r => r.card_id);
  }

  // ─── Card Metadata ────────────────────────────────────────

  /**
   * Ensure a card exists in the DB before collecting data for it.
   * If not present, fetches metadata from TCGplayer and stores it.
   */
  private async ensureCardExists(cardId: string): Promise<void> {
    const existing = this.cards.getById(cardId);
    if (existing) return;

    const fetched = await fetchCardInfo(cardId);
    if (!fetched) {
      throw new Error(`Card ${cardId} not found on TCGplayer`);
    }

    this.cards.upsert({
      id: fetched.metadata.tcgplayer_id,
      card_name: fetched.metadata.card_name,
      set_name: fetched.metadata.set_name,
      product_line: fetched.metadata.product_line,
      card_type: fetched.metadata.card_type,
      visual_layout: null,
      rarity: fetched.metadata.rarity,
      card_number: fetched.metadata.card_number,
      product_type: null,
      era: null,
      set_type: null,
    });
  }

  // ─── Accessors ────────────────────────────────────────────

  /** Get the underlying snapshot store for direct queries. */
  getSnapshotStore(): SnapshotStore {
    return this.snapshotStore;
  }
}
