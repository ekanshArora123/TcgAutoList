import type Database from 'better-sqlite3';
import { CardsHelper } from '../helpers/crud/cards.js';
import { SkusHelper } from '../helpers/crud/skus.js';
import { InventoryHelper } from '../helpers/crud/inventory.js';
import { PricesHelper } from '../helpers/crud/prices.js';
import { fetchCardInfo } from '../helpers/tcgplayer/fetchCardInfo.js';
import type {
  Card, CreateCard, UpdateCard,
  Sku, CreateSku,
  InventoryItem, CreateInventoryItem, UpdateInventoryItem,
  InventoryDetail,
} from '../types.js';

/**
 * CollectionService — High-level operations the agent calls for managing the card collection.
 *
 * Composes CRUD helpers + TCGplayer fetchers into workflow-level operations.
 * This is one of two files the agent interacts with (the other is PricingService).
 */
export class CollectionService {
  private cards: CardsHelper;
  private skus: SkusHelper;
  private inventory: InventoryHelper;
  private prices: PricesHelper;

  constructor(private db: Database.Database) {
    this.cards = new CardsHelper(db);
    this.skus = new SkusHelper(db);
    this.inventory = new InventoryHelper(db);
    this.prices = new PricesHelper(db);
  }

  // ─── Card Lookups (with auto-fetch) ──────────────────────

  /**
   * Get a card by TCGplayer ID. If not in DB, fetches from TCGplayer and stores it.
   * This is the "smart" lookup — the agent doesn't need to know if the card exists yet.
   *
   * Pseudocode for the fetch path:
   *   1. cards.getById(id) — check DB first
   *   2. If null: fetchCardInfo(id) from TCGplayer
   *   3. If fetched: cards.upsert(data) to store it
   *   4. Also create SKU entries for each available condition/finish from TCGplayer
   *   5. Return the card
   */
  async getCard(tcgplayerId: string): Promise<Card | null> {
    const existing = this.cards.getById(tcgplayerId);
    if (existing) return existing;

    // Auto-fetch from TCGplayer if not in DB
    const fetched = await fetchCardInfo(tcgplayerId);
    if (!fetched) return null;

    const { metadata, priceInfo } = fetched;

    const card = this.cards.upsert({
      id: metadata.tcgplayer_id,
      card_name: metadata.card_name,
      set_name: metadata.set_name,
      product_line: metadata.product_line,
      card_type: metadata.card_type,
      visual_layout: null,
      rarity: metadata.rarity,
      card_number: metadata.card_number,
      product_type: null,
      era: null,
      set_type: null,
    });

    // Pre-create SKU entries for known conditions
    for (const variant of priceInfo.available_conditions) {
      this.skus.getOrCreate({
        card_id: card.id,
        condition: variant.condition as any,
        finish: variant.finish as any,
        specialty_one: 'None',
        specialty_two: 'None',
        qty: 0,
      });
    }

    return card;
  }

  /** Direct DB-only card lookup (no fetch). */
  getCardLocal(tcgplayerId: string): Card | null {
    return this.cards.getById(tcgplayerId);
  }

  /** Search cards in DB by name, set, rarity, etc. */
  searchCards(filters: {
    query?: string;
    set_name?: string;
    rarity?: string;
    product_line?: string;
    limit?: number;
    offset?: number;
  }): Card[] {
    return this.cards.search(filters);
  }

  /** Update card metadata. */
  updateCard(input: UpdateCard): Card | null {
    return this.cards.update(input);
  }

  /** Bulk import cards (from migration or CSV). */
  bulkImportCards(cards: CreateCard[]): number {
    return this.cards.bulkUpsert(cards);
  }

  /** Get all set names for filtering. */
  listSets(): string[] {
    return this.cards.getAllSetNames();
  }

  /** Get all rarities for filtering. */
  listRarities(): string[] {
    return this.cards.getAllRarities();
  }

  // ─── SKU Operations ──────────────────────────────────────

  /** Get or create a SKU for a card+condition+finish+specialties combo. */
  resolveSku(input: CreateSku): Sku {
    return this.skus.getOrCreate(input);
  }

  /** Get all SKU variants for a card. */
  getSkusForCard(cardId: string): Sku[] {
    return this.skus.getByCardId(cardId);
  }

  /** Search SKUs. */
  searchSkus(filters: {
    card_id?: string;
    condition?: string;
    finish?: string;
    specialty_one?: string;
    limit?: number;
    offset?: number;
  }): Sku[] {
    return this.skus.search(filters);
  }

  /** Get all conditions in use. */
  listConditions(): string[] {
    return this.skus.getAllConditions();
  }

  /** Get all specialty values in use. */
  listSpecialties(): string[] {
    return this.skus.getAllSpecialties();
  }

  // ─── Inventory Operations ────────────────────────────────

  /**
   * Add a card to inventory using human-friendly params.
   * Resolves the SKU automatically from card_id + condition + finish + specialties,
   * creating it if needed.
   */
  addToInventory(params: {
    card_id: string;
    condition: string;
    finish?: string;
    specialty_one?: string;
    specialty_two?: string;
    pricing_condition?: string;
    pricing_finish?: string;
    qty?: number;
    tags?: string;
  }): InventoryItem {
    const sku = this.skus.getOrCreate({
      card_id: params.card_id,
      condition: params.condition as any,
      finish: (params.finish ?? 'Regular') as any,
      specialty_one: params.specialty_one ?? 'None',
      specialty_two: params.specialty_two ?? 'None',
      qty: 0,
    });

    let pricingSkuId: number | null = null;
    if (params.pricing_condition || params.pricing_finish) {
      const pricingSku = this.skus.getOrCreate({
        card_id: params.card_id,
        condition: (params.pricing_condition ?? params.condition) as any,
        finish: (params.pricing_finish ?? params.finish ?? 'Regular') as any,
        specialty_one: params.specialty_one ?? 'None',
        specialty_two: params.specialty_two ?? 'None',
        qty: 0,
      });
      pricingSkuId = pricingSku.sku_id;
    }

    return this.inventory.create({
      sku_id: sku.sku_id,
      pricing_sku_id: pricingSkuId,
      qty: params.qty ?? 1,
      tags: params.tags ?? null,
      status: 'unlisted',
    });
  }

  /** Get an inventory item with full card + price details. */
  getInventoryDetail(inventoryId: number): InventoryDetail | null {
    return this.inventory.getDetailById(inventoryId);
  }

  /** Get next card in the listing queue. */
  getNextUnlisted(): InventoryDetail | null {
    return this.inventory.getNextUnlisted();
  }

  /** Search inventory. */
  searchInventory(filters: {
    sku_id?: number;
    status?: string;
    tags_contain?: string;
    card_id?: string;
    limit?: number;
    offset?: number;
  }): InventoryItem[] {
    return this.inventory.search(filters);
  }

  /** Search inventory with full card/price detail. */
  searchInventoryWithDetails(filters: {
    status?: string;
    card_id?: string;
    limit?: number;
    offset?: number;
  }): InventoryDetail[] {
    return this.inventory.searchWithDetails(filters);
  }

  /** Update inventory item fields. */
  updateInventory(input: UpdateInventoryItem): InventoryItem | null {
    return this.inventory.update(input);
  }

  /** Mark a card as listed on eBay. */
  markAsListed(inventoryId: number, ebayListingId: string): InventoryItem | null {
    return this.inventory.markAsListed(inventoryId, ebayListingId);
  }

  /** Mark a card as sold. */
  markAsSold(inventoryId: number): InventoryItem | null {
    return this.inventory.markAsSold(inventoryId);
  }

  /** Update inventory item status. */
  updateStatus(inventoryId: number, status: string): InventoryItem | null {
    return this.inventory.updateStatus(inventoryId, status);
  }

  /** Override the pricing SKU for borderline condition cards. */
  setPricingSku(inventoryId: number, pricingSkuId: number | null): InventoryItem | null {
    return this.inventory.setPricingSku(inventoryId, pricingSkuId);
  }

  /** Add a hidden tag to an inventory item. */
  addTag(inventoryId: number, tag: string): InventoryItem | null {
    return this.inventory.addTag(inventoryId, tag);
  }

  /** Remove a tag from an inventory item. */
  removeTag(inventoryId: number, tag: string): InventoryItem | null {
    return this.inventory.removeTag(inventoryId, tag);
  }

  /** Get collection statistics. */
  getStats(): { total: number; by_status: Record<string, number>; total_estimated_value: number | null } {
    return this.inventory.getStats();
  }

  /** Delete an inventory item. */
  deleteInventoryItem(inventoryId: number): boolean {
    return this.inventory.delete(inventoryId);
  }

  /** Bulk add inventory items. */
  bulkAddToInventory(items: CreateInventoryItem[]): number {
    return this.inventory.bulkCreate(items);
  }
}
