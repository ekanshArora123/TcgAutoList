/**
 * eBay Service — Manages eBay listing CRUD operations.
 *
 * STUB — Not yet implemented. Will wrap the eBay Sell API
 * (listing creation, photo upload, category mapping, etc.).
 */

/** Template listing data ready for eBay posting. */
export interface ListingTemplate {
  title: string;
  description: string;
  price: number;
  condition: string;
  category: string;
  photoPaths: string[];
}

/**
 * Post a listing to eBay.
 *
 * TODO: Implement when eBay integration is ready.
 * For now, returns a stub listing ID.
 */
export async function postToEbay(listing: ListingTemplate): Promise<string> {
  // STUB — will be replaced with eBay API integration
  console.log(`[eBay STUB] Would post listing: ${listing.title} @ $${listing.price.toFixed(2)} (${listing.photoPaths.length} photos)`);
  return `ebay-stub-${Date.now()}`;
}
