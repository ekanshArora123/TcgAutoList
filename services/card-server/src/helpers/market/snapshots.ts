/**
 * Market Snapshots — DB read/write for the market_snapshots table.
 *
 * Handles inserting new snapshots and querying historical data
 * for trend analysis and feature extraction.
 */

import type Database from 'better-sqlite3';
import type { MarketSnapshot } from './aggregators.js';

// ─── Types ──────────────────────────────────────────────────

/** A market_snapshots row as returned from the DB. */
export interface MarketSnapshotRow extends MarketSnapshot {
  snapshot_id: number;
}

/** Filters for querying snapshot history. */
export interface SnapshotQuery {
  card_id?: string;
  condition?: string;
  finish?: string;
  source?: string;
  date_from?: string;
  date_to?: string;
  limit?: number;
  offset?: number;
}

// ─── Snapshot Store ─────────────────────────────────────────

export class SnapshotStore {
  private insertStmt: Database.Statement;
  private getLatestStmt: Database.Statement;
  private getHistoryStmt: Database.Statement;

  constructor(private db: Database.Database) {
    this.insertStmt = db.prepare(`
      INSERT INTO market_snapshots (
        card_id, condition, finish, snapshot_date, source,
        listing_count, lowest_listing_price, median_listing_price,
        mean_listing_price, p25_listing_price, p75_listing_price,
        recent_sales_count, avg_sale_price, median_sale_price,
        min_sale_price, max_sale_price, newest_sale_date, oldest_sale_date
      ) VALUES (
        @card_id, @condition, @finish, @snapshot_date, @source,
        @listing_count, @lowest_listing_price, @median_listing_price,
        @mean_listing_price, @p25_listing_price, @p75_listing_price,
        @recent_sales_count, @avg_sale_price, @median_sale_price,
        @min_sale_price, @max_sale_price, @newest_sale_date, @oldest_sale_date
      ) ON CONFLICT(card_id, condition, finish, snapshot_date, source)
      DO UPDATE SET
        listing_count = excluded.listing_count,
        lowest_listing_price = excluded.lowest_listing_price,
        median_listing_price = excluded.median_listing_price,
        mean_listing_price = excluded.mean_listing_price,
        p25_listing_price = excluded.p25_listing_price,
        p75_listing_price = excluded.p75_listing_price,
        recent_sales_count = excluded.recent_sales_count,
        avg_sale_price = excluded.avg_sale_price,
        median_sale_price = excluded.median_sale_price,
        min_sale_price = excluded.min_sale_price,
        max_sale_price = excluded.max_sale_price,
        newest_sale_date = excluded.newest_sale_date,
        oldest_sale_date = excluded.oldest_sale_date
    `);

    this.getLatestStmt = db.prepare(`
      SELECT * FROM market_snapshots
      WHERE card_id = ? AND condition = ? AND finish = ?
      ORDER BY snapshot_date DESC
      LIMIT 1
    `);

    this.getHistoryStmt = db.prepare(`
      SELECT * FROM market_snapshots
      WHERE card_id = ? AND condition = ? AND finish = ?
      ORDER BY snapshot_date DESC
      LIMIT ? OFFSET ?
    `);
  }

  /** Insert or update a single snapshot. Idempotent per (card, condition, finish, date, source). */
  upsert(snapshot: MarketSnapshot): void {
    this.insertStmt.run(snapshot);
  }

  /** Insert multiple snapshots in a single transaction. */
  upsertBatch(snapshots: MarketSnapshot[]): number {
    const tx = this.db.transaction((rows: MarketSnapshot[]) => {
      for (const row of rows) {
        this.insertStmt.run(row);
      }
      return rows.length;
    });
    return tx(snapshots);
  }

  /** Get the most recent snapshot for a card+condition+finish. */
  getLatest(cardId: string, condition: string, finish: string): MarketSnapshotRow | null {
    return (this.getLatestStmt.get(cardId, condition, finish) as MarketSnapshotRow) ?? null;
  }

  /** Get snapshot history for a card+condition+finish, newest first. */
  getHistory(
    cardId: string,
    condition: string,
    finish: string,
    limit = 90,
    offset = 0,
  ): MarketSnapshotRow[] {
    return this.getHistoryStmt.all(cardId, condition, finish, limit, offset) as MarketSnapshotRow[];
  }

  /** Flexible query with multiple filters. */
  search(query: SnapshotQuery): MarketSnapshotRow[] {
    const conditions: string[] = [];
    const params: any[] = [];

    if (query.card_id) {
      conditions.push('card_id = ?');
      params.push(query.card_id);
    }
    if (query.condition) {
      conditions.push('condition = ?');
      params.push(query.condition);
    }
    if (query.finish) {
      conditions.push('finish = ?');
      params.push(query.finish);
    }
    if (query.source) {
      conditions.push('source = ?');
      params.push(query.source);
    }
    if (query.date_from) {
      conditions.push('snapshot_date >= ?');
      params.push(query.date_from);
    }
    if (query.date_to) {
      conditions.push('snapshot_date <= ?');
      params.push(query.date_to);
    }

    const where = conditions.length > 0 ? `WHERE ${conditions.join(' AND ')}` : '';
    const limit = query.limit ?? 100;
    const offset = query.offset ?? 0;

    const sql = `SELECT * FROM market_snapshots ${where} ORDER BY snapshot_date DESC LIMIT ? OFFSET ?`;
    params.push(limit, offset);

    return this.db.prepare(sql).all(...params) as MarketSnapshotRow[];
  }

  /** Get all unique card_ids that have snapshots. */
  getTrackedCardIds(): string[] {
    const rows = this.db.prepare('SELECT DISTINCT card_id FROM market_snapshots').all() as { card_id: string }[];
    return rows.map(r => r.card_id);
  }

  /** Get the most recent snapshot date across all cards. */
  getLastCollectionDate(): string | null {
    const row = this.db.prepare('SELECT MAX(snapshot_date) as max_date FROM market_snapshots').get() as { max_date: string | null } | undefined;
    return row?.max_date ?? null;
  }

  /** Count total snapshots in the DB. */
  count(): number {
    const row = this.db.prepare('SELECT COUNT(*) as cnt FROM market_snapshots').get() as { cnt: number };
    return row.cnt;
  }

  /** Delete snapshots older than a given date. */
  pruneOlderThan(beforeDate: string): number {
    return this.db.prepare('DELETE FROM market_snapshots WHERE snapshot_date < ?').run(beforeDate).changes;
  }
}
