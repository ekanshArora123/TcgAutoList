/**
 * Tier 1 Pipeline — Dumb pipe for high-confidence cards.
 *
 * No LLM involvement. Builds a template listing from DB data,
 * requests a photo via Telegram, and posts to eBay.
 *
 * Handles ~80% of cards with zero token cost.
 */

import type { InventoryDetail, Price } from '../types.js';

/** Template listing data ready for eBay posting. */
export interface ListingTemplate {
  title: string;
  description: string;
  price: number;
  condition: string;
  category: string;
  photoPath: string;
}

/**
 * Build a template eBay listing from card data and price.
 * This is pure code — no LLM needed.
 */
export function buildListingTemplate(
  detail: InventoryDetail,
  price: Price,
  photoPath: string,
): ListingTemplate {
  const conditionMap: Record<string, string> = {
    'MINT': 'Brand New',
    'NM': 'Near Mint',
    'LP-NM': 'Near Mint',    // List as NM on eBay (in-between goes up)
    'LP': 'Lightly Played',
    'MP-LP': 'Lightly Played',
    'MP': 'Moderately Played',
    'HP-MP': 'Moderately Played',
    'HP': 'Heavily Played',
    'DM': 'Damaged',
    'DMG': 'Damaged',
  };

  const ebayCondition = conditionMap[detail.condition] ?? 'Near Mint';
  const title = buildTitle(detail);
  const description = buildDescription(detail, price);

  return {
    title,
    description,
    price: price.estimated_price!,
    condition: ebayCondition,
    category: 'Pokemon Individual Cards', // eBay category
    photoPath,
  };
}

/** Build eBay listing title (max 80 chars). */
function buildTitle(detail: InventoryDetail): string {
  const parts = [
    detail.card_name,
    detail.card_number ? `${detail.card_number}` : '',
    detail.set_name ?? '',
    detail.finish !== 'Regular' ? detail.finish : '',
    'Pokemon Card',
  ].filter(Boolean);

  let title = parts.join(' ');
  if (title.length > 80) {
    // Trim set name first, then card number
    title = [detail.card_name, detail.finish !== 'Regular' ? detail.finish : '', 'Pokemon Card']
      .filter(Boolean).join(' ');
  }
  return title.slice(0, 80);
}

/** Build eBay listing description. */
function buildDescription(detail: InventoryDetail, price: Price): string {
  const lines = [
    `${detail.card_name}`,
    '',
    `Set: ${detail.set_name ?? 'Unknown'}`,
    `Card Number: ${detail.card_number ?? 'N/A'}`,
    `Condition: ${detail.condition}`,
    `Finish: ${detail.finish}`,
    `Rarity: ${detail.rarity ?? 'N/A'}`,
  ];

  if (detail.tags) {
    lines.push('');
    lines.push(`Note: ${detail.tags}`);
  }

  lines.push('');
  lines.push('Ships in a penny sleeve + toploader in a PWE (plain white envelope).');
  lines.push('Cards over $25 ship in a tracked bubble mailer.');

  return lines.join('\n');
}

/**
 * Post a listing to eBay.
 *
 * TODO: Implement when eBay integration is ready.
 * For now, returns a stub listing ID.
 */
export async function postToEbay(listing: ListingTemplate): Promise<string> {
  // STUB — will be replaced with ebay-mcp integration
  console.log(`[eBay STUB] Would post listing: ${listing.title} @ $${listing.price.toFixed(2)}`);
  return `ebay-stub-${Date.now()}`;
}
