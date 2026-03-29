/**
 * Orchestrator — Coded event loop that drives the listing workflow.
 *
 * NOT an LLM. This is pure TypeScript that:
 * 1. Listens for /next from Telegram
 * 2. Gets next unlisted card from DB
 * 3. Fetches/refreshes price
 * 4. Routes to appropriate tier
 * 5. Tier 1: requests photo → builds listing → posts to eBay
 * 6. Tier 2/3: spawns LLM conversation (pseudocode for now)
 * 7. Marks card as done, waits for next /next
 *
 * Context resets per card. All durable state is in SQLite.
 */

import type { TelegramEvent, CardWorkflowState, Price, InventoryDetail } from '../types.js';
import type { Bot } from '../telegram/bot.js';
import type { CollectionService } from '../../card-server/src/services/collectionService.js';
import type { PricingService } from '../../card-server/src/services/pricingService.js';
import { routeToTier } from './tierRouter.js';
import { buildListingTemplate, postToEbay } from './tier1Pipeline.js';
import {
  renderCardSummary, renderPhotoRequest, renderListingConfirmation,
  renderStatus, buildPhotoRequestKeyboard, buildReviewKeyboard,
} from '../telegram/renderer.js';

export interface OrchestratorDeps {
  bot: Bot;
  collectionService: CollectionService;
  pricingService: PricingService;
}

export class Orchestrator {
  private bot: Bot;
  private collection: CollectionService;
  private pricing: PricingService;
  private state: CardWorkflowState | null = null;
  private paused = false;

  constructor(deps: OrchestratorDeps) {
    this.bot = deps.bot;
    this.collection = deps.collectionService;
    this.pricing = deps.pricingService;
  }

  /** Start listening for Telegram events. */
  start(): void {
    this.bot.on('event', (event: TelegramEvent) => {
      this.handleEvent(event).catch(err => {
        console.error('Orchestrator error handling event:', err);
        this.bot.sendMessage(`Error: ${err.message}`).catch(() => {});
      });
    });
    console.log('Orchestrator started. Waiting for /next...');
  }

  /** Main event dispatcher. */
  private async handleEvent(event: TelegramEvent): Promise<void> {
    switch (event.type) {
      case 'command':
        await this.handleCommand(event.command);
        break;
      case 'photo':
        await this.handlePhoto(event.filePath);
        break;
      case 'text':
        await this.handleText(event.message);
        break;
      case 'callback':
        await this.handleCallback(event.data, event.messageId);
        break;
    }
  }

  // ─── Command Handlers ──────────────────────────────────────

  private async handleCommand(command: string): Promise<void> {
    switch (command) {
      case 'next':
        await this.handleNext();
        break;
      case 'status':
        await this.handleStatus();
        break;
      case 'skip':
        await this.handleSkip();
        break;
      case 'pause':
        await this.handlePause();
        break;
    }
  }

  /** /next — Pick next unlisted card and start the workflow. */
  private async handleNext(): Promise<void> {
    if (this.paused) {
      await this.bot.sendMessage('Bot is paused. Send /pause to resume.');
      return;
    }

    if (this.state && this.state.step !== 'idle' && this.state.step !== 'done') {
      await this.bot.sendMessage(
        `Already processing a card (step: ${this.state.step}). Send /skip to skip it.`
      );
      return;
    }

    // Get next unlisted card
    const detail = this.collection.getNextUnlisted();
    if (!detail) {
      await this.bot.sendMessage('No more unlisted cards! All done.');
      return;
    }

    // Initialize workflow state
    this.state = {
      inventoryId: detail.inventory_id,
      inventoryDetail: detail,
      step: 'pricing',
      tier: null,
      price: null,
      photoPath: null,
      backPhotoPath: null,
      awaitingSide: null,
      confidence: null,
    };

    // Check for existing price in DB first (fast, no network)
    let price: Price | null = this.pricing.getLatestPriceForItem(detail.inventory_id);

    // If no price or stale (older than 14 days), fetch fresh from TCGplayer
    const isStale = price && this.isPriceStale(price.calculation_date);
    if (!price || isStale) {
      await this.bot.sendMessage('Fetching price...');
      const cardId = detail.card_id;
      try {
        const result = await this.pricing.computeAndStorePrice(
          cardId,
          detail.condition,
          detail.finish,
        );
        if (result) price = result;
      } catch (err) {
        // Keep existing price if fetch fails
        if (!price) {
          await this.bot.sendMessage('Failed to fetch price. No existing price in DB.');
        }
      }
    }

    this.state.price = price;
    this.state.confidence = price?.confidence_percent ?? 0;

    // Get specialty info for tier routing (now in InventoryDetail)
    const specialtyTwo = detail.specialty_two ?? 'None';

    // Route to tier
    this.state.step = 'tier_routing';
    const tierResult = routeToTier(price, specialtyTwo);
    this.state.tier = tierResult.tier;

    // Send card summary to user (with digital image if available)
    const summary = renderCardSummary(detail, price);
    const summaryText = `${summary}\n\nTier: ${tierResult.tier} — ${tierResult.reason}`;
    const cardImagePath = this.bot.getCardImagePath(detail.card_id);

    if (cardImagePath) {
      await this.bot.sendPhoto(cardImagePath, summaryText, 'Markdown');
    } else {
      await this.bot.sendMessage(summaryText, 'Markdown');
    }

    // Execute the appropriate tier
    switch (tierResult.tier) {
      case 1:
        await this.executeTier1();
        break;
      case 2:
        await this.executeTier2Stub();
        break;
      case 3:
        await this.executeTier3Stub();
        break;
    }
  }

  /** Execute Tier 1: template listing, no LLM. */
  private async executeTier1(): Promise<void> {
    if (!this.state) return;

    // Request front photo first
    this.state.step = 'awaiting_photo';
    this.state.awaitingSide = 'front';
    this.bot.setExpectedPhoto(this.state.inventoryId, 'front');

    const photoMsg = renderPhotoRequest(this.state.inventoryDetail, 'front');
    await this.bot.sendInlineKeyboard(
      photoMsg,
      buildPhotoRequestKeyboard(this.state.inventoryId),
      'Markdown',
    );

    // Update DB status
    this.collection.updateStatus(this.state.inventoryId, 'photo_requested');
  }

  /** Handle received photo — continue the workflow. */
  private async handlePhoto(filePath: string): Promise<void> {
    if (!this.state || this.state.step !== 'awaiting_photo') {
      await this.bot.sendMessage('Not expecting a photo right now.');
      return;
    }

    if (this.state.awaitingSide === 'front') {
      // Front photo received — now request back
      this.state.photoPath = filePath;
      this.state.awaitingSide = 'back';
      this.bot.setExpectedPhoto(this.state.inventoryId, 'back');

      const photoMsg = renderPhotoRequest(this.state.inventoryDetail, 'back');
      await this.bot.sendInlineKeyboard(
        photoMsg,
        buildPhotoRequestKeyboard(this.state.inventoryId),
        'Markdown',
      );
      return;
    }

    // Back photo received — proceed with listing
    this.state.backPhotoPath = filePath;
    this.bot.clearExpectedPhoto();
    this.state.awaitingSide = null;

    this.state.step = 'building_listing';
    await this.bot.sendMessage('Photos received. Building listing...');

    if (this.state.tier === 1 && this.state.price && this.state.price.estimated_price !== null) {
      const photoPaths = [this.state.photoPath!, this.state.backPhotoPath!];

      // Save photo paths to DB
      this.collection.setPhotos(this.state.inventoryId, photoPaths[0], photoPaths[1]);

      const listing = buildListingTemplate(
        this.state.inventoryDetail,
        this.state.price,
        photoPaths,
      );

      this.state.step = 'posting';
      const ebayListingId = await postToEbay(listing);

      // Mark as listed in DB
      this.collection.markAsListed(this.state.inventoryId, ebayListingId);

      const confirmation = renderListingConfirmation(
        this.state.inventoryDetail,
        listing.price,
      );
      await this.bot.sendMessage(confirmation, 'Markdown');

      this.state.step = 'done';
      await this.bot.sendMessage('Send /next for the next card.');
    }
  }

  /** /status — Show current state and collection stats. */
  private async handleStatus(): Promise<void> {
    const stats = this.collection.getStats();
    const current = this.state?.inventoryDetail ?? null;
    const statusMsg = renderStatus(current, stats);
    await this.bot.sendMessage(statusMsg, 'Markdown');

    if (this.paused) {
      await this.bot.sendMessage('Bot is currently *paused*. Send /pause to resume.', 'Markdown');
    }
  }

  /** /skip — Skip the current card. */
  private async handleSkip(): Promise<void> {
    if (!this.state || this.state.step === 'idle' || this.state.step === 'done') {
      await this.bot.sendMessage('No card to skip.');
      return;
    }

    this.collection.updateStatus(this.state.inventoryId, 'skipped');
    this.bot.clearExpectedPhoto();
    await this.bot.sendMessage(
      `Skipped: ${this.state.inventoryDetail.card_name}. Send /next for the next card.`
    );
    this.state.step = 'done';
  }

  /** /pause — Toggle pause. */
  private async handlePause(): Promise<void> {
    this.paused = !this.paused;
    await this.bot.sendMessage(this.paused ? 'Bot paused.' : 'Bot resumed.');
  }

  /** Handle inline keyboard callbacks. */
  private async handleCallback(data: string, messageId: number): Promise<void> {
    const [action, idStr] = data.split(':');
    const inventoryId = parseInt(idStr, 10);

    switch (action) {
      case 'skip':
        if (this.state && this.state.inventoryId === inventoryId) {
          await this.handleSkip();
          await this.bot.editMessage(messageId, 'Skipped.');
        }
        break;
      case 'approve':
        // Used in Tier 2/3 manual review
        await this.bot.editMessage(messageId, 'Approved.');
        break;
      case 'override':
        await this.bot.sendMessage('Send new price as a number (e.g., "12.50").');
        break;
      case 'details':
        if (this.state && this.state.price) {
          await this.bot.sendMessage(`Algorithm: ${this.state.price.algorithm_version}`);
        }
        break;
    }
  }

  /** Handle free text (Tier 2/3 relay, or price override). */
  private async handleText(message: string): Promise<void> {
    // Check if it's a price override
    const priceNum = parseFloat(message);
    if (!isNaN(priceNum) && priceNum > 0 && this.state && this.state.step !== 'done') {
      // TODO: implement price override
      await this.bot.sendMessage(`Price override to $${priceNum.toFixed(2)} — not yet implemented.`);
      return;
    }

    // For Tier 2/3: relay to LLM conversation (stub for now)
    if (this.state && (this.state.tier === 2 || this.state.tier === 3)) {
      await this.bot.sendMessage('LLM relay not implemented yet. Tier 2/3 is pseudocode.');
      return;
    }
  }

  /** Check if a price is older than 14 days. */
  private isPriceStale(calculationDate: string): boolean {
    const priceDate = new Date(calculationDate);
    const now = new Date();
    const diffMs = now.getTime() - priceDate.getTime();
    const diffDays = diffMs / (1000 * 60 * 60 * 24);
    return diffDays > 14;
  }

  // ─── Tier 2/3 Stubs ───────────────────────────────────────

  /** Tier 2 stub — will be replaced with scoped LLM conversation. */
  private async executeTier2Stub(): Promise<void> {
    if (!this.state) return;

    await this.bot.sendInlineKeyboard(
      '🔶 *Tier 2* — This card needs scoped LLM review (not yet implemented).\nYou can approve the algorithm price or skip.',
      buildReviewKeyboard(this.state.inventoryId),
      'Markdown',
    );

    // Still request photo so the workflow can complete
    await this.executeTier1();
  }

  /** Tier 3 stub — will be replaced with full LLM conversation. */
  private async executeTier3Stub(): Promise<void> {
    if (!this.state) return;

    await this.bot.sendInlineKeyboard(
      '🔴 *Tier 3* — This card needs full LLM analysis (not yet implemented).\nYou can approve the algorithm price or skip.',
      buildReviewKeyboard(this.state.inventoryId),
      'Markdown',
    );

    // Still request photo so the workflow can complete
    await this.executeTier1();
  }

}
