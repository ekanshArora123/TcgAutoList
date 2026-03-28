/**
 * Shared types for the parent project (orchestrator, telegram, agent).
 *
 * card-server types are re-exported from card-server/src/types.ts for convenience.
 * This file defines types specific to the orchestrator and telegram integration.
 */

// Import card-server types for local use AND re-export
import type {
  Card as _Card, Sku as _Sku, InventoryItem as _InventoryItem,
  InventoryDetail as _InventoryDetail, Price as _Price,
} from '../card-server/src/types.js';

// Re-export card-server types that the orchestrator uses
export type Card = _Card;
export type Sku = _Sku;
export type InventoryItem = _InventoryItem;
export type InventoryDetail = _InventoryDetail;
export type Price = _Price;

// ─── Telegram Events ─────────────────────────────────────────

/** Events emitted by the Telegram bot module to the orchestrator. */
export type TelegramEvent =
  | { type: 'command'; command: 'next' | 'status' | 'skip' | 'pause'; chatId: number }
  | { type: 'photo'; filePath: string; chatId: number }
  | { type: 'text'; message: string; chatId: number }
  | { type: 'callback'; data: string; chatId: number; messageId: number };

// ─── Orchestrator State ──────────────────────────────────────

/** The state of the current card being processed by the orchestrator. */
export interface CardWorkflowState {
  inventoryId: number;
  inventoryDetail: InventoryDetail;
  /** Current step in the workflow. */
  step: 'idle' | 'pricing' | 'tier_routing' | 'awaiting_photo' | 'building_listing' | 'posting' | 'done';
  tier: 1 | 2 | 3 | null;
  price: Price | null;
  photoPath: string | null;
  /** Confidence from the pricing algorithm. */
  confidence: number | null;
}

// ─── Tier Routing ────────────────────────────────────────────

export interface TierConfig {
  /** Minimum confidence for Tier 1 (no LLM). Default: 80. */
  tier1MinConfidence: number;
  /** Minimum confidence for Tier 2 (scoped LLM). Default: 40. */
  tier2MinConfidence: number;
  /** Price above which always escalates to Tier 2+. */
  highValueThreshold: number;
  /** Price above which always escalates to Tier 3. */
  veryHighValueThreshold: number;
}

export const DEFAULT_TIER_CONFIG: TierConfig = {
  tier1MinConfidence: 80,
  tier2MinConfidence: 40,
  highValueThreshold: 50,
  veryHighValueThreshold: 200,
};

// ─── Photo Naming ────────────────────────────────────────────

export type PhotoSide = 'front' | 'back';
