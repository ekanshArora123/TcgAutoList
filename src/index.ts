/**
 * TcgAutoList — Entry point.
 *
 * Initializes all components and starts the listing workflow:
 * 1. Load environment variables
 * 2. Initialize SQLite DB and card-server services
 * 3. Start Telegram bot
 * 4. Start orchestrator event loop
 * 5. Handle graceful shutdown
 */

import 'dotenv/config';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { initDatabase, closeDatabase, getDb } from '../card-server/src/db.js';
import { CollectionService } from '../card-server/src/services/collectionService.js';
import { PricingService } from '../card-server/src/services/pricingService.js';
import { Bot } from './telegram/bot.js';
import { Orchestrator } from './agent/orchestrator.js';

const __dirname = dirname(fileURLToPath(import.meta.url));

// ─── Environment validation ─────────────────────────────────

function requireEnv(name: string): string {
  const val = process.env[name];
  if (!val) {
    console.error(`Missing required environment variable: ${name}`);
    process.exit(1);
  }
  return val;
}

// ─── Main ────────────────────────────────────────────────────

async function main() {
  console.log('TcgAutoList starting...');

  // Load config
  const telegramToken = requireEnv('TELEGRAM_BOT_TOKEN');
  const telegramChatId = parseInt(requireEnv('TELEGRAM_CHAT_ID'), 10);
  const projectRoot = join(__dirname, '..');
  const dbPath = process.env.DB_PATH ?? join(projectRoot, 'card-server', 'data', 'cards.db');
  const photosDir = process.env.PHOTOS_DIR ?? join(projectRoot, 'photos');

  if (isNaN(telegramChatId)) {
    console.error('TELEGRAM_CHAT_ID must be a number');
    process.exit(1);
  }

  // Initialize database
  console.log(`Database: ${dbPath}`);
  initDatabase(dbPath);
  const db = getDb();

  // Initialize services (direct import, no MCP overhead for Tier 1)
  const collectionService = new CollectionService(db);
  const pricingService = new PricingService(db);

  // Print collection stats
  const stats = collectionService.getStats();
  console.log(`Collection: ${stats.total} cards (${JSON.stringify(stats.by_status)})`);

  // Initialize Telegram bot
  console.log('Starting Telegram bot...');
  const bot = new Bot({
    token: telegramToken,
    chatId: telegramChatId,
    photosDir,
    projectRoot,
  });

  // Initialize orchestrator
  const orchestrator = new Orchestrator({
    bot,
    collectionService,
    pricingService,
  });

  orchestrator.start();
  try {
    await bot.sendMessage('TcgAutoList is online. Send /next to start listing cards.');
    console.log('Startup message sent to Telegram.');
  } catch (err: any) {
    console.error('Failed to send startup message:', err.message);
    console.error('Check your TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.');
  }

  // ─── Graceful shutdown ─────────────────────────────────────

  const shutdown = async (signal: string) => {
    console.log(`\nReceived ${signal}. Shutting down...`);
    await bot.stop();
    closeDatabase();
    console.log('Goodbye.');
    process.exit(0);
  };

  process.on('SIGINT', () => shutdown('SIGINT'));
  process.on('SIGTERM', () => shutdown('SIGTERM'));
}

main().catch(err => {
  console.error('Fatal error:', err);
  process.exit(1);
});
