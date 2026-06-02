import type Database from 'better-sqlite3';
import type { Price, CreatePrice } from '../../types.js';

export class PricesHelper {
  private stmts: {
    insert: Database.Statement;
    upsert: Database.Statement;
    getLatest: Database.Statement;
    getHistory: Database.Statement;
    markChecked: Database.Statement;
    deleteForSku: Database.Statement;
    deleteOlderThan: Database.Statement;
    updateSkuCalcDate: Database.Statement;
  };

  constructor(private db: Database.Database) {
    this.stmts = {
      insert: db.prepare(`
        INSERT INTO prices (sku_id, calculation_date, estimated_price, estimated_liquid_value,
          confidence_percent, manual_check_necessary, manually_checked, algorithm_version,
          estimated_low_price, estimated_high_price, estimated_low_price_liquid, estimated_high_price_liquid)
        VALUES (@sku_id, @calculation_date, @estimated_price, @estimated_liquid_value,
          @confidence_percent, @manual_check_necessary, @manually_checked, @algorithm_version,
          @estimated_low_price, @estimated_high_price, @estimated_low_price_liquid, @estimated_high_price_liquid)
      `),
      upsert: db.prepare(`
        INSERT INTO prices (sku_id, calculation_date, estimated_price, estimated_liquid_value,
          confidence_percent, manual_check_necessary, manually_checked, algorithm_version,
          estimated_low_price, estimated_high_price, estimated_low_price_liquid, estimated_high_price_liquid)
        VALUES (@sku_id, @calculation_date, @estimated_price, @estimated_liquid_value,
          @confidence_percent, @manual_check_necessary, @manually_checked, @algorithm_version,
          @estimated_low_price, @estimated_high_price, @estimated_low_price_liquid, @estimated_high_price_liquid)
        ON CONFLICT(sku_id, calculation_date) DO UPDATE SET
          estimated_price = excluded.estimated_price,
          estimated_liquid_value = excluded.estimated_liquid_value,
          confidence_percent = excluded.confidence_percent,
          manual_check_necessary = excluded.manual_check_necessary,
          manually_checked = excluded.manually_checked,
          algorithm_version = excluded.algorithm_version,
          estimated_low_price = excluded.estimated_low_price,
          estimated_high_price = excluded.estimated_high_price,
          estimated_low_price_liquid = excluded.estimated_low_price_liquid,
          estimated_high_price_liquid = excluded.estimated_high_price_liquid
      `),
      getLatest: db.prepare('SELECT * FROM prices WHERE sku_id = ? ORDER BY calculation_date DESC LIMIT 1'),
      getHistory: db.prepare('SELECT * FROM prices WHERE sku_id = ? ORDER BY calculation_date DESC LIMIT ? OFFSET ?'),
      markChecked: db.prepare('UPDATE prices SET manually_checked = 1 WHERE sku_id = @sku_id AND calculation_date = @calculation_date'),
      deleteForSku: db.prepare('DELETE FROM prices WHERE sku_id = ?'),
      deleteOlderThan: db.prepare('DELETE FROM prices WHERE calculation_date < ?'),
      updateSkuCalcDate: db.prepare(`
        UPDATE skus SET latest_calc_date = @date
        WHERE sku_id = @sku_id AND (latest_calc_date IS NULL OR latest_calc_date < @date)
      `),
    };
  }

  private toBoolParams(input: CreatePrice): Record<string, unknown> {
    return {
      ...input,
      manual_check_necessary: input.manual_check_necessary ? 1 : 0,
      manually_checked: input.manually_checked ? 1 : 0,
    };
  }

  create(input: CreatePrice): Price {
    this.stmts.insert.run(this.toBoolParams(input));
    this.stmts.updateSkuCalcDate.run({ sku_id: input.sku_id, date: input.calculation_date });
    return this.stmts.getLatest.get(input.sku_id) as Price;
  }

  upsert(input: CreatePrice): Price {
    this.stmts.upsert.run(this.toBoolParams(input));
    this.stmts.updateSkuCalcDate.run({ sku_id: input.sku_id, date: input.calculation_date });
    return this.stmts.getLatest.get(input.sku_id) as Price;
  }

  bulkUpsert(prices: CreatePrice[]): number {
    const tx = this.db.transaction((items: CreatePrice[]) => {
      const affectedSkus = new Map<number, string>(); // sku_id -> max date
      let count = 0;
      for (const price of items) {
        this.stmts.upsert.run(this.toBoolParams(price));
        const existing = affectedSkus.get(price.sku_id);
        if (!existing || price.calculation_date > existing) {
          affectedSkus.set(price.sku_id, price.calculation_date);
        }
        count++;
      }
      for (const [skuId, date] of affectedSkus) {
        this.stmts.updateSkuCalcDate.run({ sku_id: skuId, date });
      }
      return count;
    });
    return tx(prices);
  }

  getLatest(skuId: number): Price | null {
    return (this.stmts.getLatest.get(skuId) as Price) ?? null;
  }

  getLatestForInventoryItem(inventoryId: number): Price | null {
    const row = this.db.prepare(`
      SELECT p.* FROM prices p
      JOIN inventory i ON COALESCE(i.pricing_sku_id, i.sku_id) = p.sku_id
      JOIN skus s ON COALESCE(i.pricing_sku_id, i.sku_id) = s.sku_id
      WHERE i.inventory_id = ? AND p.calculation_date = s.latest_calc_date
    `).get(inventoryId) as Price | undefined;
    return row ?? null;
  }

  getHistory(skuId: number, limit = 50, offset = 0): Price[] {
    return this.stmts.getHistory.all(skuId, limit, offset) as Price[];
  }

  search(filters: {
    sku_id?: number;
    card_id?: string;
    date_from?: string;
    date_to?: string;
    manual_check_only?: boolean;
    limit?: number;
    offset?: number;
  }): Price[] {
    const conditions: string[] = [];
    const params: Record<string, unknown> = {};

    if (filters.sku_id !== undefined) {
      conditions.push('p.sku_id = @sku_id');
      params.sku_id = filters.sku_id;
    }
    if (filters.card_id) {
      conditions.push('s.card_id = @card_id');
      params.card_id = filters.card_id;
    }
    if (filters.date_from) {
      conditions.push('p.calculation_date >= @date_from');
      params.date_from = filters.date_from;
    }
    if (filters.date_to) {
      conditions.push('p.calculation_date <= @date_to');
      params.date_to = filters.date_to;
    }
    if (filters.manual_check_only) {
      conditions.push('p.manual_check_necessary = 1 AND p.manually_checked = 0');
    }

    const needsJoin = !!filters.card_id;
    const from = needsJoin
      ? 'prices p JOIN skus s ON p.sku_id = s.sku_id'
      : 'prices p';
    const where = conditions.length > 0 ? `WHERE ${conditions.join(' AND ')}` : '';
    const limit = filters.limit ?? 50;
    const offset = filters.offset ?? 0;

    return this.db.prepare(`SELECT p.* FROM ${from} ${where} ORDER BY p.calculation_date DESC LIMIT @limit OFFSET @offset`)
      .all({ ...params, limit, offset }) as Price[];
  }

  getPendingManualChecks(): (Price & { card_name: string; condition: string })[] {
    return this.db.prepare(`
      SELECT p.*, c.card_name, s.condition
      FROM prices p
      JOIN skus s ON p.sku_id = s.sku_id
      JOIN cards c ON s.card_id = c.id
      WHERE p.manual_check_necessary = 1 AND p.manually_checked = 0
      ORDER BY p.calculation_date DESC
    `).all() as (Price & { card_name: string; condition: string })[];
  }

  getPricesByCardAcrossConditions(cardId: string): {
    condition: string;
    finish: string;
    estimated_price: number | null;
    estimated_liquid_value: number | null;
  }[] {
    return this.db.prepare(`
      SELECT s.condition, s.finish, p.estimated_price, p.estimated_liquid_value
      FROM skus s
      JOIN prices p ON s.sku_id = p.sku_id AND p.calculation_date = s.latest_calc_date
      WHERE s.card_id = ?
      ORDER BY p.estimated_price DESC
    `).all(cardId) as { condition: string; finish: string; estimated_price: number | null; estimated_liquid_value: number | null }[];
  }

  markAsChecked(skuId: number, calculationDate: string): boolean {
    const result = this.stmts.markChecked.run({ sku_id: skuId, calculation_date: calculationDate });
    return result.changes > 0;
  }

  deleteForSku(skuId: number): number {
    return this.stmts.deleteForSku.run(skuId).changes;
  }

  deleteOlderThan(date: string): number {
    return this.stmts.deleteOlderThan.run(date).changes;
  }
}
