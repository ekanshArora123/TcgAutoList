/**
 * fetchPrices — Fetches pricing data from TCGplayer for a given product/SKU.
 *
 * This module handles web requests to TCGplayer for:
 *   - Active listing prices (for competitive pricing)
 *   - Recent sold listings (for price estimation) — TODO
 *   - Current market prices by condition — TODO
 *
 * Ported from QuickCollectionValueFinder.py.
 */

import { formatConditionForApi, formatFinishForApi } from './formatters.js';

// ─── Types ───────────────────────────────────────────────────

export interface TcgPlayerSoldListing {
  tcgplayer_id: string;
  condition: string;
  finish: string;
  sold_price: number;
  sold_date: string;
  seller_name: string | null;
}

export interface TcgPlayerActiveListing {
  tcgplayer_id: string;
  condition: string;
  finish: string;
  listed_price: number;
  shipping_price: number;
  seller_name: string | null;
  seller_feedback_count: number | null;
  seller_rating: number | null;
  seller_sales: string | null;
}

// ─── TCGplayer Listings API ──────────────────────────────────

/** Headers that mimic a browser request to TCGplayer's internal search API. */
const TCGPLAYER_HEADERS: Record<string, string> = {
  'authority': 'mp-search-api.tcgplayer.com',
  'accept': 'application/json, text/plain, */*',
  'accept-language': 'en-US,en;q=0.9',
  'content-type': 'application/json',
  'origin': 'https://www.tcgplayer.com',
  'referer': 'https://www.tcgplayer.com/',
  'sec-ch-ua': '"Not A Brand";v="99", "Google Chrome";v="121", "Chromium";v="121"',
  'sec-ch-ua-mobile': '?0',
  'sec-ch-ua-platform': '"Windows"',
  'sec-fetch-dest': 'empty',
  'sec-fetch-mode': 'cors',
  'sec-fetch-site': 'same-site',
  'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
};

/**
 * Minimum seller quality to include a listing in pricing.
 * Filters out low-reputation sellers whose prices may be unreliable.
 */
const MIN_SELLER_RATING = 80;
const MIN_SELLER_SALES = 30;

/**
 * Build the POST payload for TCGplayer's mp-search-api listings endpoint.
 *
 * @param conditions - Array of API-formatted condition strings (e.g., ["Near Mint"])
 * @param finish     - API-formatted finish string (e.g., "Holofoil")
 * @param offset     - Pagination offset (default 0). TCGplayer returns max 50 per request.
 * @param size       - Number of results per page (max 50).
 */
function buildListingsPayload(
  conditions: string[],
  finish: string,
  offset = 0,
  size = 50,
) {
  return {
    filters: {
      term: {
        sellerStatus: 'Live',
        channelId: 0,
        language: ['English'],
        condition: conditions,
        printing: finish,
        listingType: 'standard',
      },
      range: { quantity: { gte: 1 } },
      exclude: { channelExclusion: 0 },
    },
    from: offset,
    size,
    sort: {
      field: 'price+shipping',
      order: 'asc',
    },
    context: {
      shippingCountry: 'US',
      cart: {},
    },
    aggregations: ['listingType'],
  };
}

/**
 * Raw listing result from the TCGplayer API.
 * The API returns more fields but these are the ones we use.
 */
interface RawListingResult {
  score: number;           // price + shipping combined
  price: number;
  shippingPrice: number;
  sellerName?: string;
  sellerRating?: string | number;
  sellerSales?: string;
  condition?: string;
  printing?: string;
}

/**
 * Fetch active listings from TCGplayer's internal search API.
 *
 * This hits the same endpoint the TCGplayer website uses:
 *   POST https://mp-search-api.tcgplayer.com/v1/product/{id}/listings?mpfev=2163
 *
 * Results are sorted by price+shipping ascending (cheapest first).
 * Low-reputation sellers (rating < 80 or sales < 30) are filtered out
 * to avoid unreliable pricing signals.
 *
 * @param tcgplayerId - TCGplayer product ID
 * @param condition   - Internal condition string (e.g., "NM", "LP"). If omitted, fetches all conditions.
 * @param finish      - Internal finish string (e.g., "Holo", "Regular"). Required for API formatting.
 * @param setName     - Set name (needed to determine Unlimited vs normal finish for WOTC sets).
 * @param specialtyOne - Specialty (e.g., "First Edition") for finish formatting.
 * @param offset      - Pagination offset. TCGplayer returns max 50 per request.
 */
export async function fetchActiveListings(
  tcgplayerId: string,
  condition?: string,
  finish?: string,
  setName?: string,
  specialtyOne?: string,
  offset = 0,
): Promise<TcgPlayerActiveListing[]> {
  const apiConditions = condition
    ? formatConditionForApi(condition)
    : ['Near Mint']; // default to NM if not specified

  const apiFinish = formatFinishForApi(
    finish ?? 'Regular',
    specialtyOne ?? 'None',
    setName ?? '',
  );

  const url = `https://mp-search-api.tcgplayer.com/v1/product/${tcgplayerId}/listings`;
  const payload = buildListingsPayload(apiConditions, apiFinish, offset);

  const response = await fetch(`${url}?mpfev=2163`, {
    method: 'POST',
    headers: TCGPLAYER_HEADERS,
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    if (response.status === 404) return [];
    throw new Error(`TCGplayer API error: ${response.status} ${response.statusText}`);
  }

  const json = await response.json();
  if (!json?.results?.[0]?.results) return [];

  const rawResults: RawListingResult[] = json.results[0].results;
  return filterAndMapListings(rawResults, tcgplayerId, condition ?? 'NM', finish ?? 'Regular');
}

/**
 * Filter out low-quality sellers and map raw API results to our type.
 *
 * From QuickCollectionValueFinder.py cleanRawListingsData:
 *   - sellerRating must be > 80
 *   - sellerSales must end with "+" or be > 30
 */
function filterAndMapListings(
  raw: RawListingResult[],
  tcgplayerId: string,
  condition: string,
  finish: string,
): TcgPlayerActiveListing[] {
  const results: TcgPlayerActiveListing[] = [];

  for (const row of raw) {
    const rating = parseNumeric(row.sellerRating);
    const sales = row.sellerSales;

    // Filter: skip sellers with bad/missing reputation
    if (rating === null || rating < MIN_SELLER_RATING) continue;
    if (sales === undefined || sales === 'NA') continue;
    const salesOk = sales.endsWith('+') || parseNumeric(sales) !== null && parseNumeric(sales)! > MIN_SELLER_SALES;
    if (!salesOk) continue;

    results.push({
      tcgplayer_id: tcgplayerId,
      condition,
      finish,
      listed_price: row.price ?? 0,
      shipping_price: row.shippingPrice ?? 0,
      seller_name: row.sellerName ?? null,
      seller_feedback_count: null,
      seller_rating: rating,
      seller_sales: sales,
    });
  }

  return results;
}

function parseNumeric(val: unknown): number | null {
  if (typeof val === 'number') return val;
  if (typeof val === 'string') {
    const cleaned = val.replace(/[+,]/g, '');
    const n = Number(cleaned);
    return isNaN(n) ? null : n;
  }
  return null;
}

// ─── TCGplayer Sales API ─────────────────────────────────────

/**
 * Sales API condition filter IDs.
 * The sales endpoint requires integer IDs, not string names.
 */
const SALES_CONDITION_IDS: Record<string, number> = {
  'NM': 1,
  'Near Mint': 1,
  'LP': 2,
  'Lightly Played': 2,
  'MP': 3,
  'Moderately Played': 3,
  'HP': 4,
  'Heavily Played': 4,
  'DMG': 5,
  'DM': 5,
  'Damaged': 5,
};

/**
 * Sales API variant (finish) filter IDs.
 * These are TCGplayer-internal IDs for the "printing" variants.
 */
const SALES_VARIANT_IDS: Record<string, number> = {
  'Normal': 10,
  'Regular': 10,
  'Holofoil': 11,
  'Holo': 11,
  'Reverse Holofoil': 77,
  'Reverse-Holo': 77,
};

/**
 * Fetch recent sold listings from TCGplayer's sales API.
 *
 * Endpoint: POST https://mpapi.tcgplayer.com/v2/product/{id}/latestsales
 *
 * This API returns a maximum of 5 results per request, even with limit > 5.
 * When no condition filter is specified, it returns the 5 most recent sales
 * across all conditions. To get more data, we query each condition separately
 * (up to 25 total: 5 conditions x 5 results each).
 *
 * @param tcgplayerId - TCGplayer product ID
 * @param condition   - Internal condition (e.g., "NM"). If omitted, fetches all conditions separately.
 * @param finish      - Internal finish (e.g., "Holo"). Maps to variant ID filter.
 */
export async function fetchSoldListings(
  tcgplayerId: string,
  condition?: string,
  finish?: string,
): Promise<TcgPlayerSoldListing[]> {
  // If no condition specified, query each condition separately to maximize data
  if (!condition) {
    const conditions = ['NM', 'LP', 'MP', 'HP', 'DMG'];
    const promises = conditions.map(c => fetchSoldListingsForCondition(tcgplayerId, c, finish));
    const results = await Promise.all(promises);
    return results.flat();
  }

  return fetchSoldListingsForCondition(tcgplayerId, condition, finish);
}

/**
 * Fetch sold listings for a specific condition.
 */
async function fetchSoldListingsForCondition(
  tcgplayerId: string,
  condition: string,
  finish?: string,
): Promise<TcgPlayerSoldListing[]> {
  const url = `https://mpapi.tcgplayer.com/v2/product/${tcgplayerId}/latestsales`;

  const conditionId = SALES_CONDITION_IDS[condition];
  const conditions = conditionId ? [conditionId] : [];

  const variants: number[] = [];
  if (finish) {
    const variantId = SALES_VARIANT_IDS[finish];
    if (variantId) variants.push(variantId);
  }

  const payload = {
    variants,
    listingType: 'All',
    conditions,
    languages: [1], // English
    limit: 25,
    offset: 0,
  };

  try {
    const response = await fetch(url, {
      method: 'POST',
      headers: {
        ...TCGPLAYER_HEADERS,
        'authority': 'mpapi.tcgplayer.com',
      },
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      if (response.status === 404) return [];
      throw new Error(`TCGplayer sales API error: ${response.status} ${response.statusText}`);
    }

    const json = await response.json();
    if (!json?.data || !Array.isArray(json.data)) return [];

    return json.data.map((sale: any) => ({
      tcgplayer_id: tcgplayerId,
      condition: parseConditionFromSalesApi(sale.condition ?? condition),
      finish: parseFinishFromSalesApi(sale.variant ?? ''),
      sold_price: (sale.purchasePrice ?? 0) + (sale.shippingPrice ?? 0),
      sold_date: sale.orderDate ?? '',
      seller_name: null, // Sales API doesn't include seller name
    }));
  } catch (err) {
    if (err instanceof Error && err.message.includes('TCGplayer sales API error')) throw err;
    // Network errors — return empty rather than crashing
    return [];
  }
}

/**
 * Map sales API condition strings back to internal format.
 */
function parseConditionFromSalesApi(apiCondition: string): string {
  const map: Record<string, string> = {
    'Near Mint': 'NM',
    'Lightly Played': 'LP',
    'Moderately Played': 'MP',
    'Heavily Played': 'HP',
    'Damaged': 'DMG',
  };
  return map[apiCondition] ?? apiCondition;
}

/**
 * Map sales API variant strings back to internal finish format.
 */
function parseFinishFromSalesApi(variant: string): string {
  const map: Record<string, string> = {
    'Normal': 'Regular',
    'Holofoil': 'Holo',
    'Reverse Holofoil': 'Reverse-Holo',
    '1st Edition Holofoil': 'Holo',
    '1st Edition': 'Regular',
    'Unlimited Holofoil': 'Holo',
    'Unlimited': 'Regular',
  };
  return map[variant] ?? variant;
}

