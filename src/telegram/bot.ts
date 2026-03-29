/**
 * Telegram Bot wrapper.
 *
 * Initializes the bot, routes incoming messages to the appropriate handlers,
 * and exposes methods for the orchestrator to send messages back to the user.
 *
 * This is a TRANSPORT LAYER — it forwards events to the orchestrator and
 * sends orchestrator output back to the user. It is NOT an MCP server.
 */

import TelegramBot from 'node-telegram-bot-api';
import { EventEmitter } from 'events';
import { mkdirSync, existsSync } from 'fs';
import { join } from 'path';
import { writeFile } from 'fs/promises';
import type { TelegramEvent, PhotoSide } from '../types.js';
import type { InventoryDetail } from '../types.js';

export interface BotConfig {
  token: string;
  chatId: number;
  photosDir: string;
}

export class Bot extends EventEmitter {
  private bot: TelegramBot;
  private botToken: string;
  private chatId: number;
  private photosDir: string;
  /** Tracks which photo side we're expecting next per inventory item. */
  private expectedPhoto: { inventoryId: number; side: PhotoSide } | null = null;

  constructor(config: BotConfig) {
    super();
    this.botToken = config.token;
    this.chatId = config.chatId;
    this.photosDir = config.photosDir;
    mkdirSync(this.photosDir, { recursive: true });

    this.bot = new TelegramBot(config.token, { polling: true });
    console.log(`Telegram bot polling started (chatId: ${this.chatId})`);
    this.setupHandlers();
  }

  private setupHandlers(): void {
    // Command handler
    this.bot.onText(/^\/(next|status|skip|pause)(?:@\S+)?$/, (msg, match) => {
      console.log(`Received command from chat ${msg.chat.id}: ${msg.text}`);
      if (!this.isAuthorized(msg.chat.id)) {
        console.log(`Unauthorized: expected ${this.chatId}, got ${msg.chat.id}`);
        return;
      }
      const command = match![1] as TelegramEvent & { type: 'command' } extends { command: infer C } ? C : never;
      this.emit('event', {
        type: 'command',
        command: command as 'next' | 'status' | 'skip' | 'pause',
        chatId: msg.chat.id,
      } satisfies TelegramEvent);
    });

    // Photo handler
    this.bot.on('photo', async (msg) => {
      if (!this.isAuthorized(msg.chat.id)) return;

      // Get the highest resolution photo
      const photos = msg.photo!;
      const largest = photos[photos.length - 1];
      const fileId = largest.file_id;

      try {
        const filePath = await this.downloadPhoto(fileId);
        this.emit('event', {
          type: 'photo',
          filePath,
          chatId: msg.chat.id,
        } satisfies TelegramEvent);
      } catch (err) {
        console.error('Failed to download photo:', err);
        await this.sendMessage('Failed to download photo. Please try again.');
      }
    });

    // Text handler (for free-text relay in Tier 2/3)
    this.bot.on('text', (msg) => {
      if (!this.isAuthorized(msg.chat.id)) return;
      // Skip commands (already handled by onText)
      if (msg.text?.startsWith('/')) return;

      this.emit('event', {
        type: 'text',
        message: msg.text ?? '',
        chatId: msg.chat.id,
      } satisfies TelegramEvent);
    });

    // Callback query handler (inline keyboard buttons)
    this.bot.on('callback_query', async (query) => {
      if (!query.message || !this.isAuthorized(query.message.chat.id)) return;

      await this.bot.answerCallbackQuery(query.id);
      this.emit('event', {
        type: 'callback',
        data: query.data ?? '',
        chatId: query.message.chat.id,
        messageId: query.message.message_id,
      } satisfies TelegramEvent);
    });

    // Error handling
    this.bot.on('polling_error', (err) => {
      console.error('Telegram polling error:', err.message);
      if ('response' in err && (err as any).response?.body) {
        console.error('Response:', JSON.stringify((err as any).response.body));
      }
    });

    this.bot.on('error', (err) => {
      console.error('Telegram bot error:', err.message);
    });
  }

  private isAuthorized(chatId: number): boolean {
    return chatId === this.chatId;
  }

  /** Download a photo from Telegram and save to disk with a human-readable name. */
  private async downloadPhoto(fileId: string): Promise<string> {
    const file = await this.bot.getFile(fileId);
    if (!file.file_path) throw new Error('No file_path in Telegram response');

    const fileUrl = `https://api.telegram.org/file/bot${this.botToken}/${file.file_path}`;
    const response = await fetch(fileUrl);
    if (!response.ok) throw new Error(`Failed to fetch photo: ${response.status}`);

    const buffer = Buffer.from(await response.arrayBuffer());

    // Build human-readable filename
    const side = this.expectedPhoto?.side ?? 'front';
    const invId = this.expectedPhoto?.inventoryId ?? 'unknown';
    const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
    const filename = `inv${invId}_${side}_${timestamp}.jpg`;
    const savePath = join(this.photosDir, filename);

    await writeFile(savePath, buffer);
    return savePath;
  }

  // ─── Public methods for the orchestrator ───────────────────

  /** Tell the bot which photo to expect next. */
  setExpectedPhoto(inventoryId: number, side: PhotoSide): void {
    this.expectedPhoto = { inventoryId, side };
  }

  /** Clear expected photo state. */
  clearExpectedPhoto(): void {
    this.expectedPhoto = null;
  }

  /** Send a text message. */
  async sendMessage(text: string, parseMode?: 'Markdown' | 'HTML'): Promise<void> {
    await this.bot.sendMessage(this.chatId, text, {
      parse_mode: parseMode,
    });
  }

  /** Send a message with inline keyboard buttons. */
  async sendInlineKeyboard(
    text: string,
    buttons: { text: string; callbackData: string }[][],
    parseMode?: 'Markdown' | 'HTML',
  ): Promise<number> {
    const msg = await this.bot.sendMessage(this.chatId, text, {
      parse_mode: parseMode,
      reply_markup: {
        inline_keyboard: buttons.map(row =>
          row.map(btn => ({ text: btn.text, callback_data: btn.callbackData }))
        ),
      },
    });
    return msg.message_id;
  }

  /** Edit a previously sent message (e.g., to remove inline keyboard after selection). */
  async editMessage(messageId: number, text: string, parseMode?: 'Markdown' | 'HTML'): Promise<void> {
    await this.bot.editMessageText(text, {
      chat_id: this.chatId,
      message_id: messageId,
      parse_mode: parseMode,
    });
  }

  /** Send a photo from a local file path. */
  async sendPhoto(filePath: string, caption?: string, parseMode?: 'Markdown' | 'HTML'): Promise<void> {
    await this.bot.sendPhoto(this.chatId, filePath, {
      caption,
      parse_mode: parseMode,
    });
  }

  /**
   * Get the path to a card's digital image, or null if not available.
   * Images are stored as `data/card-images/{cardId}.webp`.
   */
  getCardImagePath(cardId: string): string | null {
    const imgPath = join(process.cwd(), 'data', 'card-images', `${cardId}.webp`);
    return existsSync(imgPath) ? imgPath : null;
  }

  /** Stop polling and clean up. */
  async stop(): Promise<void> {
    await this.bot.stopPolling();
  }
}
