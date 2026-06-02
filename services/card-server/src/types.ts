import { z } from 'zod';

// ─── Enums / Shared ──────────────────────────────────────────

export const CardCondition = z.enum([
  'MINT', 'NM', 'LP-NM', 'LP', 'MP-LP', 'MP', 'HP-MP', 'HP', 'DM', 'DMG'
]);

export const CardFinish = z.enum(['Regular', 'Holo', 'Reverse-Holo']);

export const InventoryStatus = z.enum([
  'unlisted', 'photo_requested', 'listed', 'skipped', 'sold'
]);

// ─── Cards ───────────────────────────────────────────────────

export const CardSchema = z.object({
  id: z.string(),
  card_name: z.string(),
  set_name: z.string().nullable(),
  product_line: z.string().default('Pokemon'),
  card_type: z.string().nullable(),
  visual_layout: z.string().nullable(),
  rarity: z.string().nullable(),
  card_number: z.string().nullable(),
  product_type: z.string().nullable(),
  era: z.string().nullable(),
  set_type: z.string().nullable(),
});

export const CreateCardInput = CardSchema.omit({});
export const UpdateCardInput = CardSchema.partial().required({ id: true });
export const SearchCardsInput = z.object({
  query: z.string().optional(),
  set_name: z.string().optional(),
  rarity: z.string().optional(),
  product_line: z.string().optional(),
  limit: z.number().default(50),
  offset: z.number().default(0),
});

// ─── SKUs ────────────────────────────────────────────────────

export const SkuSchema = z.object({
  sku_id: z.number(),
  card_id: z.string(),
  condition: CardCondition,
  finish: CardFinish,
  specialty_one: z.string().default('None'),
  specialty_two: z.string().default('None'),
  qty: z.number().default(0),
  latest_calc_date: z.string().nullable(),
});

export const CreateSkuInput = SkuSchema.omit({ sku_id: true, latest_calc_date: true });
export const UpdateSkuInput = SkuSchema.partial().required({ sku_id: true });
export const SearchSkusInput = z.object({
  card_id: z.string().optional(),
  condition: CardCondition.optional(),
  finish: CardFinish.optional(),
  specialty_one: z.string().optional(),
  limit: z.number().default(50),
  offset: z.number().default(0),
});

// ─── Inventory ───────────────────────────────────────────────

export const InventorySchema = z.object({
  inventory_id: z.number(),
  sku_id: z.number(),
  pricing_sku_id: z.number().nullable(),
  qty: z.number().default(1),
  tags: z.string().nullable(),
  status: InventoryStatus.default('unlisted'),
  front_photo_path: z.string().nullable().default(null),
  back_photo_path: z.string().nullable().default(null),
  ebay_listing_id: z.string().nullable(),
  listed_at: z.string().nullable(),
});

export const CreateInventoryInput = InventorySchema.omit({ inventory_id: true, front_photo_path: true, back_photo_path: true, ebay_listing_id: true, listed_at: true });
export const UpdateInventoryInput = InventorySchema.partial().required({ inventory_id: true });
export const SearchInventoryInput = z.object({
  sku_id: z.number().optional(),
  status: InventoryStatus.optional(),
  tags_contain: z.string().optional(),
  card_id: z.string().optional(),
  limit: z.number().default(50),
  offset: z.number().default(0),
});

// ─── Prices ──────────────────────────────────────────────────

export const PriceSchema = z.object({
  sku_id: z.number(),
  calculation_date: z.string(),
  estimated_price: z.number().nullable(),
  estimated_liquid_value: z.number().nullable(),
  confidence_percent: z.number().nullable(),
  manual_check_necessary: z.boolean().default(false),
  manually_checked: z.boolean().default(false),
  algorithm_version: z.string().nullable(),
  estimated_low_price: z.number().nullable(),
  estimated_high_price: z.number().nullable(),
  estimated_low_price_liquid: z.number().nullable(),
  estimated_high_price_liquid: z.number().nullable(),
});

export const CreatePriceInput = PriceSchema;
export const SearchPricesInput = z.object({
  sku_id: z.number().optional(),
  card_id: z.string().optional(),
  date_from: z.string().optional(),
  date_to: z.string().optional(),
  manual_check_only: z.boolean().optional(),
  limit: z.number().default(50),
  offset: z.number().default(0),
});

// ─── Joined / Composite Types ────────────────────────────────

/** Full inventory item with card info, SKU details, and latest price */
export const InventoryDetailSchema = InventorySchema.extend({
  card_id: z.string(),
  card_name: z.string(),
  set_name: z.string().nullable(),
  condition: CardCondition,
  finish: CardFinish,
  specialty_one: z.string().default('None'),
  specialty_two: z.string().default('None'),
  rarity: z.string().nullable(),
  card_number: z.string().nullable(),
  estimated_price: z.number().nullable(),
});

// ─── Type Exports ────────────────────────────────────────────

export type Card = z.infer<typeof CardSchema>;
export type CreateCard = z.infer<typeof CreateCardInput>;
export type UpdateCard = z.infer<typeof UpdateCardInput>;
export type Sku = z.infer<typeof SkuSchema>;
export type CreateSku = z.infer<typeof CreateSkuInput>;
export type UpdateSku = z.infer<typeof UpdateSkuInput>;
export type InventoryItem = z.infer<typeof InventorySchema>;
export type CreateInventoryItem = z.infer<typeof CreateInventoryInput>;
export type UpdateInventoryItem = z.infer<typeof UpdateInventoryInput>;
export type Price = z.infer<typeof PriceSchema>;
export type CreatePrice = z.infer<typeof CreatePriceInput>;
export type InventoryDetail = z.infer<typeof InventoryDetailSchema>;
