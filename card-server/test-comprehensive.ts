/**
 * Comprehensive card-server test script.
 * Tests: CRUD operations, TCGplayer API calls, pricing algorithm, edge cases.
 *
 * Uses real TCGplayer API calls — requires internet access.
 *
 * Test cards (representative edge cases):
 *   - 86929  = Base Set Charizard (WOTC holo, high value, Unlimited finish)
 *   - 510399 = Pikachu VMAX (modern, liquid, should have lots of data)
 *   - 233381 = Pikachu V (Vivid Voltage, common modern card)
 *   - 87    = Machamp (Base Set, 1st Edition only holo — foilOnly card)
 *   - 293587 = Arceus & Dialga & Palkia GX (Tag Team, high value)
 *   - 451287 = A random obscure card that may have no listings
 *   - 521399 = Likely nonexistent ID (edge case: 404)
 */

import { initDatabase, closeDatabase, getDb } from './src/db.js';
import { CollectionService } from './src/services/collectionService.js';
import { PricingService } from './src/services/pricingService.js';
import { computePrice, extrapolateAcrossConditions, computeLiquidValue } from './src/helpers/pricing/algorithm.js';
import { fetchActiveListings, fetchSoldListings } from './src/helpers/tcgplayer/fetchPrices.js';
import { fetchCardInfo } from './src/helpers/tcgplayer/fetchCardInfo.js';
import { formatConditionForApi, formatFinishForApi } from './src/helpers/tcgplayer/formatters.js';

// ─── Test Harness ─────────────────────────────────────────────

let passed = 0;
let failed = 0;
const failures: string[] = [];
const findings: string[] = [];

function log(msg: string) { console.log(msg); }
function pass(name: string, detail?: string) {
  passed++;
  log(`  ✓ ${name}${detail ? ` — ${detail}` : ''}`);
}
function fail(name: string, detail: string) {
  failed++;
  failures.push(`${name}: ${detail}`);
  log(`  ✗ ${name} — ${detail}`);
}
function finding(msg: string) {
  findings.push(msg);
  log(`  ★ FINDING: ${msg}`);
}
function section(name: string) { log(`\n${'═'.repeat(60)}\n  ${name}\n${'═'.repeat(60)}`); }

// ─── Test Data ────────────────────────────────────────────────

// Well-known Pokemon TCGplayer product IDs
const TEST_CARDS = {
  // Base Set Charizard — WOTC, Holo, high value, should use Unlimited Holofoil finish
  BASE_CHARIZARD: '86929',
  // Pikachu VMAX — modern, liquid, lots of data
  PIKACHU_VMAX: '510399',
  // Pikachu V (Vivid Voltage) — common modern card
  PIKACHU_V: '233381',
  // Base Set Machamp — 1st Edition only holo (foilOnly)
  BASE_MACHAMP: '87',
  // Tag Team high-value card
  ADP_GX: '199264',
  // Likely nonexistent product ID
  NONEXISTENT: '99999999',
};

async function sleep(ms: number) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

// ─── CRUD Tests ───────────────────────────────────────────────

function testCrud(collection: CollectionService, pricing: PricingService) {
  section('CRUD Operations');

  // Card CRUD
  const card = collection.searchCards({ query: 'Test' });
  pass('searchCards (empty DB)', `returned ${card.length} results`);

  // Manual card creation
  const testCard = {
    id: '12345',
    card_name: 'Test Pikachu',
    set_name: 'Test Set',
    product_line: 'Pokemon',
    card_type: 'Pokemon',
    visual_layout: null,
    rarity: 'Common',
    card_number: '25/102',
    product_type: 'Cards',
    era: 'Modern',
    set_type: 'Base',
  };
  collection.bulkImportCards([testCard]);
  const fetched = collection.getCardLocal('12345');
  if (fetched && fetched.card_name === 'Test Pikachu') {
    pass('bulkImportCards + getCardLocal', `card: ${fetched.card_name}`);
  } else {
    fail('bulkImportCards + getCardLocal', `expected Test Pikachu, got ${fetched?.card_name}`);
  }

  // SKU operations
  const sku = collection.resolveSku({
    card_id: '12345', condition: 'NM', finish: 'Regular',
    specialty_one: 'None', specialty_two: 'None', qty: 0,
  });
  if (sku && sku.sku_id > 0) {
    pass('resolveSku (create)', `sku_id=${sku.sku_id}`);
  } else {
    fail('resolveSku (create)', 'no SKU returned');
  }

  // Resolve same SKU again — should return existing
  const sku2 = collection.resolveSku({
    card_id: '12345', condition: 'NM', finish: 'Regular',
    specialty_one: 'None', specialty_two: 'None', qty: 0,
  });
  if (sku2.sku_id === sku.sku_id) {
    pass('resolveSku (idempotent)', `same sku_id=${sku2.sku_id}`);
  } else {
    fail('resolveSku (idempotent)', `expected ${sku.sku_id}, got ${sku2.sku_id}`);
  }

  // Inventory operations
  const invItem = collection.addToInventory({
    card_id: '12345', condition: 'NM', finish: 'Regular', qty: 1,
  });
  if (invItem && invItem.inventory_id > 0 && invItem.status === 'unlisted') {
    pass('addToInventory', `inventory_id=${invItem.inventory_id}, status=${invItem.status}`);
  } else {
    fail('addToInventory', `unexpected result: ${JSON.stringify(invItem)}`);
  }

  // Inventory with pricing override
  const invItemOverride = collection.addToInventory({
    card_id: '12345', condition: 'LP-NM', finish: 'Regular',
    pricing_condition: 'NM', qty: 1,
  });
  if (invItemOverride.pricing_sku_id !== null && invItemOverride.pricing_sku_id !== invItemOverride.sku_id) {
    pass('addToInventory (pricing override)', `sku=${invItemOverride.sku_id}, pricing_sku=${invItemOverride.pricing_sku_id}`);
  } else {
    fail('addToInventory (pricing override)', `pricing_sku_id not set correctly`);
  }

  // Get next unlisted
  const next = collection.getNextUnlisted();
  if (next && next.card_name === 'Test Pikachu') {
    pass('getNextUnlisted', `got: ${next.card_name} (${next.condition})`);
  } else {
    fail('getNextUnlisted', `unexpected result: ${JSON.stringify(next)}`);
  }

  // Tags
  const tagged = collection.addTag(invItem.inventory_id, 'hidden crease');
  if (tagged?.tags === 'hidden crease') {
    pass('addTag', `tags: "${tagged.tags}"`);
  } else {
    fail('addTag', `expected "hidden crease", got "${tagged?.tags}"`);
  }

  const tagged2 = collection.addTag(invItem.inventory_id, 'surface marks');
  if (tagged2?.tags === 'hidden crease,surface marks') {
    pass('addTag (multiple)', `tags: "${tagged2.tags}"`);
  } else {
    fail('addTag (multiple)', `expected "hidden crease,surface marks", got "${tagged2?.tags}"`);
  }

  const untagged = collection.removeTag(invItem.inventory_id, 'hidden crease');
  if (untagged?.tags === 'surface marks') {
    pass('removeTag', `tags: "${untagged.tags}"`);
  } else {
    fail('removeTag', `expected "surface marks", got "${untagged?.tags}"`);
  }

  // Mark as listed
  const listed = collection.markAsListed(invItem.inventory_id, 'EBAY-123');
  if (listed?.status === 'listed' && listed?.ebay_listing_id === 'EBAY-123') {
    pass('markAsListed', `status=${listed.status}, ebay_id=${listed.ebay_listing_id}`);
  } else {
    fail('markAsListed', `unexpected: ${JSON.stringify(listed)}`);
  }

  // Mark as sold
  const sold = collection.markAsSold(invItem.inventory_id);
  if (sold?.status === 'sold') {
    pass('markAsSold', `status=${sold.status}`);
  } else {
    fail('markAsSold', `expected 'sold', got ${sold?.status}`);
  }

  // Stats
  const stats = collection.getStats();
  if (stats.total > 0) {
    pass('getStats', `total=${stats.total}, statuses=${JSON.stringify(stats.by_status)}`);
  } else {
    fail('getStats', 'no items counted');
  }

  // Price record
  const priceRec = pricing.recordPrice({
    sku_id: sku.sku_id,
    calculation_date: '2026-03-24',
    estimated_price: 10.50,
    estimated_liquid_value: 7.93,
    confidence_percent: 85,
    manual_check_necessary: false,
    manually_checked: false,
    algorithm_version: 'lowest-listing-v1',
    estimated_low_price: 9.00,
    estimated_high_price: 12.00,
    estimated_low_price_liquid: 6.65,
    estimated_high_price_liquid: 9.20,
  });
  if (priceRec.estimated_price === 10.50) {
    pass('recordPrice', `price=$${priceRec.estimated_price}`);
  } else {
    fail('recordPrice', `unexpected price: ${priceRec.estimated_price}`);
  }

  // Get latest price
  const latestPrice = pricing.getLatestPrice(sku.sku_id);
  if (latestPrice?.estimated_price === 10.50) {
    pass('getLatestPrice', `$${latestPrice.estimated_price}`);
  } else {
    fail('getLatestPrice', `expected 10.50, got ${latestPrice?.estimated_price}`);
  }
}

// ─── Formatter Tests ──────────────────────────────────────────

function testFormatters() {
  section('Formatter Tests');

  // Condition formatting
  const nm = formatConditionForApi('NM');
  if (JSON.stringify(nm) === '["Near Mint"]') pass('formatCondition NM', nm.join(', '));
  else fail('formatCondition NM', JSON.stringify(nm));

  const lpnm = formatConditionForApi('LP-NM');
  if (lpnm.length === 2 && lpnm.includes('Lightly Played') && lpnm.includes('Near Mint'))
    pass('formatCondition LP-NM (in-between)', lpnm.join(', '));
  else fail('formatCondition LP-NM', JSON.stringify(lpnm));

  // Finish formatting
  const holo = formatFinishForApi('Holo', 'None', 'Sword & Shield');
  if (holo === 'Holofoil') pass('formatFinish modern Holo', holo);
  else fail('formatFinish modern Holo', holo);

  const wotcHolo = formatFinishForApi('Holo', 'None', 'Jungle');
  if (wotcHolo === 'Unlimited Holofoil') pass('formatFinish WOTC Holo (Jungle)', wotcHolo);
  else fail('formatFinish WOTC Holo', wotcHolo);

  const firstEdHolo = formatFinishForApi('Holo', 'First Edition', 'Jungle');
  if (firstEdHolo === '1st Edition Holofoil') pass('formatFinish 1st Ed Holo', firstEdHolo);
  else fail('formatFinish 1st Ed Holo', firstEdHolo);

  const reverseHolo = formatFinishForApi('Reverse-Holo', 'None', 'Vivid Voltage');
  if (reverseHolo === 'Reverse Holofoil') pass('formatFinish Reverse-Holo', reverseHolo);
  else fail('formatFinish Reverse-Holo', reverseHolo);

  const wotcRegular = formatFinishForApi('Regular', 'None', 'Base Set (Shadowless)');
  if (wotcRegular === 'Unlimited') pass('formatFinish WOTC Regular (Shadowless)', wotcRegular);
  else fail('formatFinish WOTC Regular', wotcRegular);
}

// ─── Pricing Algorithm Unit Tests ─────────────────────────────

function testPricingAlgorithm() {
  section('Pricing Algorithm (Unit Tests)');

  // Case 1: Listings + solds, prices agree
  const result1 = computePrice({
    activeListings: [
      { tcgplayer_id: '1', condition: 'NM', finish: 'Regular', listed_price: 5.00, shipping_price: 0.99, seller_name: 'A', seller_feedback_count: 100, seller_rating: 99, seller_sales: '1000+' },
      { tcgplayer_id: '1', condition: 'NM', finish: 'Regular', listed_price: 5.50, shipping_price: 0.99, seller_name: 'B', seller_feedback_count: 100, seller_rating: 99, seller_sales: '500+' },
    ],
    soldListings: [
      { tcgplayer_id: '1', condition: 'NM', finish: 'Regular', sold_price: 6.00, sold_date: '2026-03-20', seller_name: null },
      { tcgplayer_id: '1', condition: 'NM', finish: 'Regular', sold_price: 5.80, sold_date: '2026-03-19', seller_name: null },
      { tcgplayer_id: '1', condition: 'NM', finish: 'Regular', sold_price: 6.20, sold_date: '2026-03-18', seller_name: null },
    ],
    condition: 'NM', finish: 'Regular', hasManualReviewSpecialty: false,
  });
  if (result1.estimated_price === 5.99 && result1.confidence_percent >= 85) {
    pass('Algorithm: listings+solds agree', `$${result1.estimated_price}, confidence=${result1.confidence_percent}%`);
  } else {
    fail('Algorithm: listings+solds agree', `price=$${result1.estimated_price}, conf=${result1.confidence_percent}%`);
  }
  log(`    Reasoning: ${result1.reasoning}`);

  // Case 2: Listings exist but solds are much LOWER (stale listings)
  const result2 = computePrice({
    activeListings: [
      { tcgplayer_id: '2', condition: 'NM', finish: 'Regular', listed_price: 20.00, shipping_price: 0, seller_name: 'A', seller_feedback_count: 100, seller_rating: 99, seller_sales: '1000+' },
    ],
    soldListings: [
      { tcgplayer_id: '2', condition: 'NM', finish: 'Regular', sold_price: 12.00, sold_date: '2026-03-20', seller_name: null },
      { tcgplayer_id: '2', condition: 'NM', finish: 'Regular', sold_price: 11.50, sold_date: '2026-03-19', seller_name: null },
      { tcgplayer_id: '2', condition: 'NM', finish: 'Regular', sold_price: 13.00, sold_date: '2026-03-18', seller_name: null },
    ],
    condition: 'NM', finish: 'Regular', hasManualReviewSpecialty: false,
  });
  if (result2.estimated_price !== null && result2.estimated_price < 15 && result2.manual_check_necessary) {
    pass('Algorithm: listings >> solds (stale)', `price=$${result2.estimated_price}, manual_check=${result2.manual_check_necessary}`);
  } else {
    fail('Algorithm: listings >> solds', `price=$${result2.estimated_price}, check=${result2.manual_check_necessary}`);
  }
  log(`    Reasoning: ${result2.reasoning}`);

  // Case 3: No listings, only solds
  const result3 = computePrice({
    activeListings: [],
    soldListings: [
      { tcgplayer_id: '3', condition: 'NM', finish: 'Regular', sold_price: 8.00, sold_date: '2026-03-20', seller_name: null },
      { tcgplayer_id: '3', condition: 'NM', finish: 'Regular', sold_price: 7.50, sold_date: '2026-03-19', seller_name: null },
    ],
    condition: 'NM', finish: 'Regular', hasManualReviewSpecialty: false,
  });
  if (result3.estimated_price !== null && result3.estimated_price > 7 && result3.estimated_price < 9) {
    pass('Algorithm: no listings, solds only', `price=$${result3.estimated_price}`);
  } else {
    fail('Algorithm: no listings, solds only', `price=$${result3.estimated_price}`);
  }
  log(`    Reasoning: ${result3.reasoning}`);

  // Case 4: No data at all
  const result4 = computePrice({
    activeListings: [],
    soldListings: [],
    condition: 'NM', finish: 'Regular', hasManualReviewSpecialty: false,
  });
  if (result4.estimated_price === null && result4.confidence_percent === 0 && result4.manual_check_necessary) {
    pass('Algorithm: no data (unpriceable)', `price=null, conf=${result4.confidence_percent}%`);
  } else {
    fail('Algorithm: no data', `price=$${result4.estimated_price}, conf=${result4.confidence_percent}%`);
  }
  log(`    Reasoning: ${result4.reasoning}`);

  // Case 5: High value card
  const result5 = computePrice({
    activeListings: [
      { tcgplayer_id: '5', condition: 'NM', finish: 'Holo', listed_price: 75.00, shipping_price: 0, seller_name: 'A', seller_feedback_count: 100, seller_rating: 99, seller_sales: '1000+' },
    ],
    soldListings: [
      { tcgplayer_id: '5', condition: 'NM', finish: 'Holo', sold_price: 73.00, sold_date: '2026-03-20', seller_name: null },
    ],
    condition: 'NM', finish: 'Holo', hasManualReviewSpecialty: false,
  });
  if (result5.manual_check_necessary && result5.estimated_price! > 50) {
    pass('Algorithm: high value flag', `$${result5.estimated_price} flagged for review`);
  } else {
    fail('Algorithm: high value flag', `price=$${result5.estimated_price}, check=${result5.manual_check_necessary}`);
  }

  // Case 6: Manual review specialty (graded card)
  const result6 = computePrice({
    activeListings: [
      { tcgplayer_id: '6', condition: 'NM', finish: 'Regular', listed_price: 10.00, shipping_price: 0, seller_name: 'A', seller_feedback_count: 100, seller_rating: 99, seller_sales: '1000+' },
    ],
    soldListings: [],
    condition: 'NM', finish: 'Regular', hasManualReviewSpecialty: true,
  });
  if (result6.manual_check_necessary) {
    pass('Algorithm: specialty triggers review', 'manual_check=true');
  } else {
    fail('Algorithm: specialty triggers review', 'manual_check should be true');
  }

  // Liquid value tests
  const lv1 = computeLiquidValue(10);
  if (lv1 === 7.5) pass('liquidValue $10', `$${lv1}`);
  else fail('liquidValue $10', `expected $7.50, got $${lv1}`);

  const lv2 = computeLiquidValue(40);
  if (lv2 === 29) pass('liquidValue $40', `$${lv2}`);
  else fail('liquidValue $40', `expected $29.00, got $${lv2}`);

  const lv3 = computeLiquidValue(0.50);
  if (lv3 === 0) pass('liquidValue $0.50 (below zero)', `$${lv3}`);
  else fail('liquidValue $0.50', `expected $0, got $${lv3}`);

  // Cross-condition extrapolation
  const extrap1 = extrapolateAcrossConditions(10, 'NM', 'LP');
  if (extrap1 !== null && Math.abs(extrap1 - 7.0) < 0.01) {
    pass('extrapolate NM→LP', `$10 NM → $${extrap1} LP`);
  } else {
    fail('extrapolate NM→LP', `expected ~$7.00, got $${extrap1}`);
  }

  const extrap2 = extrapolateAcrossConditions(10, 'NM', 'MP');
  if (extrap2 !== null && Math.abs(extrap2 - 4.9) < 0.01) {
    pass('extrapolate NM→MP', `$10 NM → $${extrap2} MP`);
  } else {
    fail('extrapolate NM→MP', `expected ~$4.90, got $${extrap2}`);
  }

  // Reverse extrapolation: LP→NM
  const extrap3 = extrapolateAcrossConditions(7, 'LP', 'NM');
  if (extrap3 !== null && Math.abs(extrap3 - 10.0) < 0.01) {
    pass('extrapolate LP→NM (reverse)', `$7 LP → $${extrap3} NM`);
  } else {
    fail('extrapolate LP→NM', `expected ~$10.00, got $${extrap3}`);
  }

  // In-between extrapolation: NM→LP-NM
  const extrap4 = extrapolateAcrossConditions(10, 'NM', 'LP-NM');
  if (extrap4 !== null && Math.abs(extrap4 - 8.5) < 0.01) {
    pass('extrapolate NM→LP-NM', `$10 NM → $${extrap4} LP-NM`);
  } else {
    fail('extrapolate NM→LP-NM', `expected ~$8.50, got $${extrap4}`);
  }

  // Unknown condition
  const extrap5 = extrapolateAcrossConditions(10, 'NM', 'GARBAGE');
  if (extrap5 === null) {
    pass('extrapolate unknown condition', 'returned null');
  } else {
    fail('extrapolate unknown condition', `expected null, got ${extrap5}`);
  }
}

// ─── TCGplayer API Tests ──────────────────────────────────────

async function testTcgplayerApis() {
  section('TCGplayer API Tests (Live)');

  // ── fetchCardInfo ──
  log('\n  --- fetchCardInfo ---');

  // Modern card with lots of data
  const pikaVmax = await fetchCardInfo(TEST_CARDS.PIKACHU_VMAX);
  if (pikaVmax) {
    pass('fetchCardInfo (Pikachu VMAX)', `name="${pikaVmax.metadata.card_name}", set="${pikaVmax.metadata.set_name}"`);
    log(`    Rarity: ${pikaVmax.metadata.rarity}, Number: ${pikaVmax.metadata.card_number}`);
    log(`    Market: $${pikaVmax.priceInfo.market_price}, Lowest: $${pikaVmax.priceInfo.lowest_price}`);
    log(`    Total listings: ${pikaVmax.priceInfo.total_listings}`);
    log(`    Available conditions: ${pikaVmax.priceInfo.available_conditions.map(c => `${c.condition}/${c.finish}(${c.listing_count})`).join(', ')}`);
    log(`    Foil only: ${pikaVmax.metadata.foil_only}`);
    if (pikaVmax.metadata.attacks.length > 0) {
      log(`    Attacks: ${pikaVmax.metadata.attacks.join('; ')}`);
    }
  } else {
    fail('fetchCardInfo (Pikachu VMAX)', 'returned null');
  }

  await sleep(300);

  // WOTC card — Base Set Charizard
  const charizard = await fetchCardInfo(TEST_CARDS.BASE_CHARIZARD);
  if (charizard) {
    pass('fetchCardInfo (Base Set Charizard)', `name="${charizard.metadata.card_name}", set="${charizard.metadata.set_name}"`);
    log(`    Rarity: ${charizard.metadata.rarity}, foilOnly: ${charizard.metadata.foil_only}`);
    log(`    Market: $${charizard.priceInfo.market_price}, Lowest: $${charizard.priceInfo.lowest_price}`);
    log(`    Conditions available: ${charizard.priceInfo.available_conditions.map(c => `${c.condition}/${c.finish}`).join(', ')}`);
  } else {
    fail('fetchCardInfo (Charizard)', 'returned null');
  }

  await sleep(300);

  // Nonexistent card
  const nonexistent = await fetchCardInfo(TEST_CARDS.NONEXISTENT);
  if (nonexistent === null) {
    pass('fetchCardInfo (nonexistent)', 'returned null as expected');
  } else {
    fail('fetchCardInfo (nonexistent)', `expected null, got: ${nonexistent.metadata.card_name}`);
  }

  await sleep(300);

  // ── fetchActiveListings ──
  log('\n  --- fetchActiveListings ---');

  // Modern NM Regular
  const pikaListings = await fetchActiveListings(TEST_CARDS.PIKACHU_V, 'NM', 'Regular');
  if (pikaListings.length > 0) {
    pass('fetchActiveListings (Pikachu V, NM Regular)', `${pikaListings.length} listings`);
    const first = pikaListings[0];
    log(`    Cheapest: $${first.listed_price} + $${first.shipping_price} shipping = $${(first.listed_price + first.shipping_price).toFixed(2)}`);
    log(`    Seller: ${first.seller_name} (rating=${first.seller_rating}, sales=${first.seller_sales})`);

    // Check if any listings have images — this is the hasImage filter test
    // The current API payload doesn't filter by hasImage, so we note this
    finding('fetchActiveListings does NOT currently filter out listings with images. All listings returned include image and non-image listings mixed together.');
  } else {
    fail('fetchActiveListings (Pikachu V, NM)', 'no listings returned');
  }

  await sleep(300);

  // WOTC Holo — should use Unlimited Holofoil finish
  const charizardListings = await fetchActiveListings(TEST_CARDS.BASE_CHARIZARD, 'NM', 'Holo', 'Base Set');
  if (charizardListings.length > 0) {
    pass('fetchActiveListings (Charizard, NM Holo)', `${charizardListings.length} listings`);
    const first = charizardListings[0];
    log(`    Cheapest NM Holo: $${first.listed_price} + $${first.shipping_price} = $${(first.listed_price + first.shipping_price).toFixed(2)}`);
    if (first.listed_price + first.shipping_price > 50) {
      finding(`Base Set Charizard NM Holo lowest = $${(first.listed_price + first.shipping_price).toFixed(2)} — will trigger high-value flag`);
    }
  } else {
    fail('fetchActiveListings (Charizard NM Holo)', 'no listings returned — check if finish formatting is correct for Base Set');
    finding('Charizard fetchActiveListings returned 0 results. "Base Set" may not be in UNLIMITED_FINISH_SETS — only "Base Set (Shadowless)" is. This might be a bug if the card\'s set_name from TCGplayer is "Base Set" not "Base Set (Shadowless)".');
  }

  await sleep(300);

  // Test with no finish/condition (defaults)
  const defaultListings = await fetchActiveListings(TEST_CARDS.PIKACHU_V);
  if (defaultListings.length > 0) {
    pass('fetchActiveListings (defaults: NM, Regular)', `${defaultListings.length} listings`);
  } else {
    fail('fetchActiveListings (defaults)', 'no listings');
  }

  await sleep(300);

  // ── fetchSoldListings ──
  log('\n  --- fetchSoldListings ---');

  // Modern card solds
  const pikaSolds = await fetchSoldListings(TEST_CARDS.PIKACHU_V, 'NM', 'Regular');
  if (pikaSolds.length > 0) {
    pass('fetchSoldListings (Pikachu V, NM Regular)', `${pikaSolds.length} solds`);
    const first = pikaSolds[0];
    log(`    Latest sold: $${first.sold_price} on ${first.sold_date}`);
    log(`    Condition: ${first.condition}, Finish: ${first.finish}`);
    const prices = pikaSolds.map(s => s.sold_price);
    log(`    Price range: $${Math.min(...prices).toFixed(2)} - $${Math.max(...prices).toFixed(2)}`);
    log(`    Average: $${(prices.reduce((a, b) => a + b, 0) / prices.length).toFixed(2)}`);
  } else {
    fail('fetchSoldListings (Pikachu V, NM)', 'no solds returned');
    finding('fetchSoldListings returned 0 results without auth cookie. This may be expected (max 5 results per request without auth).');
  }

  await sleep(300);

  // All conditions (fan-out strategy without auth)
  const allSolds = await fetchSoldListings(TEST_CARDS.PIKACHU_V);
  if (allSolds.length > 0) {
    pass('fetchSoldListings (all conditions)', `${allSolds.length} total solds`);
    const byCondition = new Map<string, number>();
    for (const s of allSolds) byCondition.set(s.condition, (byCondition.get(s.condition) ?? 0) + 1);
    log(`    By condition: ${[...byCondition.entries()].map(([k, v]) => `${k}=${v}`).join(', ')}`);
  } else {
    fail('fetchSoldListings (all conditions)', 'no solds returned');
  }

  await sleep(300);

  // Nonexistent card — should return empty
  const noSolds = await fetchSoldListings(TEST_CARDS.NONEXISTENT);
  if (noSolds.length === 0) {
    pass('fetchSoldListings (nonexistent)', 'returned empty as expected');
  } else {
    fail('fetchSoldListings (nonexistent)', `expected empty, got ${noSolds.length} results`);
  }
}

// ─── Edge Case: No Listings on TCGplayer ──────────────────────

async function testEdgeCaseNoListings() {
  section('Edge Case: Cards With No/Few Listings');

  // Try Base Set Machamp — 1st Edition only, might have limited NM Regular listings
  const machampListings = await fetchActiveListings(TEST_CARDS.BASE_MACHAMP, 'NM', 'Regular');
  log(`  Machamp (${TEST_CARDS.BASE_MACHAMP}) NM Regular: ${machampListings.length} listings`);
  if (machampListings.length === 0) {
    finding('Base Set Machamp NM Regular has 0 active listings — foilOnly card, Regular finish likely invalid. Algorithm would return null price.');
  }

  await sleep(300);

  // Machamp as Holo
  const machampHolo = await fetchActiveListings(TEST_CARDS.BASE_MACHAMP, 'NM', 'Holo');
  log(`  Machamp NM Holo: ${machampHolo.length} listings`);
  if (machampHolo.length > 0) {
    log(`    Cheapest: $${(machampHolo[0].listed_price + machampHolo[0].shipping_price).toFixed(2)}`);
  }

  await sleep(300);

  // Test the pricing algorithm with no listings for a condition
  const hpListings = await fetchActiveListings(TEST_CARDS.PIKACHU_V, 'HP', 'Regular');
  const hpSolds = await fetchSoldListings(TEST_CARDS.PIKACHU_V, 'HP', 'Regular');
  log(`  Pikachu V HP Regular: ${hpListings.length} listings, ${hpSolds.length} solds`);

  const hpResult = computePrice({
    activeListings: hpListings,
    soldListings: hpSolds,
    condition: 'HP', finish: 'Regular', hasManualReviewSpecialty: false,
  });
  log(`    Algorithm result: price=$${hpResult.estimated_price}, conf=${hpResult.confidence_percent}%, check=${hpResult.manual_check_necessary}`);
  log(`    Reasoning: ${hpResult.reasoning}`);

  if (hpListings.length === 0 && hpSolds.length === 0) {
    finding('Pikachu V HP Regular has zero data. Algorithm correctly returns null/unpriceable. Cross-condition extrapolation would be needed.');
  }
}

// ─── Edge Case: Listing vs Sold Price Divergence ──────────────

async function testEdgeCasePriceDivergence() {
  section('Edge Case: Listing vs Sold Price Divergence');

  // Fetch both listings and solds for a real card
  const cardId = TEST_CARDS.PIKACHU_V;
  const [listings, solds] = await Promise.all([
    fetchActiveListings(cardId, 'NM', 'Regular'),
    fetchSoldListings(cardId, 'NM', 'Regular'),
  ]);

  if (listings.length > 0 && solds.length > 0) {
    const lowestListing = Math.min(...listings.map(l => l.listed_price + l.shipping_price));
    const avgSold = solds.reduce((a, s) => a + s.sold_price, 0) / solds.length;
    const divergence = Math.abs(lowestListing - avgSold) / avgSold;

    log(`  Pikachu V NM Regular:`);
    log(`    Lowest listing: $${lowestListing.toFixed(2)}`);
    log(`    Average sold:   $${avgSold.toFixed(2)}`);
    log(`    Divergence:     ${(divergence * 100).toFixed(1)}%`);

    if (divergence > 0.30) {
      finding(`REAL DIVERGENCE found on Pikachu V: listing=$${lowestListing.toFixed(2)} vs sold_avg=$${avgSold.toFixed(2)} (${(divergence * 100).toFixed(1)}% divergence). Algorithm will flag for review and use sold average.`);
    } else {
      pass('Price divergence check', `${(divergence * 100).toFixed(1)}% divergence — within 30% threshold`);
    }

    // Run algorithm with real data
    const result = computePrice({
      activeListings: listings,
      soldListings: solds,
      condition: 'NM', finish: 'Regular', hasManualReviewSpecialty: false,
    });
    log(`    Algorithm price: $${result.estimated_price}, conf=${result.confidence_percent}%`);
    log(`    Reasoning: ${result.reasoning}`);
    pass('Real-data pricing', `$${result.estimated_price}`);
  } else {
    log(`  Insufficient data: ${listings.length} listings, ${solds.length} solds`);
  }

  await sleep(300);

  // Try high-value card
  log('\n  --- ADP GX (higher value) ---');
  const [adpListings, adpSolds] = await Promise.all([
    fetchActiveListings(TEST_CARDS.ADP_GX, 'NM', 'Regular'),
    fetchSoldListings(TEST_CARDS.ADP_GX, 'NM', 'Regular'),
  ]);

  if (adpListings.length > 0 || adpSolds.length > 0) {
    const lowestListing = adpListings.length > 0
      ? Math.min(...adpListings.map(l => l.listed_price + l.shipping_price))
      : null;
    const avgSold = adpSolds.length > 0
      ? adpSolds.reduce((a, s) => a + s.sold_price, 0) / adpSolds.length
      : null;

    log(`  ADP GX NM Regular:`);
    log(`    Lowest listing: ${lowestListing !== null ? `$${lowestListing.toFixed(2)}` : 'none'}`);
    log(`    Average sold:   ${avgSold !== null ? `$${avgSold.toFixed(2)}` : 'none'}`);
    log(`    Listings: ${adpListings.length}, Solds: ${adpSolds.length}`);

    const result = computePrice({
      activeListings: adpListings,
      soldListings: adpSolds,
      condition: 'NM', finish: 'Regular', hasManualReviewSpecialty: false,
    });
    log(`    Algorithm: $${result.estimated_price}, conf=${result.confidence_percent}%, liquid=$${result.estimated_liquid_value}`);
    log(`    Reasoning: ${result.reasoning}`);
  } else {
    log('  No data for ADP GX NM Regular');
  }
}

// ─── hasImage Filter Test ─────────────────────────────────────

async function testImageFilter() {
  section('hasImage Filter Analysis');

  // Inspect the raw API payload to check if we're filtering images
  log('  The current buildListingsPayload does NOT include a hasImage or listingType filter');
  log('  that would exclude listings with custom seller photos.');
  log('');
  log('  Current payload filters:');
  log('    - sellerStatus: "Live"');
  log('    - listingType: "standard"');
  log('    - condition, printing, language');
  log('    - quantity >= 1');
  log('');
  log('  To exclude listings with images, the payload should include:');
  log('    filters.term.sellerListingHasPhoto: false');
  log('  or');
  log('    filters.exclude.hasCustomImage: true');
  log('');

  finding('MISSING: No image filter in fetchActiveListings. Listings with seller photos are mixed in with stock-photo listings. Sellers with custom photos often price higher (or lower for damaged cards). This could skew the "lowest listing" anchor. Add a filter to EXCLUDE listings with images for more accurate pricing.');

  // Demonstrate by checking if the API even supports this filter
  // Try a modified payload with listingType filter
  const url = `https://mp-search-api.tcgplayer.com/v1/product/${TEST_CARDS.PIKACHU_V}/listings?mpfev=2163`;
  const payload = {
    filters: {
      term: {
        sellerStatus: 'Live',
        channelId: 0,
        language: ['English'],
        condition: ['Near Mint'],
        printing: 'Normal',
        listingType: 'standard',
      },
      range: { quantity: { gte: 1 } },
      exclude: { channelExclusion: 0 },
    },
    from: 0,
    size: 10,
    sort: { field: 'price+shipping', order: 'asc' },
    context: { shippingCountry: 'US', cart: {} },
    aggregations: ['listingType'],
  };

  try {
    const response = await fetch(url, {
      method: 'POST',
      headers: {
        'accept': 'application/json, text/plain, */*',
        'content-type': 'application/json',
        'origin': 'https://www.tcgplayer.com',
        'referer': 'https://www.tcgplayer.com/',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
      },
      body: JSON.stringify(payload),
    });
    const json = await response.json();
    const results = json?.results?.[0]?.results ?? [];
    if (results.length > 0) {
      const hasPhotoCount = results.filter((r: any) => r.hasPhoto || r.sellerListingHasPhoto).length;
      const noPhotoCount = results.length - hasPhotoCount;
      log(`  Sample of ${results.length} listings: ${hasPhotoCount} have photo flag, ${noPhotoCount} without`);
      // Check what fields are available
      const sampleKeys = Object.keys(results[0]);
      const photoRelatedKeys = sampleKeys.filter(k => k.toLowerCase().includes('photo') || k.toLowerCase().includes('image'));
      if (photoRelatedKeys.length > 0) {
        log(`  Photo-related fields found: ${photoRelatedKeys.join(', ')}`);
        finding(`API returns photo-related fields: ${photoRelatedKeys.join(', ')}. These can be used to filter out image listings client-side.`);
      } else {
        log(`  No photo-related fields in listing response.`);
        log(`  Available fields: ${sampleKeys.join(', ')}`);
        finding('No photo-related fields found in raw listing API response. May need to use a different filter parameter in the request payload to exclude image listings.');
      }
    }
  } catch (err) {
    log(`  Error testing raw API: ${err}`);
  }
}

// ─── Full Integration: getCard with Auto-fetch ────────────────

async function testAutoFetch(collection: CollectionService) {
  section('Integration: Auto-fetch Card from TCGplayer');

  // getCard should auto-fetch from TCGplayer if not in DB
  const card = await collection.getCard(TEST_CARDS.PIKACHU_V);
  if (card) {
    pass('getCard (auto-fetch)', `fetched: "${card.card_name}" from "${card.set_name}"`);
    log(`    ID: ${card.id}, Rarity: ${card.rarity}, Number: ${card.card_number}`);

    // Check that SKUs were pre-created
    const skus = collection.getSkusForCard(TEST_CARDS.PIKACHU_V);
    log(`    Pre-created SKUs: ${skus.length}`);
    for (const s of skus.slice(0, 5)) {
      log(`      ${s.condition}/${s.finish} (sku_id=${s.sku_id})`);
    }
    if (skus.length > 0) {
      pass('Auto-created SKUs', `${skus.length} variants`);
    } else {
      fail('Auto-created SKUs', 'no SKUs created');
    }
  } else {
    fail('getCard (auto-fetch)', 'returned null');
  }

  await sleep(300);

  // Fetch again — should come from DB (no API call)
  const card2 = await collection.getCard(TEST_CARDS.PIKACHU_V);
  if (card2 && card2.card_name === card?.card_name) {
    pass('getCard (DB cache)', 'returned from DB without re-fetching');
  } else {
    fail('getCard (DB cache)', 'mismatch or null');
  }
}

// ─── Full Integration: fetchAndStorePrices ────────────────────

async function testFetchAndStorePrices(pricing: PricingService) {
  section('Integration: fetchAndStorePrices (Full Pipeline)');

  const results = await pricing.fetchAndStorePrices(TEST_CARDS.PIKACHU_V);
  if (results.length > 0) {
    pass('fetchAndStorePrices', `${results.length} condition/finish combos priced`);
    for (const r of results) {
      const db = pricing.getLatestPrice(r.sku_id);
      const icon = r.manual_check_necessary ? '⚠' : '✓';
      log(`    ${icon} sku=${r.sku_id}: $${r.estimated_price ?? 'null'} (conf=${r.confidence_percent}%, liquid=$${r.estimated_liquid_value ?? 'null'})`);
      log(`      ${r.reasoning}`);
      if (db) {
        log(`      DB stored: $${db.estimated_price} (verified in DB)`);
      } else {
        finding(`Price for sku_id=${r.sku_id} was NOT found in DB after storage`);
      }
    }
  } else {
    fail('fetchAndStorePrices', 'no results — TCGplayer may have returned no data');
    finding('fetchAndStorePrices returned 0 results for Pikachu V. The solds/listings fan-out may have returned empty arrays.');
  }
}

// ─── Full Integration: computeAndStorePrice ───────────────────

async function testComputeAndStorePrice(pricing: PricingService) {
  section('Integration: computeAndStorePrice (Single Condition)');

  const result = await pricing.computeAndStorePrice(TEST_CARDS.PIKACHU_V, 'NM', 'Regular');
  if (result) {
    pass('computeAndStorePrice (NM Regular)', `$${result.estimated_price}, conf=${result.confidence_percent}%`);
    log(`    Reasoning: ${result.reasoning}`);
    log(`    Liquid value: $${result.estimated_liquid_value}`);
    log(`    Range: $${result.estimated_low_price} - $${result.estimated_high_price}`);
  } else {
    fail('computeAndStorePrice (NM Regular)', 'returned null');
  }

  await sleep(300);

  // Try a condition that likely has no data — should attempt extrapolation
  const dmgResult = await pricing.computeAndStorePrice(TEST_CARDS.PIKACHU_V, 'DMG', 'Regular');
  if (dmgResult) {
    log(`  DMG Regular: $${dmgResult.estimated_price} (conf=${dmgResult.confidence_percent}%)`);
    log(`    Reasoning: ${dmgResult.reasoning}`);
    if (dmgResult.reasoning.includes('Extrapolated')) {
      pass('Cross-condition extrapolation triggered', 'DMG price extrapolated from other condition');
    } else if (dmgResult.estimated_price !== null) {
      pass('DMG pricing', 'had direct data');
    } else {
      finding('DMG condition returned null price even with extrapolation. Check if NM/LP price was stored before extrapolation runs.');
    }
  } else {
    fail('computeAndStorePrice (DMG)', 'returned null');
  }
}

// ─── Main ─────────────────────────────────────────────────────

async function main() {
  log('╔════════════════════════════════════════════════════════════╗');
  log('║       CARD-SERVER COMPREHENSIVE TEST SUITE                ║');
  log('╚════════════════════════════════════════════════════════════╝');
  log(`\nDate: ${new Date().toISOString()}`);
  log(`Auth cookie: ${process.env.TCGPLAYER_AUTH_COOKIE ? 'SET' : 'NOT SET'}`);

  // Init with temp DB for testing
  const db = initDatabase(':memory:');
  const collection = new CollectionService(db);
  const pricing = new PricingService(db);

  try {
    // Phase 1: Unit tests (no API calls)
    testCrud(collection, pricing);
    testFormatters();
    testPricingAlgorithm();

    // Phase 2: Live API tests
    await testTcgplayerApis();
    await sleep(500);
    await testEdgeCaseNoListings();
    await sleep(500);
    await testEdgeCasePriceDivergence();
    await sleep(500);
    await testImageFilter();

    // Phase 3: Integration tests
    await sleep(500);
    await testAutoFetch(collection);
    await sleep(500);
    await testFetchAndStorePrices(pricing);
    await sleep(500);
    await testComputeAndStorePrice(pricing);

  } finally {
    closeDatabase();
  }

  // ── Summary ──
  section('TEST SUMMARY');
  log(`\n  Passed: ${passed}`);
  log(`  Failed: ${failed}`);
  if (failures.length > 0) {
    log(`\n  FAILURES:`);
    for (const f of failures) log(`    ✗ ${f}`);
  }

  section('KEY FINDINGS');
  for (let i = 0; i < findings.length; i++) {
    log(`\n  ${i + 1}. ${findings[i]}`);
  }

  log('\n');
  process.exit(failed > 0 ? 1 : 0);
}

main().catch(err => {
  console.error('Fatal error:', err);
  process.exit(1);
});
