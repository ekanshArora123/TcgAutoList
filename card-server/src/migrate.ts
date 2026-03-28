/**
 * Migration script: MySQL dump → SQLite
 *
 * Reads the UTF-16LE MySQL dump and populates the new SQLite schema.
 * Mapping:
 *   cardinfo        → cards
 *   skutable        → skus  (hashed SKU → auto-increment ID)
 *   actualinventory → inventory  (hashed SKU FK → sku_id FK)
 *   cardprices      → prices (hashed SKU PK → sku_id PK)
 *
 * Run: cd card-server && npx tsx src/migrate.ts [--dump-path <path>] [--db-path <path>]
 */

import { readFileSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { initDatabase, closeDatabase, getDb } from './db.js';

const __dirname = dirname(fileURLToPath(import.meta.url));

// ─── CLI args ────────────────────────────────────────────────

function getArg(name: string, fallback: string): string {
  const idx = process.argv.indexOf(`--${name}`);
  return idx !== -1 && process.argv[idx + 1] ? process.argv[idx + 1] : fallback;
}

const dumpPath = getArg('dump-path', join(__dirname, '..', '..', 'Old implementation files', 'database-dump.sql'));
const dbPath = getArg('db-path', join(__dirname, '..', 'data', 'cards.db'));

// ─── SQL Parsing ─────────────────────────────────────────────

/**
 * Parse a MySQL INSERT INTO statement and extract rows of values.
 * Handles: strings with escaped quotes, NULL, numbers, dates.
 */
function parseInsertValues(line: string): string[][] {
  // Find the VALUES portion
  const valuesIdx = line.indexOf('VALUES ');
  if (valuesIdx === -1) return [];
  const valuesPart = line.slice(valuesIdx + 7);

  const rows: string[][] = [];
  let current: string[] = [];
  let i = 0;
  let inValue = false;

  while (i < valuesPart.length) {
    const ch = valuesPart[i];

    if (ch === '(') {
      current = [];
      inValue = true;
      i++;
      continue;
    }

    if (ch === ')' && inValue) {
      rows.push(current);
      inValue = false;
      i++;
      continue;
    }

    if (!inValue) {
      i++;
      continue;
    }

    // Inside a row — parse next value
    if (ch === "'") {
      // String value — read until unescaped closing quote
      let str = '';
      i++; // skip opening quote
      while (i < valuesPart.length) {
        if (valuesPart[i] === "'" && valuesPart[i + 1] === "'") {
          str += "'";
          i += 2;
        } else if (valuesPart[i] === '\\' && valuesPart[i + 1] === "'") {
          str += "'";
          i += 2;
        } else if (valuesPart[i] === '\\' && valuesPart[i + 1] === '\\') {
          str += '\\';
          i += 2;
        } else if (valuesPart[i] === "'") {
          i++; // skip closing quote
          break;
        } else {
          str += valuesPart[i];
          i++;
        }
      }
      current.push(str);
    } else if (ch === 'N' && valuesPart.slice(i, i + 4) === 'NULL') {
      current.push('NULL');
      i += 4;
    } else if (ch === ',' || ch === ' ') {
      i++;
    } else {
      // Number
      let num = '';
      while (i < valuesPart.length && valuesPart[i] !== ',' && valuesPart[i] !== ')') {
        num += valuesPart[i];
        i++;
      }
      current.push(num.trim());
    }
  }

  return rows;
}

function nullableStr(val: string): string | null {
  return val === 'NULL' || val === '' ? null : val;
}

function nullableNum(val: string): number | null {
  if (val === 'NULL' || val === '') return null;
  const n = parseFloat(val);
  return isNaN(n) ? null : n;
}

function nullableInt(val: string): number | null {
  if (val === 'NULL' || val === '') return null;
  const n = parseInt(val, 10);
  return isNaN(n) ? null : n;
}

// ─── Main ────────────────────────────────────────────────────

async function migrate() {
  console.log('Reading MySQL dump...');
  console.log(`  Dump: ${dumpPath}`);
  console.log(`  DB:   ${dbPath}`);

  const raw = readFileSync(dumpPath);
  // Detect UTF-16LE BOM (FF FE) and decode accordingly
  const content = (raw[0] === 0xFF && raw[1] === 0xFE)
    ? raw.toString('utf16le')
    : raw.toString('utf8');

  const lines = content.split(/\r?\n/);

  // Find INSERT lines for each table
  const cardInfoLine = lines.find(l => l.startsWith('INSERT INTO `cardinfo`'));
  const skuTableLine = lines.find(l => l.startsWith('INSERT INTO `skutable`'));
  const inventoryLines = lines.filter(l => l.startsWith('INSERT INTO `actualinventory`'));
  const priceLines = lines.filter(l => l.startsWith('INSERT INTO `cardprices`'));

  if (!cardInfoLine) throw new Error('No cardinfo INSERT found in dump');
  if (!skuTableLine) throw new Error('No skutable INSERT found in dump');
  if (inventoryLines.length === 0) throw new Error('No actualinventory INSERT found in dump');
  if (priceLines.length === 0) throw new Error('No cardprices INSERT found in dump');

  // Parse all rows
  console.log('Parsing rows...');
  const cardRows = parseInsertValues(cardInfoLine);
  const skuRows = parseInsertValues(skuTableLine);
  const inventoryRows = inventoryLines.flatMap(l => parseInsertValues(l));
  const priceRows = priceLines.flatMap(l => parseInsertValues(l));

  console.log(`  cardinfo:        ${cardRows.length} rows`);
  console.log(`  skutable:        ${skuRows.length} rows`);
  console.log(`  actualinventory: ${inventoryRows.length} rows`);
  console.log(`  cardprices:      ${priceRows.length} rows`);

  // Init DB
  initDatabase(dbPath);
  const db = getDb();

  // ── 1. Migrate cards ──
  console.log('\nMigrating cards...');
  const insertCard = db.prepare(`
    INSERT OR IGNORE INTO cards (id, card_name, set_name, product_line, card_type, visual_layout, rarity, card_number, product_type, era, set_type)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `);

  let cardCount = 0;
  const cardTx = db.transaction(() => {
    for (const row of cardRows) {
      // cardinfo: ID, cardName, setName, productLine, cardType, visualLayout, rarity, cardNumber, productType, era, setType
      const id = row[0];
      if (!id) continue; // skip empty ID row

      insertCard.run(
        id,
        row[1],                    // card_name
        nullableStr(row[2]),       // set_name
        nullableStr(row[3]),       // product_line
        nullableStr(row[4]),       // card_type
        nullableStr(row[5]),       // visual_layout
        nullableStr(row[6]),       // rarity
        nullableStr(row[7]),       // card_number
        nullableStr(row[8]),       // product_type
        nullableStr(row[9]),       // era
        nullableStr(row[10]),      // set_type
      );
      cardCount++;
    }
  });
  cardTx();
  console.log(`  Inserted ${cardCount} cards`);

  // ── 2. Migrate SKUs (build hash→id mapping) ──
  console.log('Migrating SKUs...');
  const insertSku = db.prepare(`
    INSERT OR IGNORE INTO skus (card_id, condition, finish, specialty_one, specialty_two, qty, latest_calc_date)
    VALUES (?, ?, ?, ?, ?, ?, ?)
  `);
  const getSkuId = db.prepare(`
    SELECT sku_id FROM skus WHERE card_id = ? AND condition = ? AND finish = ? AND specialty_one = ? AND specialty_two = ?
  `);

  // Map: old hashed SKU → new auto-increment sku_id
  const skuHashToId = new Map<string, number>();
  let skuCount = 0;
  let skuSkipped = 0;

  const skuTx = db.transaction(() => {
    for (const row of skuRows) {
      // skutable: ID, cardCondition, cardFinish, specialtyOne, specialtyTwo, skuString, hashedSku, qty, latestCalcDate
      const cardId = row[0];
      const condition = row[1];
      const finish = row[2];
      const specialtyOne = row[3];
      const specialtyTwo = row[4];
      // row[5] = skuString (not needed)
      const hashedSku = row[6];
      const qty = nullableInt(row[7]) ?? 0;
      const latestCalcDate = nullableStr(row[8]);

      if (!cardId) {
        skuSkipped++;
        continue;
      }

      // Check card exists (FK constraint)
      const cardExists = db.prepare('SELECT 1 FROM cards WHERE id = ?').get(cardId);
      if (!cardExists) {
        skuSkipped++;
        continue;
      }

      insertSku.run(cardId, condition, finish, specialtyOne, specialtyTwo, qty, latestCalcDate);
      const result = getSkuId.get(cardId, condition, finish, specialtyOne, specialtyTwo) as { sku_id: number } | undefined;
      if (result) {
        skuHashToId.set(hashedSku, result.sku_id);
        skuCount++;
      }
    }
  });
  skuTx();
  console.log(`  Inserted ${skuCount} SKUs (skipped ${skuSkipped})`);

  // ── 3. Migrate inventory ──
  console.log('Migrating inventory...');
  const insertInventory = db.prepare(`
    INSERT INTO inventory (sku_id, pricing_sku_id, qty, tags, status)
    VALUES (?, ?, ?, ?, 'unlisted')
  `);

  let invCount = 0;
  let invSkipped = 0;

  const invTx = db.transaction(() => {
    for (const row of inventoryRows) {
      // actualinventory CREATE TABLE column order:
      //   [0] sku (FK to skutable.hashedSku)
      //   [1] tags
      //   [2] individualCardHashed (PK, not used in new schema)
      //   [3] individualCardQty
      //   [4] lotBoughtFrom (not used in new schema)
      //   [5] pricingSku (FK to skutable.hashedSku)
      const skuHash = row[0];
      const tags = nullableStr(row[1]);
      const qty = nullableInt(row[3]) ?? 1;
      const pricingSkuHash = nullableStr(row[5]);

      const skuId = skuHashToId.get(skuHash);
      if (!skuId) {
        invSkipped++;
        continue;
      }

      // If pricingSku is the same as sku, it's not an override — store null
      const pricingSkuId = (pricingSkuHash && pricingSkuHash !== skuHash)
        ? (skuHashToId.get(pricingSkuHash) ?? null)
        : null;

      insertInventory.run(skuId, pricingSkuId, qty, tags);
      invCount++;
    }
  });
  invTx();
  console.log(`  Inserted ${invCount} inventory items (skipped ${invSkipped})`);

  // ── 4. Migrate prices ──
  console.log('Migrating prices...');
  const insertPrice = db.prepare(`
    INSERT OR IGNORE INTO prices (sku_id, calculation_date, estimated_price, estimated_liquid_value, confidence_percent, manual_check_necessary, manually_checked, algorithm_version, estimated_low_price, estimated_high_price, estimated_low_price_liquid, estimated_high_price_liquid)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `);

  let priceCount = 0;
  let priceSkipped = 0;

  const priceTx = db.transaction(() => {
    for (const row of priceRows) {
      // cardprices: skuID, calculationDate, estimatedPrice, estimatedLiquidValue, priceEstimateConfidencePercent, manualCheckNecessary, manuallyCheckedBool, algorithmVersion, estimatedLowPrice, estimatedHighPrice, estimatedLowPriceLiquid, estimatedHighPriceLiquid
      const skuHash = row[0];
      const skuId = skuHashToId.get(skuHash);
      if (!skuId) {
        priceSkipped++;
        continue;
      }

      insertPrice.run(
        skuId,
        row[1],                     // calculation_date
        nullableNum(row[2]),        // estimated_price
        nullableNum(row[3]),        // estimated_liquid_value
        nullableInt(row[4]),        // confidence_percent
        nullableInt(row[5]),        // manual_check_necessary
        nullableInt(row[6]) ?? 0,   // manually_checked
        nullableStr(row[7]),        // algorithm_version
        nullableNum(row[8]),        // estimated_low_price
        nullableNum(row[9]),        // estimated_high_price
        nullableNum(row[10]),       // estimated_low_price_liquid
        nullableNum(row[11]),       // estimated_high_price_liquid
      );
      priceCount++;
    }
  });
  priceTx();
  console.log(`  Inserted ${priceCount} price records (skipped ${priceSkipped})`);

  // ── Summary ──
  console.log('\n═══════════════════════════════════════');
  console.log('Migration complete!');
  console.log(`  Cards:     ${cardCount}`);
  console.log(`  SKUs:      ${skuCount}`);
  console.log(`  Inventory: ${invCount}`);
  console.log(`  Prices:    ${priceCount}`);
  console.log(`  DB:        ${dbPath}`);
  console.log('═══════════════════════════════════════');

  closeDatabase();
}

migrate().catch(err => {
  console.error('Migration failed:', err);
  process.exit(1);
});
