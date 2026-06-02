/**
 * Deduplication script: Remove duplicate inventory rows caused by
 * running the migration script multiple times.
 *
 * Strategy:
 * - For each group of duplicates (same sku_id, qty, tags), keep ONE row.
 * - If any row in the group has been modified (status != 'unlisted', has photos,
 *   ebay_listing_id, etc.), keep that one. Otherwise keep the lowest inventory_id.
 * - Delete the rest.
 *
 * Run: cd card-server && npx tsx src/dedup-inventory.ts [--db-path <path>] [--dry-run]
 */

import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { initDatabase, closeDatabase, getDb } from './db.js';

const __dirname = dirname(fileURLToPath(import.meta.url));

function getArg(name: string, fallback: string): string {
  const idx = process.argv.indexOf(`--${name}`);
  return idx !== -1 && process.argv[idx + 1] ? process.argv[idx + 1] : fallback;
}

const dbPath = getArg('db-path', join(__dirname, '..', 'data', 'cards.db'));
const dryRun = process.argv.includes('--dry-run');

async function dedup() {
  console.log(`Deduplicating inventory...`);
  console.log(`  DB: ${dbPath}`);
  console.log(`  Mode: ${dryRun ? 'DRY RUN' : 'LIVE'}`);

  initDatabase(dbPath);
  const db = getDb();

  // Get current state
  const totalBefore = (db.prepare('SELECT COUNT(*) as c FROM inventory').get() as { c: number }).c;
  console.log(`\n  Total inventory rows before: ${totalBefore}`);

  // Find all duplicate groups.
  // Group by sku_id + qty + tags (the fields set during migration).
  // Within each group, pick the "best" row to keep.
  const groups = db.prepare(`
    SELECT sku_id, qty, COALESCE(tags, '') as tags, COUNT(*) as cnt,
           GROUP_CONCAT(inventory_id) as ids
    FROM inventory
    GROUP BY sku_id, qty, COALESCE(tags, '')
    HAVING cnt > 1
    ORDER BY cnt DESC
  `).all() as { sku_id: number; qty: number; tags: string; cnt: number; ids: string }[];

  console.log(`  Duplicate groups: ${groups.length}`);

  const idsToDelete: number[] = [];

  for (const group of groups) {
    const allIds = group.ids.split(',').map(Number);

    // Check if any row in this group has been modified (non-default state)
    const modifiedRows = db.prepare(`
      SELECT inventory_id FROM inventory
      WHERE inventory_id IN (${allIds.join(',')})
        AND (status != 'unlisted'
             OR front_photo_path IS NOT NULL
             OR back_photo_path IS NOT NULL
             OR ebay_listing_id IS NOT NULL)
      ORDER BY inventory_id ASC
    `).all() as { inventory_id: number }[];

    let keepId: number;
    if (modifiedRows.length > 0) {
      // Keep the first modified row
      keepId = modifiedRows[0].inventory_id;
    } else {
      // Keep the lowest ID (original migration row)
      keepId = Math.min(...allIds);
    }

    for (const id of allIds) {
      if (id !== keepId) {
        idsToDelete.push(id);
      }
    }
  }

  console.log(`  Rows to delete: ${idsToDelete.length}`);
  console.log(`  Rows to keep: ${totalBefore - idsToDelete.length}`);

  if (dryRun) {
    console.log('\n  DRY RUN — no changes made. Run without --dry-run to apply.');
  } else {
    // Delete in batches within a transaction
    const batchSize = 500;
    db.exec('BEGIN');
    try {
      for (let i = 0; i < idsToDelete.length; i += batchSize) {
        const batch = idsToDelete.slice(i, i + batchSize);
        db.prepare(`DELETE FROM inventory WHERE inventory_id IN (${batch.join(',')})`).run();
      }
      db.exec('COMMIT');

      const totalAfter = (db.prepare('SELECT COUNT(*) as c FROM inventory').get() as { c: number }).c;
      console.log(`\n  Total inventory rows after: ${totalAfter}`);
      console.log(`  Deleted: ${totalBefore - totalAfter}`);

      // Recalculate SKU quantities
      console.log('  Recalculating SKU quantities...');
      db.prepare(`
        UPDATE skus SET qty = (
          SELECT COALESCE(SUM(qty), 0) FROM inventory WHERE inventory.sku_id = skus.sku_id
        )
      `).run();
      console.log('  Done.');
    } catch (err) {
      db.exec('ROLLBACK');
      throw err;
    }
  }

  // Show status breakdown of remaining rows
  const statusBreakdown = db.prepare(`
    SELECT status, COUNT(*) as cnt FROM inventory GROUP BY status ORDER BY cnt DESC
  `).all() as { status: string; cnt: number }[];
  console.log('\n  Status breakdown (after):');
  for (const row of statusBreakdown) {
    console.log(`    ${row.status}: ${row.cnt}`);
  }

  closeDatabase();
}

dedup().catch(err => {
  console.error('Dedup failed:', err);
  process.exit(1);
});
