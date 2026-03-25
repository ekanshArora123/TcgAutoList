/**
 * fetchCardInfo — Fetches card metadata from TCGplayer by product ID.
 *
 * Uses TCGplayer's internal search API:
 *   POST https://mp-search-api.tcgplayer.com/v1/search/request
 *
 * Returns two separate objects:
 *   - TcgPlayerCardMetadata: Card identity info (name, set, rarity, attacks, etc.)
 *   - TcgPlayerCardPriceInfo: Market/pricing snapshot (market price, lowest price, listing counts)
 */

// ─── Types ───────────────────────────────────────────────────

/** Card identity and attributes — stable, rarely changes. */
export interface TcgPlayerCardMetadata {
  tcgplayer_id: string;
  card_name: string;
  set_name: string | null;
  product_line: string;
  card_type: string | null;
  rarity: string | null;
  card_number: string | null;
  hp: string | null;
  stage: string | null;
  description: string | null;
  attacks: string[];
  weakness: string | null;
  resistance: string | null;
  retreat_cost: string | null;
  flavor_text: string | null;
  energy_type: string[];
  release_date: string | null;
  foil_only: boolean;
}

/** Price/market snapshot — changes frequently. */
export interface TcgPlayerCardPriceInfo {
  tcgplayer_id: string;
  market_price: number | null;
  lowest_price: number | null;
  lowest_price_with_shipping: number | null;
  total_listings: number | null;
  available_conditions: {
    condition: string;
    finish: string;
    listing_count: number;
  }[];
}

/** Combined result from the search API call. */
export interface TcgPlayerCardFetchResult {
  metadata: TcgPlayerCardMetadata;
  priceInfo: TcgPlayerCardPriceInfo;
}

// ─── Headers ─────────────────────────────────────────────────

const SEARCH_API_HEADERS: Record<string, string> = {
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

// ─── Main Fetch ──────────────────────────────────────────────

/**
 * Fetch card data from TCGplayer for a given product ID.
 * Returns metadata and price info as separate objects.
 *
 * @param tcgplayerId - TCGplayer product ID
 * @returns Both metadata and price info, or null if not found
 */
export async function fetchCardInfo(tcgplayerId: string): Promise<TcgPlayerCardFetchResult | null> {
  const url = 'https://mp-search-api.tcgplayer.com/v1/search/request?q=&isList=false&mpfev=2163';

  const payload = {
    algorithm: 'sales_synonym_v2',
    from: 0,
    size: 1,
    filters: {
      term: {
        productLineName: ['pokemon'],
        productId: [Number(tcgplayerId)],
      },
    },
    listingSearch: {
      filters: {
        term: {},
        range: { quantity: { gte: 1 } },
        exclude: { channelExclusion: 0 },
      },
      context: { cart: {} },
    },
  };

  try {
    const response = await fetch(url, {
      method: 'POST',
      headers: SEARCH_API_HEADERS,
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      if (response.status === 404) return null;
      throw new Error(`TCGplayer search API error: ${response.status} ${response.statusText}`);
    }

    const json: any = await response.json();

    const searchResult = json?.results?.[0];
    if (!searchResult?.results?.[0]) return null;

    const product = searchResult.results[0];
    const attrs = product.customAttributes ?? {};
    const aggs = searchResult.aggregations ?? {};

    // Extract attacks (up to 4)
    const attacks: string[] = [];
    for (const key of ['attack1', 'attack2', 'attack3', 'attack4']) {
      if (attrs[key]) attacks.push(stripHtml(attrs[key]));
    }

    const metadata: TcgPlayerCardMetadata = {
      tcgplayer_id: tcgplayerId,
      card_name: product.productName ?? '',
      set_name: product.setName ?? null,
      product_line: product.productLineName ?? 'Pokemon',
      card_type: attrs.cardType?.[0] ?? null,
      rarity: product.rarityName ?? null,
      card_number: attrs.number ?? null,
      hp: attrs.hp ?? null,
      stage: attrs.stage ?? null,
      description: attrs.description ? stripHtml(attrs.description) : null,
      attacks,
      weakness: attrs.weakness ?? null,
      resistance: attrs.resistance ?? null,
      retreat_cost: attrs.retreatCost ?? null,
      flavor_text: attrs.flavorText ? stripHtml(attrs.flavorText) : null,
      energy_type: attrs.energyType ?? [],
      release_date: attrs.releaseDate ?? null,
      foil_only: product.foilOnly ?? false,
    };

    const priceInfo: TcgPlayerCardPriceInfo = {
      tcgplayer_id: tcgplayerId,
      market_price: product.marketPrice ?? null,
      lowest_price: product.lowestPrice ?? null,
      lowest_price_with_shipping: product.lowestPriceWithShipping ?? null,
      total_listings: product.totalListings ?? null,
      available_conditions: buildAvailableConditions(aggs),
    };

    return { metadata, priceInfo };
  } catch (err) {
    if (err instanceof Error && err.message.includes('TCGplayer search API error')) throw err;
    return null;
  }
}

/**
 * Fetch just card metadata (no price info). Convenience wrapper.
 */
export async function fetchCardMetadata(tcgplayerId: string): Promise<TcgPlayerCardMetadata | null> {
  const result = await fetchCardInfo(tcgplayerId);
  return result?.metadata ?? null;
}

/**
 * Fetch just price info snapshot. Convenience wrapper.
 */
export async function fetchCardPriceInfo(tcgplayerId: string): Promise<TcgPlayerCardPriceInfo | null> {
  const result = await fetchCardInfo(tcgplayerId);
  return result?.priceInfo ?? null;
}

/**
 * Fetch card data for multiple product IDs in batch.
 * Sequential with configurable delay for rate limiting.
 */
export async function fetchCardInfoBatch(
  tcgplayerIds: string[],
  delayMs = 100,
): Promise<(TcgPlayerCardFetchResult | null)[]> {
  const results: (TcgPlayerCardFetchResult | null)[] = [];

  for (let i = 0; i < tcgplayerIds.length; i++) {
    if (i > 0 && delayMs > 0) {
      await new Promise(resolve => setTimeout(resolve, delayMs));
    }
    results.push(await fetchCardInfo(tcgplayerIds[i]));
  }

  return results;
}

// ─── Helpers ──────────────────────────────────────────────────

/**
 * Build available condition/finish combos from search API aggregation data.
 */
function buildAvailableConditions(
  aggs: Record<string, { value: string; count: number }[]>,
): { condition: string; finish: string; listing_count: number }[] {
  const conditions = aggs.condition ?? [];
  const printings = aggs.printing ?? [];

  if (conditions.length === 0 || printings.length === 0) return [];

  // The API doesn't give per-condition-per-finish counts directly,
  // so we create the cross product. listing_count is the condition count.
  const results: { condition: string; finish: string; listing_count: number }[] = [];

  for (const cond of conditions) {
    for (const print of printings) {
      results.push({
        condition: parseConditionFromSearch(cond.value),
        finish: parseFinishFromSearch(print.value),
        listing_count: cond.count,
      });
    }
  }

  return results;
}

function parseConditionFromSearch(apiCondition: string): string {
  const map: Record<string, string> = {
    'Near Mint': 'NM',
    'Lightly Played': 'LP',
    'Moderately Played': 'MP',
    'Heavily Played': 'HP',
    'Damaged': 'DMG',
  };
  return map[apiCondition] ?? apiCondition;
}

function parseFinishFromSearch(printing: string): string {
  const map: Record<string, string> = {
    'Normal': 'Regular',
    'Holofoil': 'Holo',
    'Reverse Holofoil': 'Reverse-Holo',
    '1st Edition Holofoil': 'Holo',
    '1st Edition': 'Regular',
    'Unlimited Holofoil': 'Holo',
    'Unlimited': 'Regular',
  };
  return map[printing] ?? printing;
}

function stripHtml(html: string): string {
  return html.replace(/<[^>]*>/g, '').replace(/\r\n/g, ' ').replace(/\s+/g, ' ').trim();
}
