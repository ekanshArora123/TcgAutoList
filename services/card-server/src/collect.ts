/**
 * Market Data Collection Runner
 *
 * Standalone script that collects market data for owned cards.
 * Run manually or via Windows Task Scheduler on a schedule.
 *
 * Usage:
 *   npx tsx src/collect.ts                  # collect all owned cards
 *   npx tsx src/collect.ts --stale          # only cards not collected in 7+ days
 *   npx tsx src/collect.ts --stale 14       # only cards not collected in 14+ days
 *   npx tsx src/collect.ts --cohort         # same-set neighbors (not owned)
 *   npx tsx src/collect.ts --cards 123,456  # specific card IDs
 *   npx tsx src/collect.ts --delay 1000     # 1s between API calls (default 500ms)
 *
 * Environment:
 *   DB_PATH                 — SQLite database path (default: card-server/data/cards.db)
 *   TCGPLAYER_AUTH_COOKIE   — Optional, enables full sold data pagination
 */

import { initDatabase, closeDatabase } from './db.js';
import { MarketCollector } from './helpers/market/index.js';

// ─── CLI Args ───────────────────────────────────────────────

interface CliArgs {
  mode: 'owned' | 'stale' | 'cohort' | 'cards';
  staleDays: number;
  cardIds: string[];
  delayMs: number;
}

function parseArgs(): CliArgs {
  const args = process.argv.slice(2);
  const result: CliArgs = {
    mode: 'owned',
    staleDays: 7,
    cardIds: [],
    delayMs: 500,
  };

  for (let i = 0; i < args.length; i++) {
    switch (args[i]) {
      case '--stale':
        result.mode = 'stale';
        if (args[i + 1] && !args[i + 1].startsWith('--')) {
          result.staleDays = parseInt(args[++i], 10);
        }
        break;
      case '--cohort':
        result.mode = 'cohort';
        break;
      case '--cards':
        result.mode = 'cards';
        if (args[i + 1]) {
          result.cardIds = args[++i].split(',').map(s => s.trim()).filter(Boolean);
        }
        break;
      case '--delay':
        if (args[i + 1]) {
          result.delayMs = parseInt(args[++i], 10);
        }
        break;
    }
  }

  return result;
}

// ─── Main ───────────────────────────────────────────────────

async function main() {
  const args = parseArgs();
  const db = initDatabase(process.env.DB_PATH);
  const collector = new MarketCollector(db);

  console.log(`Market data collection starting (mode: ${args.mode}, delay: ${args.delayMs}ms)`);
  console.log(`Date: ${new Date().toISOString().split('T')[0]}\n`);

  try {
    let report;

    switch (args.mode) {
      case 'owned':
        report = await collector.collectOwned({ delayMs: args.delayMs });
        break;
      case 'stale':
        console.log(`Collecting cards with snapshots older than ${args.staleDays} days\n`);
        report = await collector.collectStale(args.staleDays, { delayMs: args.delayMs });
        break;
      case 'cohort':
        report = await collector.collectCohort({ delayMs: args.delayMs });
        break;
      case 'cards':
        if (args.cardIds.length === 0) {
          console.error('Error: --cards requires comma-separated card IDs');
          process.exit(1);
        }
        report = await collector.collectCards(args.cardIds, { delayMs: args.delayMs });
        break;
    }

    if (report.errors.length > 0) {
      console.log('\nErrors:');
      for (const err of report.errors) {
        console.log(`  ${err.card_id}: ${err.error}`);
      }
    }

    process.exit(report.errors.length > 0 ? 1 : 0);
  } catch (err) {
    console.error('Fatal error:', err);
    process.exit(2);
  } finally {
    closeDatabase();
  }
}

main();
