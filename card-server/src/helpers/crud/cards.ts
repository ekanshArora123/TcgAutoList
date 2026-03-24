import type Database from 'better-sqlite3';
import type { Card, CreateCard, UpdateCard } from '../../types.js';

export class CardsHelper {
  private stmts: {
    insert: Database.Statement;
    upsert: Database.Statement;
    getById: Database.Statement;
    delete: Database.Statement;
    count: Database.Statement;
    countByLine: Database.Statement;
    distinctSets: Database.Statement;
    distinctRarities: Database.Statement;
  };

  constructor(private db: Database.Database) {
    this.stmts = {
      insert: db.prepare(`
        INSERT INTO cards (id, card_name, set_name, product_line, card_type, visual_layout, rarity, card_number, product_type, era, set_type)
        VALUES (@id, @card_name, @set_name, @product_line, @card_type, @visual_layout, @rarity, @card_number, @product_type, @era, @set_type)
      `),
      upsert: db.prepare(`
        INSERT INTO cards (id, card_name, set_name, product_line, card_type, visual_layout, rarity, card_number, product_type, era, set_type)
        VALUES (@id, @card_name, @set_name, @product_line, @card_type, @visual_layout, @rarity, @card_number, @product_type, @era, @set_type)
        ON CONFLICT(id) DO UPDATE SET
          card_name = excluded.card_name,
          set_name = excluded.set_name,
          product_line = excluded.product_line,
          card_type = excluded.card_type,
          visual_layout = excluded.visual_layout,
          rarity = excluded.rarity,
          card_number = excluded.card_number,
          product_type = excluded.product_type,
          era = excluded.era,
          set_type = excluded.set_type
      `),
      getById: db.prepare('SELECT * FROM cards WHERE id = ?'),
      delete: db.prepare('DELETE FROM cards WHERE id = ?'),
      count: db.prepare('SELECT COUNT(*) as count FROM cards'),
      countByLine: db.prepare('SELECT COUNT(*) as count FROM cards WHERE product_line = ?'),
      distinctSets: db.prepare('SELECT DISTINCT set_name FROM cards WHERE set_name IS NOT NULL ORDER BY set_name'),
      distinctRarities: db.prepare('SELECT DISTINCT rarity FROM cards WHERE rarity IS NOT NULL ORDER BY rarity'),
    };
  }

  create(input: CreateCard): Card {
    this.stmts.insert.run(input);
    return this.stmts.getById.get(input.id) as Card;
  }

  upsert(input: CreateCard): Card {
    this.stmts.upsert.run(input);
    return this.stmts.getById.get(input.id) as Card;
  }

  bulkUpsert(cards: CreateCard[]): number {
    const tx = this.db.transaction((items: CreateCard[]) => {
      let count = 0;
      for (const card of items) {
        this.stmts.upsert.run(card);
        count++;
      }
      return count;
    });
    return tx(cards);
  }

  getById(id: string): Card | null {
    return (this.stmts.getById.get(id) as Card) ?? null;
  }

  search(filters: {
    query?: string;
    set_name?: string;
    rarity?: string;
    product_line?: string;
    limit?: number;
    offset?: number;
  }): Card[] {
    const conditions: string[] = [];
    const params: Record<string, unknown> = {};

    if (filters.query) {
      conditions.push("card_name LIKE @query");
      params.query = `%${filters.query}%`;
    }
    if (filters.set_name) {
      conditions.push("set_name = @set_name");
      params.set_name = filters.set_name;
    }
    if (filters.rarity) {
      conditions.push("rarity = @rarity");
      params.rarity = filters.rarity;
    }
    if (filters.product_line) {
      conditions.push("product_line = @product_line");
      params.product_line = filters.product_line;
    }

    const where = conditions.length > 0 ? `WHERE ${conditions.join(' AND ')}` : '';
    const limit = filters.limit ?? 50;
    const offset = filters.offset ?? 0;

    const stmt = this.db.prepare(`SELECT * FROM cards ${where} ORDER BY card_name LIMIT @limit OFFSET @offset`);
    return stmt.all({ ...params, limit, offset }) as Card[];
  }

  getAllSetNames(): string[] {
    const rows = this.stmts.distinctSets.all() as { set_name: string }[];
    return rows.map(r => r.set_name);
  }

  getAllRarities(): string[] {
    const rows = this.stmts.distinctRarities.all() as { rarity: string }[];
    return rows.map(r => r.rarity);
  }

  count(productLine?: string): number {
    if (productLine) {
      return (this.stmts.countByLine.get(productLine) as { count: number }).count;
    }
    return (this.stmts.count.get() as { count: number }).count;
  }

  update(input: UpdateCard): Card | null {
    const existing = this.getById(input.id);
    if (!existing) return null;

    const fields: string[] = [];
    const params: Record<string, unknown> = { id: input.id };

    const updatable = ['card_name', 'set_name', 'product_line', 'card_type', 'visual_layout', 'rarity', 'card_number', 'product_type', 'era', 'set_type'] as const;
    for (const field of updatable) {
      if (input[field] !== undefined) {
        fields.push(`${field} = @${field}`);
        params[field] = input[field];
      }
    }

    if (fields.length === 0) return existing;

    this.db.prepare(`UPDATE cards SET ${fields.join(', ')} WHERE id = @id`).run(params);
    return this.stmts.getById.get(input.id) as Card;
  }

  delete(id: string): boolean {
    const result = this.stmts.delete.run(id);
    return result.changes > 0;
  }
}
