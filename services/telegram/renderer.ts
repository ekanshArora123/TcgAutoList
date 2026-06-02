/**
 * Message renderer — formats card details, prices, and status into
 * Telegram messages with inline keyboards.
 */

import type { InventoryDetail, Price } from '../../src/types.js';

/** Format card details for display. */
export function renderCardDetails(detail: InventoryDetail): string {
  const lines = [
    `*${escMd(detail.card_name)}*`,
    `Set: ${escMd(detail.set_name ?? 'Unknown')}`,
    `Condition: ${escMd(detail.condition)}  |  Finish: ${escMd(detail.finish)}`,
  ];

  if (detail.rarity) lines.push(`Rarity: ${escMd(detail.rarity)}`);
  if (detail.card_number) lines.push(`Number: ${escMd(detail.card_number)}`);
  if (detail.tags) lines.push(`Tags: ${escMd(detail.tags)}`);

  return lines.join('\n');
}

/** Format price info for display. */
export function renderPriceInfo(price: Price): string {
  if (price.estimated_price === null) {
    return `Price: *Unpriceable*\nConfidence: ${price.confidence_percent ?? 0}%`;
  }

  const lines = [
    `Price: *$${price.estimated_price.toFixed(2)}*`,
    `Liquid Value: $${price.estimated_liquid_value?.toFixed(2) ?? 'N/A'}`,
    `Confidence: ${price.confidence_percent}%`,
  ];

  if (price.estimated_low_price !== null && price.estimated_high_price !== null) {
    lines.push(`Range: $${price.estimated_low_price.toFixed(2)} — $${price.estimated_high_price.toFixed(2)}`);
  }

  if (price.manual_check_necessary) {
    lines.push('⚠ Manual review flagged');
  }

  return lines.join('\n');
}

/** Format a combined card + price summary for the /next flow. */
export function renderCardSummary(detail: InventoryDetail, price: Price | null): string {
  const parts = [renderCardDetails(detail)];

  if (price) {
    parts.push('');
    parts.push(renderPriceInfo(price));
  }

  return parts.join('\n');
}

/** Format status update message. */
export function renderStatus(
  current: InventoryDetail | null,
  stats: { total: number; by_status: Record<string, number> },
): string {
  const lines = ['*Collection Status*', ''];

  for (const [status, count] of Object.entries(stats.by_status)) {
    lines.push(`${escMd(status)}: ${count}`);
  }
  lines.push(`Total: ${stats.total}`);

  if (current) {
    lines.push('');
    lines.push('*Currently processing:*');
    lines.push(`${escMd(current.card_name)} (${escMd(current.set_name ?? 'Unknown')})`);
    lines.push(`Status: ${escMd(current.status)}`);
  }

  return lines.join('\n');
}

/** Build inline keyboard for photo request. */
export function buildPhotoRequestKeyboard(inventoryId: number): { text: string; callbackData: string }[][] {
  return [
    [{ text: 'Skip this card', callbackData: `skip:${inventoryId}` }],
  ];
}

/** Build inline keyboard for manual review. */
export function buildReviewKeyboard(inventoryId: number): { text: string; callbackData: string }[][] {
  return [
    [
      { text: 'Approve', callbackData: `approve:${inventoryId}` },
      { text: 'Skip', callbackData: `skip:${inventoryId}` },
    ],
    [
      { text: 'Override Price', callbackData: `override:${inventoryId}` },
      { text: 'Details', callbackData: `details:${inventoryId}` },
    ],
  ];
}

/** Format photo request message. */
export function renderPhotoRequest(detail: InventoryDetail, side: 'front' | 'back'): string {
  return [
    `📷 Send *${side}* photo for:`,
    `*${escMd(detail.card_name)}*`,
    `${escMd(detail.set_name ?? 'Unknown')} — ${escMd(detail.condition)} ${escMd(detail.finish)}`,
  ].join('\n');
}

/** Render listing confirmation. */
export function renderListingConfirmation(detail: InventoryDetail, price: number): string {
  return [
    '✅ *Listing created*',
    `${escMd(detail.card_name)} — ${escMd(detail.set_name ?? 'Unknown')}`,
    `${escMd(detail.condition)} ${escMd(detail.finish)}`,
    `Price: $${price.toFixed(2)}`,
  ].join('\n');
}

/** Escape Markdown special characters for Telegram. */
function escMd(text: string): string {
  return text.replace(/[_*[\]()~`>#+\-=|{}.!]/g, '\\$&');
}
