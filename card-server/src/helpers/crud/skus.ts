import type Database from 'better-sqlite3';
import type { Sku, CreateSku, UpdateSku } from '../../types.js';

export class SkusHelper {
  private stmts: {
    insert: Database.Statement;
    getById: Database.Statement;
    getByComposite: Database.Statement;
    getByCardId: Database.Statement;
    delete: Database.Statement;
    recalcQty: Database.Statement;
    distinctConditions: Database.Statement;
    distinctSpecialties: Database.Statement;
  };

  constructor(private db: Database.Database) {
    this.stmts = {
      insert: db.prepare(`
        INSERT INTO skus (card_id, condition, finish, specialty_one, specialty_two, qty)
        VALUES (@card_id, @condition, @finish, @specialty_one, @specialty_two, @qty)
      `),
      getById: db.prepare('SELECT * FROM skus WHERE sku_id = ?'),
      getByComposite: db.prepare(`
        SELECT * FROM skus
        WHERE card_id = @card_id AND condition = @condition AND finish = @finish
          AND specialty_one = @specialty_one AND specialty_two = @specialty_two
      `),
      getByCardId: db.prepare('SELECT * FROM skus WHERE card_id = ? ORDER BY condition, finish'),
      delete: db.prepare('DELETE FROM skus WHERE sku_id = ?'),
      recalcQty: db.prepare(`
        UPDATE skus SET qty = (SELECT COALESCE(SUM(qty), 0) FROM inventory WHERE sku_id = @sku_id)
        WHERE sku_id = @sku_id
      `),
      distinctConditions: db.prepare('SELECT DISTINCT condition FROM skus ORDER BY condition'),
      distinctSpecialties: db.prepare("SELECT DISTINCT specialty_one FROM skus WHERE specialty_one != 'None' ORDER BY specialty_one"),
    };
  }

  create(input: CreateSku): Sku {
    const result = this.stmts.insert.run(input);
    return this.stmts.getById.get(result.lastInsertRowid) as Sku;
  }

  getOrCreate(input: CreateSku): Sku {
    const existing = this.stmts.getByComposite.get(input) as Sku | undefined;
    if (existing) return existing;
    return this.create(input);
  }

  bulkImport(skus: CreateSku[]): number {
    const tx = this.db.transaction((items: CreateSku[]) => {
      let created = 0;
      for (const sku of items) {
        const existing = this.stmts.getByComposite.get(sku) as Sku | undefined;
        if (!existing) {
          this.stmts.insert.run(sku);
          created++;
        }
      }
      return created;
    });
    return tx(skus);
  }

  getById(skuId: number): Sku | null {
    return (this.stmts.getById.get(skuId) as Sku) ?? null;
  }

  findByCompositeKey(
    cardId: string,
    condition: string,
    finish: string,
    specialtyOne: string,
    specialtyTwo: string,
  ): Sku | null {
    const row = this.stmts.getByComposite.get({
      card_id: cardId,
      condition,
      finish,
      specialty_one: specialtyOne,
      specialty_two: specialtyTwo,
    }) as Sku | undefined;
    return row ?? null;
  }

  getByCardId(cardId: string): Sku[] {
    return this.stmts.getByCardId.all(cardId) as Sku[];
  }

  search(filters: {
    card_id?: string;
    condition?: string;
    finish?: string;
    specialty_one?: string;
    limit?: number;
    offset?: number;
  }): Sku[] {
    const conditions: string[] = [];
    const params: Record<string, unknown> = {};

    if (filters.card_id) {
      conditions.push('card_id = @card_id');
      params.card_id = filters.card_id;
    }
    if (filters.condition) {
      conditions.push('condition = @condition');
      params.condition = filters.condition;
    }
    if (filters.finish) {
      conditions.push('finish = @finish');
      params.finish = filters.finish;
    }
    if (filters.specialty_one) {
      conditions.push('specialty_one = @specialty_one');
      params.specialty_one = filters.specialty_one;
    }

    const where = conditions.length > 0 ? `WHERE ${conditions.join(' AND ')}` : '';
    const limit = filters.limit ?? 50;
    const offset = filters.offset ?? 0;

    return this.db.prepare(`SELECT * FROM skus ${where} ORDER BY card_id, condition LIMIT @limit OFFSET @offset`)
      .all({ ...params, limit, offset }) as Sku[];
  }

  getAllConditions(): string[] {
    const rows = this.stmts.distinctConditions.all() as { condition: string }[];
    return rows.map(r => r.condition);
  }

  getAllSpecialties(): string[] {
    const rows = this.stmts.distinctSpecialties.all() as { specialty_one: string }[];
    return rows.map(r => r.specialty_one);
  }

  update(input: UpdateSku): Sku | null {
    const existing = this.getById(input.sku_id);
    if (!existing) return null;

    const fields: string[] = [];
    const params: Record<string, unknown> = { sku_id: input.sku_id };

    const updatable = ['condition', 'finish', 'specialty_one', 'specialty_two', 'qty', 'latest_calc_date'] as const;
    for (const field of updatable) {
      if (input[field] !== undefined) {
        fields.push(`${field} = @${field}`);
        params[field] = input[field];
      }
    }

    if (fields.length === 0) return existing;

    this.db.prepare(`UPDATE skus SET ${fields.join(', ')} WHERE sku_id = @sku_id`).run(params);
    return this.stmts.getById.get(input.sku_id) as Sku;
  }

  recalculateQty(skuId: number): void {
    this.stmts.recalcQty.run({ sku_id: skuId });
  }

  delete(skuId: number): boolean {
    const result = this.stmts.delete.run(skuId);
    return result.changes > 0;
  }
}
