import type Database from 'better-sqlite3';
import type {
  InventoryItem, CreateInventoryItem, UpdateInventoryItem, InventoryDetail,
} from '../../types.js';
import { SkusHelper } from './skus.js';

export class InventoryHelper {
  private skus: SkusHelper;
  private stmts: {
    insert: Database.Statement;
    getById: Database.Statement;
    getDetailById: Database.Statement;
    getNextUnlisted: Database.Statement;
    markListed: Database.Statement;
    markSold: Database.Statement;
    updateStatus: Database.Statement;
    setPricingSku: Database.Statement;
    delete: Database.Statement;
  };

  constructor(private db: Database.Database) {
    this.skus = new SkusHelper(db);

    this.stmts = {
      insert: db.prepare(`
        INSERT INTO inventory (sku_id, pricing_sku_id, qty, tags, status)
        VALUES (@sku_id, @pricing_sku_id, @qty, @tags, @status)
      `),
      getById: db.prepare('SELECT * FROM inventory WHERE inventory_id = ?'),
      getDetailById: db.prepare(`
        SELECT
          i.*,
          s.condition, s.finish, s.card_id, s.specialty_one, s.specialty_two,
          c.card_name, c.set_name, c.rarity, c.card_number,
          p.estimated_price
        FROM inventory i
        JOIN skus s ON i.sku_id = s.sku_id
        JOIN cards c ON s.card_id = c.id
        LEFT JOIN prices p ON COALESCE(i.pricing_sku_id, i.sku_id) = p.sku_id
          AND p.calculation_date = s.latest_calc_date
        WHERE i.inventory_id = ?
      `),
      getNextUnlisted: db.prepare(`
        SELECT
          i.*,
          s.condition, s.finish, s.card_id, s.specialty_one, s.specialty_two,
          c.card_name, c.set_name, c.rarity, c.card_number,
          p.estimated_price
        FROM inventory i
        JOIN skus s ON i.sku_id = s.sku_id
        JOIN cards c ON s.card_id = c.id
        LEFT JOIN prices p ON COALESCE(i.pricing_sku_id, i.sku_id) = p.sku_id
          AND p.calculation_date = s.latest_calc_date
        WHERE i.status = 'unlisted'
        ORDER BY i.created_at ASC
        LIMIT 1
      `),
      markListed: db.prepare(`
        UPDATE inventory SET status = 'listed', ebay_listing_id = @ebay_listing_id, listed_at = datetime('now')
        WHERE inventory_id = @inventory_id
      `),
      markSold: db.prepare("UPDATE inventory SET status = 'sold' WHERE inventory_id = ?"),
      updateStatus: db.prepare('UPDATE inventory SET status = @status WHERE inventory_id = @inventory_id'),
      setPricingSku: db.prepare('UPDATE inventory SET pricing_sku_id = @pricing_sku_id WHERE inventory_id = @inventory_id'),
      delete: db.prepare('DELETE FROM inventory WHERE inventory_id = ?'),
    };
  }

  create(input: CreateInventoryItem): InventoryItem {
    const result = this.stmts.insert.run({
      sku_id: input.sku_id,
      pricing_sku_id: input.pricing_sku_id ?? null,
      qty: input.qty ?? 1,
      tags: input.tags ?? null,
      status: input.status ?? 'unlisted',
    });
    this.skus.recalculateQty(input.sku_id);
    return this.stmts.getById.get(result.lastInsertRowid) as InventoryItem;
  }

  bulkCreate(items: CreateInventoryItem[]): number {
    const tx = this.db.transaction((list: CreateInventoryItem[]) => {
      const affectedSkus = new Set<number>();
      let count = 0;
      for (const item of list) {
        this.stmts.insert.run({
          sku_id: item.sku_id,
          pricing_sku_id: item.pricing_sku_id ?? null,
          qty: item.qty ?? 1,
          tags: item.tags ?? null,
          status: item.status ?? 'unlisted',
        });
        affectedSkus.add(item.sku_id);
        count++;
      }
      for (const skuId of affectedSkus) {
        this.skus.recalculateQty(skuId);
      }
      return count;
    });
    return tx(items);
  }

  getById(inventoryId: number): InventoryItem | null {
    return (this.stmts.getById.get(inventoryId) as InventoryItem) ?? null;
  }

  getDetailById(inventoryId: number): InventoryDetail | null {
    return (this.stmts.getDetailById.get(inventoryId) as InventoryDetail) ?? null;
  }

  getNextUnlisted(): InventoryDetail | null {
    return (this.stmts.getNextUnlisted.get() as InventoryDetail) ?? null;
  }

  search(filters: {
    sku_id?: number;
    status?: string;
    tags_contain?: string;
    card_id?: string;
    limit?: number;
    offset?: number;
  }): InventoryItem[] {
    const conditions: string[] = [];
    const params: Record<string, unknown> = {};

    if (filters.sku_id !== undefined) {
      conditions.push('i.sku_id = @sku_id');
      params.sku_id = filters.sku_id;
    }
    if (filters.status) {
      conditions.push('i.status = @status');
      params.status = filters.status;
    }
    if (filters.tags_contain) {
      conditions.push('i.tags LIKE @tags_contain');
      params.tags_contain = `%${filters.tags_contain}%`;
    }
    if (filters.card_id) {
      conditions.push('s.card_id = @card_id');
      params.card_id = filters.card_id;
    }

    const needsJoin = !!filters.card_id;
    const from = needsJoin
      ? 'inventory i JOIN skus s ON i.sku_id = s.sku_id'
      : 'inventory i';
    const where = conditions.length > 0 ? `WHERE ${conditions.join(' AND ')}` : '';
    const limit = filters.limit ?? 50;
    const offset = filters.offset ?? 0;

    return this.db.prepare(`SELECT i.* FROM ${from} ${where} ORDER BY i.created_at ASC LIMIT @limit OFFSET @offset`)
      .all({ ...params, limit, offset }) as InventoryItem[];
  }

  searchWithDetails(filters: {
    status?: string;
    card_id?: string;
    limit?: number;
    offset?: number;
  }): InventoryDetail[] {
    const conditions: string[] = [];
    const params: Record<string, unknown> = {};

    if (filters.status) {
      conditions.push('i.status = @status');
      params.status = filters.status;
    }
    if (filters.card_id) {
      conditions.push('s.card_id = @card_id');
      params.card_id = filters.card_id;
    }

    const where = conditions.length > 0 ? `WHERE ${conditions.join(' AND ')}` : '';
    const limit = filters.limit ?? 50;
    const offset = filters.offset ?? 0;

    return this.db.prepare(`
      SELECT
        i.*,
        s.condition, s.finish, s.card_id, s.specialty_one, s.specialty_two,
        c.card_name, c.set_name, c.rarity, c.card_number,
        p.estimated_price
      FROM inventory i
      JOIN skus s ON i.sku_id = s.sku_id
      JOIN cards c ON s.card_id = c.id
      LEFT JOIN prices p ON COALESCE(i.pricing_sku_id, i.sku_id) = p.sku_id
        AND p.calculation_date = s.latest_calc_date
      ${where}
      ORDER BY i.created_at ASC
      LIMIT @limit OFFSET @offset
    `).all({ ...params, limit, offset }) as InventoryDetail[];
  }

  getStats(): { total: number; by_status: Record<string, number>; total_estimated_value: number | null } {
    const statusRows = this.db.prepare(
      "SELECT status, COUNT(*) as count FROM inventory GROUP BY status"
    ).all() as { status: string; count: number }[];

    const byStatus: Record<string, number> = {};
    let total = 0;
    for (const row of statusRows) {
      byStatus[row.status] = row.count;
      total += row.count;
    }

    const valueRow = this.db.prepare(`
      SELECT SUM(p.estimated_price * i.qty) as total_value
      FROM inventory i
      JOIN skus s ON i.sku_id = s.sku_id
      LEFT JOIN prices p ON COALESCE(i.pricing_sku_id, i.sku_id) = p.sku_id
        AND p.calculation_date = s.latest_calc_date
    `).get() as { total_value: number | null };

    return {
      total,
      by_status: byStatus,
      total_estimated_value: valueRow.total_value,
    };
  }

  update(input: UpdateInventoryItem): InventoryItem | null {
    const existing = this.getById(input.inventory_id);
    if (!existing) return null;

    const fields: string[] = [];
    const params: Record<string, unknown> = { inventory_id: input.inventory_id };
    const oldSkuId = existing.sku_id;

    const updatable = ['sku_id', 'pricing_sku_id', 'qty', 'tags', 'status', 'ebay_listing_id', 'listed_at'] as const;
    for (const field of updatable) {
      if (input[field] !== undefined) {
        fields.push(`${field} = @${field}`);
        params[field] = input[field];
      }
    }

    if (fields.length === 0) return existing;

    this.db.prepare(`UPDATE inventory SET ${fields.join(', ')} WHERE inventory_id = @inventory_id`).run(params);

    if (input.sku_id !== undefined && input.sku_id !== oldSkuId) {
      this.skus.recalculateQty(oldSkuId);
      this.skus.recalculateQty(input.sku_id);
    }

    return this.stmts.getById.get(input.inventory_id) as InventoryItem;
  }

  markAsListed(inventoryId: number, ebayListingId: string): InventoryItem | null {
    this.stmts.markListed.run({ inventory_id: inventoryId, ebay_listing_id: ebayListingId });
    return this.getById(inventoryId);
  }

  markAsSold(inventoryId: number): InventoryItem | null {
    this.stmts.markSold.run(inventoryId);
    return this.getById(inventoryId);
  }

  updateStatus(inventoryId: number, status: string): InventoryItem | null {
    this.stmts.updateStatus.run({ inventory_id: inventoryId, status });
    return this.getById(inventoryId);
  }

  setPricingSku(inventoryId: number, pricingSkuId: number | null): InventoryItem | null {
    this.stmts.setPricingSku.run({ inventory_id: inventoryId, pricing_sku_id: pricingSkuId });
    return this.getById(inventoryId);
  }

  addTag(inventoryId: number, tag: string): InventoryItem | null {
    const item = this.getById(inventoryId);
    if (!item) return null;

    const tags = item.tags ? item.tags.split(',').map(t => t.trim()) : [];
    if (tags.includes(tag)) return item;

    tags.push(tag);
    this.db.prepare('UPDATE inventory SET tags = ? WHERE inventory_id = ?').run(tags.join(','), inventoryId);
    return this.getById(inventoryId);
  }

  removeTag(inventoryId: number, tag: string): InventoryItem | null {
    const item = this.getById(inventoryId);
    if (!item) return null;
    if (!item.tags) return item;

    const tags = item.tags.split(',').map(t => t.trim()).filter(t => t !== tag);
    const newTags = tags.length > 0 ? tags.join(',') : null;
    this.db.prepare('UPDATE inventory SET tags = ? WHERE inventory_id = ?').run(newTags, inventoryId);
    return this.getById(inventoryId);
  }

  delete(inventoryId: number): boolean {
    const item = this.getById(inventoryId);
    if (!item) return false;

    const result = this.stmts.delete.run(inventoryId);
    if (result.changes > 0) {
      this.skus.recalculateQty(item.sku_id);
      return true;
    }
    return false;
  }
}
