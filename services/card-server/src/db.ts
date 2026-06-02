import Database from 'better-sqlite3';
import { readFileSync, mkdirSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));

let db: Database.Database;

export function initDatabase(dbPath?: string): Database.Database {
  const resolvedPath = dbPath ?? join(__dirname, '..', 'data', 'cards.db');
  mkdirSync(dirname(resolvedPath), { recursive: true });

  db = new Database(resolvedPath);
  db.pragma('journal_mode = WAL');
  db.pragma('foreign_keys = ON');

  const schema = readFileSync(join(__dirname, 'schema.sql'), 'utf-8');
  db.exec(schema);

  // Migrations: add columns to existing tables (safe to re-run)
  const migrations = [
    'ALTER TABLE inventory ADD COLUMN front_photo_path TEXT',
    'ALTER TABLE inventory ADD COLUMN back_photo_path TEXT',
  ];
  for (const sql of migrations) {
    try { db.exec(sql); } catch { /* column already exists */ }
  }

  return db;
}

export function getDb(): Database.Database {
  if (!db) throw new Error('Database not initialized. Call initDatabase() first.');
  return db;
}

export function closeDatabase(): void {
  if (db) {
    db.close();
  }
}
