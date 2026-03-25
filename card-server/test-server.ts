/**
 * Comprehensive card-server test.
 * Tests: DB init, CRUD helpers, pricing algorithm, services, live TCGplayer API.
 *
 * Run: cd card-server && npx tsx test-server.ts
 */

import Database from 'better-sqlite3';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

// ─── Helpers ──────────────────────────────────────────────────
const __dirname = path.dirname(fileURLToPath(import.meta.url));

let passed = 0;
let failed = 0;
const failures: string[] = [];

function assert(condition: boolean, label: string, detail?: string) {
  if (condition) {
    passed++;
    console.log(`  ✓ ${label}`);
  } else {
    failed++;
    const msg = detail ? `${label} — ${detail}` : label;
    failures.push(msg);
    console.log(`  ✗ ${label}${detail ? ` (${detail})` : ''}`);
  }
}

function section(name: string) {
  console.log(`\n── ${name} ──`);
}

// ─── Setup: in-memory DB ──────────────────────────────────────

const schemaPath = path.join(__dirname, 'src', 'schema.sql');
const schema = fs.readFileSync(schemaPath, 'utf-8');
const db = new Database(':memory:');
db.pragma('journal_mode = WAL');
db.pragma('foreign_keys = ON');
db.exec(schema);

// ─── Import modules ───────────────────────────────────────────

const { CardsHelper } = await import('./src/helpers/crud/cards.js');
const { SkusHelper } = await import('./src/helpers/crud/skus.js');
const { InventoryHelper } = await import('./src/helpers/crud/inventory.js');
const { PricesHelper } = await import('./src/helpers/crud/prices.js');
const { computePrice, computeLiquidValue, extrapolateAcrossConditions } = await import('./src/helpers/pricing/algorithm.js');
const { formatConditionForApi, formatFinishForApi } = await import('./src/helpers/tcgplayer/formatters.js');
const { CollectionService } = await import('./src/services/collectionService.js');
const { PricingService } = await import('./src/services/pricingService.js');

// ══════════════════════════════════════════════════════════════
// 1. CRUD HELPERS
// ══════════════════════════════════════════════════════════════

section('CardsHelper');
const cards = new CardsHelper(db);

const card1 = cards.upsert({
  id: '42382',
  card_name: 'Charizard',
  set_name: 'Base Set',
  product_line: 'Pokemon',
  card_type: 'Fire',
  visual_layout: null,
  rarity: 'Rare Holo',
  card_number: '4/102',
  product_type: null,
  era: null,
  set_type: null,
});
assert(card1.id === '42382', 'Card upsert returns correct ID');
assert(card1.card_name === 'Charizard', 'Card name stored correctly');

const card1b = cards.getById('42382');
assert(card1b !== null && card1b.card_name === 'Charizard', 'getById returns stored card');

cards.upsert({
  id: '226432',
  card_name: 'Pikachu VMAX',
  set_name: 'Vivid Voltage',
  product_line: 'Pokemon',
  card_type: 'Lightning',
  visual_layout: null,
  rarity: 'Ultra Rare',
  card_number: '44/185',
  product_type: null,
  era: null,
  set_type: null,
});

const searchByName = cards.search({ query: 'Charizard' });
assert(searchByName.length === 1, 'Search by name finds Charizard');

const searchBySet = cards.search({ set_name: 'Vivid Voltage' });
assert(searchBySet.length === 1 && searchBySet[0].card_name === 'Pikachu VMAX', 'Search by set finds Pikachu VMAX');

const allSets = cards.getAllSetNames();
assert(allSets.length === 2 && allSets.includes('Base Set'), 'getAllSetNames returns both sets');

const allRarities = cards.getAllRarities();
assert(allRarities.length === 2, 'getAllRarities returns both rarities');

// ── SKUs ──

section('SkusHelper');
const skus = new SkusHelper(db);

const sku1 = skus.getOrCreate({
  card_id: '42382',
  condition: 'NM',
  finish: 'Holo',
  specialty_one: 'None',
  specialty_two: 'None',
  qty: 0,
});
assert(sku1.sku_id > 0, 'SKU created with auto-increment ID');
assert(sku1.condition === 'NM', 'SKU condition stored');

const sku1b = skus.getOrCreate({
  card_id: '42382',
  condition: 'NM',
  finish: 'Holo',
  specialty_one: 'None',
  specialty_two: 'None',
  qty: 0,
});
assert(sku1.sku_id === sku1b.sku_id, 'getOrCreate returns same SKU for same combo');

const sku2 = skus.getOrCreate({
  card_id: '42382',
  condition: 'LP',
  finish: 'Holo',
  specialty_one: 'None',
  specialty_two: 'None',
  qty: 0,
});
assert(sku2.sku_id !== sku1.sku_id, 'Different condition creates different SKU');

const cardSkus = skus.getByCardId('42382');
assert(cardSkus.length === 2, 'getByCardId returns both SKUs');

// ── Inventory ──

section('InventoryHelper');
const inventory = new InventoryHelper(db);

const inv1 = inventory.create({
  sku_id: sku1.sku_id,
  pricing_sku_id: null,
  qty: 1,
  tags: null,
  status: 'unlisted',
});
assert(inv1.inventory_id > 0, 'Inventory item created');
assert(inv1.status === 'unlisted', 'Status is unlisted');

const nextUnlisted = inventory.getNextUnlisted();
assert(nextUnlisted !== null && nextUnlisted.inventory_id === inv1.inventory_id, 'getNextUnlisted returns first unlisted');

const detail = inventory.getDetailById(inv1.inventory_id);
assert(detail !== null, 'getDetailById returns detail');
assert(detail!.card_name === 'Charizard', 'Detail includes card name');

const listed = inventory.markAsListed(inv1.inventory_id, 'ebay-123');
assert(listed !== null && listed.status === 'listed', 'markAsListed changes status');
assert(listed!.ebay_listing_id === 'ebay-123', 'eBay listing ID stored');

const noMoreUnlisted = inventory.getNextUnlisted();
assert(noMoreUnlisted === null, 'No unlisted items after marking');

const sold = inventory.markAsSold(inv1.inventory_id);
assert(sold !== null && sold.status === 'sold', 'markAsSold changes status');

// Tags
const inv2 = inventory.create({ sku_id: sku1.sku_id, pricing_sku_id: null, qty: 1, tags: null, status: 'unlisted' });
const tagged = inventory.addTag(inv2.inventory_id, 'hidden crease');
assert(tagged !== null && tagged.tags === 'hidden crease', 'addTag works');
const tagged2 = inventory.addTag(inv2.inventory_id, 'surface marks');
assert(tagged2!.tags === 'hidden crease,surface marks', 'Multiple tags concatenated');
const untagged = inventory.removeTag(inv2.inventory_id, 'hidden crease');
assert(untagged!.tags === 'surface marks', 'removeTag removes only target tag');

// Stats
const stats = inventory.getStats();
assert(stats.total === 2, 'Stats total count correct');

// Pricing SKU override
const inv3 = inventory.create({ sku_id: sku2.sku_id, pricing_sku_id: null, qty: 1, tags: null, status: 'unlisted' });
const overridden = inventory.setPricingSku(inv3.inventory_id, sku1.sku_id);
assert(overridden!.pricing_sku_id === sku1.sku_id, 'Pricing SKU override stored');

// ── Prices ──

section('PricesHelper');
const prices = new PricesHelper(db);

const price1 = prices.upsert({
  sku_id: sku1.sku_id,
  calculation_date: '2025-01-01',
  estimated_price: 650.00,
  estimated_liquid_value: 547.50,
  confidence_percent: 85,
  manual_check_necessary: true,
  manually_checked: false,
  algorithm_version: 'lowest-listing-v1',
  estimated_low_price: 580.00,
  estimated_high_price: 750.00,
  estimated_low_price_liquid: 488.00,
  estimated_high_price_liquid: 632.50,
});
assert(price1.sku_id === sku1.sku_id, 'Price upserted for SKU');
assert(price1.estimated_price === 650, 'Price amount correct');

const latest = prices.getLatest(sku1.sku_id);
assert(latest !== null && latest.estimated_price === 650, 'getLatest returns correct price');

// Upsert same date updates
const price1b = prices.upsert({
  sku_id: sku1.sku_id,
  calculation_date: '2025-01-01',
  estimated_price: 660.00,
  estimated_liquid_value: 556.00,
  confidence_percent: 90,
  manual_check_necessary: true,
  manually_checked: false,
  algorithm_version: 'lowest-listing-v1',
  estimated_low_price: 580.00,
  estimated_high_price: 750.00,
  estimated_low_price_liquid: 488.00,
  estimated_high_price_liquid: 632.50,
});
assert(price1b.estimated_price === 660, 'Upsert same date updates price');

const history = prices.getHistory(sku1.sku_id);
assert(history.length === 1, 'Upsert does not duplicate — 1 record for same date');

// Manual check
const pending = prices.getPendingManualChecks();
assert(pending.length >= 1, 'getPendingManualChecks returns flagged prices');
const checked = prices.markAsChecked(sku1.sku_id, '2025-01-01');
assert(checked, 'markAsChecked returns true');

// Price for inventory item with pricing override
prices.upsert({
  sku_id: sku1.sku_id,
  calculation_date: '2025-01-02',
  estimated_price: 700.00,
  estimated_liquid_value: 590.00,
  confidence_percent: 90,
  manual_check_necessary: false,
  manually_checked: false,
  algorithm_version: 'lowest-listing-v1',
  estimated_low_price: null,
  estimated_high_price: null,
  estimated_low_price_liquid: null,
  estimated_high_price_liquid: null,
});
const priceForOverride = prices.getLatestForInventoryItem(inv3.inventory_id);
assert(priceForOverride !== null && priceForOverride.estimated_price === 700, 'getLatestForInventoryItem respects pricing_sku_id override');

// ══════════════════════════════════════════════════════════════
// 2. FORMATTERS
// ══════════════════════════════════════════════════════════════

section('Formatters');

assert(JSON.stringify(formatConditionForApi('NM')) === '["Near Mint"]', 'NM → Near Mint');
assert(JSON.stringify(formatConditionForApi('LP')) === '["Lightly Played"]', 'LP → Lightly Played');
assert(JSON.stringify(formatConditionForApi('DMG')) === '["Damaged"]', 'DMG → Damaged');

assert(formatFinishForApi('Holo', 'None', 'Vivid Voltage') === 'Holofoil', 'Holo → Holofoil');
assert(formatFinishForApi('Regular', 'None', '') === 'Normal', 'Regular → Normal');
assert(formatFinishForApi('Reverse-Holo', 'None', '') === 'Reverse Holofoil', 'Reverse-Holo → Reverse Holofoil');
assert(formatFinishForApi('Holo', 'First Edition', '') === '1st Edition Holofoil', '1st Ed Holo → 1st Edition Holofoil');

// WOTC sets
assert(formatFinishForApi('Holo', 'None', 'Base Set (Shadowless)') === 'Unlimited Holofoil', 'WOTC Holo → Unlimited Holofoil');
assert(formatFinishForApi('Regular', 'None', 'Jungle') === 'Unlimited', 'WOTC Regular → Unlimited');

// ══════════════════════════════════════════════════════════════
// 3. PRICING ALGORITHM
// ══════════════════════════════════════════════════════════════

section('Pricing Algorithm — computePrice');

// Case 1: Listings + solds agree
const result1 = computePrice({
  activeListings: [
    { tcgplayer_id: '1', condition: 'NM', finish: 'Holo', listed_price: 10.00, shipping_price: 0, seller_name: null, seller_feedback_count: null, seller_rating: 99, seller_sales: '1000+' },
    { tcgplayer_id: '1', condition: 'NM', finish: 'Holo', listed_price: 11.00, shipping_price: 0, seller_name: null, seller_feedback_count: null, seller_rating: 99, seller_sales: '1000+' },
  ],
  soldListings: [
    { tcgplayer_id: '1', condition: 'NM', finish: 'Holo', sold_price: 9.50, sold_date: '2025-01-01', seller_name: null },
    { tcgplayer_id: '1', condition: 'NM', finish: 'Holo', sold_price: 10.50, sold_date: '2025-01-02', seller_name: null },
    { tcgplayer_id: '1', condition: 'NM', finish: 'Holo', sold_price: 10.00, sold_date: '2025-01-03', seller_name: null },
  ],
  condition: 'NM',
  finish: 'Holo',
  hasManualReviewSpecialty: false,
});
assert(result1.estimated_price === 10.00, 'Listings+solds agree → uses lowest listing $10',
  `got $${result1.estimated_price}`);
assert(result1.confidence_percent >= 85, 'High confidence when data agrees',
  `got ${result1.confidence_percent}%`);
assert(!result1.manual_check_necessary, 'No manual check for confident price');

// Case 2: No listings, only solds
const result2 = computePrice({
  activeListings: [],
  soldListings: [
    { tcgplayer_id: '1', condition: 'NM', finish: 'Holo', sold_price: 8.00, sold_date: '2025-01-01', seller_name: null },
    { tcgplayer_id: '1', condition: 'NM', finish: 'Holo', sold_price: 12.00, sold_date: '2025-01-02', seller_name: null },
    { tcgplayer_id: '1', condition: 'NM', finish: 'Holo', sold_price: 10.00, sold_date: '2025-01-03', seller_name: null },
  ],
  condition: 'NM',
  finish: 'Holo',
  hasManualReviewSpecialty: false,
});
assert(result2.estimated_price === 10.00, 'No listings → uses sold average $10', `got $${result2.estimated_price}`);
assert(result2.confidence_percent < 70, 'Lower confidence with no listings');

// Case 3: No data at all
const result3 = computePrice({
  activeListings: [],
  soldListings: [],
  condition: 'NM',
  finish: 'Holo',
  hasManualReviewSpecialty: false,
});
assert(result3.estimated_price === null, 'No data → null price');
assert(result3.confidence_percent === 0, 'Zero confidence');
assert(result3.manual_check_necessary, 'Flagged for manual review');

// Case 4: High value → manual review
const result4 = computePrice({
  activeListings: [
    { tcgplayer_id: '1', condition: 'NM', finish: 'Holo', listed_price: 100.00, shipping_price: 5, seller_name: null, seller_feedback_count: null, seller_rating: 99, seller_sales: '1000+' },
  ],
  soldListings: [
    { tcgplayer_id: '1', condition: 'NM', finish: 'Holo', sold_price: 95.00, sold_date: '2025-01-01', seller_name: null },
    { tcgplayer_id: '1', condition: 'NM', finish: 'Holo', sold_price: 105.00, sold_date: '2025-01-02', seller_name: null },
    { tcgplayer_id: '1', condition: 'NM', finish: 'Holo', sold_price: 100.00, sold_date: '2025-01-03', seller_name: null },
  ],
  condition: 'NM',
  finish: 'Holo',
  hasManualReviewSpecialty: false,
});
assert(result4.estimated_price === 105.00, 'High value card uses listing+shipping $105', `got $${result4.estimated_price}`);
assert(result4.manual_check_necessary, 'High value flagged for review');

// Case 5: Cheap card (<$5) — listings win despite divergence
const result5 = computePrice({
  activeListings: [
    { tcgplayer_id: '1', condition: 'NM', finish: 'Regular', listed_price: 0.25, shipping_price: 1.00, seller_name: null, seller_feedback_count: null, seller_rating: 99, seller_sales: '1000+' },
  ],
  soldListings: [
    { tcgplayer_id: '1', condition: 'NM', finish: 'Regular', sold_price: 1.25, sold_date: '2025-01-01', seller_name: null },
    { tcgplayer_id: '1', condition: 'NM', finish: 'Regular', sold_price: 1.15, sold_date: '2025-01-02', seller_name: null },
    { tcgplayer_id: '1', condition: 'NM', finish: 'Regular', sold_price: 0.25, sold_date: '2025-01-03', seller_name: null },
  ],
  condition: 'NM',
  finish: 'Regular',
  hasManualReviewSpecialty: false,
});
assert(result5.estimated_price === 0.25, 'Cheap card uses listed_price only (ignores $1 shipping)', `got $${result5.estimated_price}`);
assert(!result5.manual_check_necessary, 'Cheap card NOT flagged despite sold divergence');

// Case 6: Divergence — solds much lower → switch to sold avg
const result6 = computePrice({
  activeListings: [
    { tcgplayer_id: '1', condition: 'NM', finish: 'Holo', listed_price: 20.00, shipping_price: 1.00, seller_name: null, seller_feedback_count: null, seller_rating: 99, seller_sales: '1000+' },
  ],
  soldListings: [
    { tcgplayer_id: '1', condition: 'NM', finish: 'Holo', sold_price: 12.00, sold_date: '2025-01-01', seller_name: null },
    { tcgplayer_id: '1', condition: 'NM', finish: 'Holo', sold_price: 13.00, sold_date: '2025-01-02', seller_name: null },
    { tcgplayer_id: '1', condition: 'NM', finish: 'Holo', sold_price: 14.00, sold_date: '2025-01-03', seller_name: null },
  ],
  condition: 'NM',
  finish: 'Holo',
  hasManualReviewSpecialty: false,
});
assert(result6.estimated_price === 13.00, 'Solds much lower than listings → uses sold avg $13', `got $${result6.estimated_price}`);
assert(result6.manual_check_necessary, 'Divergence flagged for review');

// Case 7: Specialty → manual review
const result7 = computePrice({
  activeListings: [
    { tcgplayer_id: '1', condition: 'NM', finish: 'Holo', listed_price: 10.00, shipping_price: 0, seller_name: null, seller_feedback_count: null, seller_rating: 99, seller_sales: '1000+' },
  ],
  soldListings: [],
  condition: 'NM',
  finish: 'Holo',
  hasManualReviewSpecialty: true,
});
assert(result7.manual_check_necessary, 'Manual review specialty → flagged');

section('Pricing Algorithm — computeLiquidValue');

assert(computeLiquidValue(2.00) === 1.70, 'Cheap card: $2 → $1.70 (no shipping)', `got ${computeLiquidValue(2.00)}`);
assert(computeLiquidValue(10.00) === 7.50, '$10 card → $7.50 ($1 PWE shipping)', `got ${computeLiquidValue(10.00)}`);
assert(computeLiquidValue(40.00) === 29.00, '$40 card → $29.00 ($5 tracked shipping)', `got ${computeLiquidValue(40.00)}`);
assert(computeLiquidValue(0.05) === 0.04, '5 cent card → $0.04', `got ${computeLiquidValue(0.05)}`);

section('Pricing Algorithm — extrapolateAcrossConditions');

assert(extrapolateAcrossConditions(10.00, 'NM', 'LP') === 7.00, 'NM $10 → LP $7.00', `got ${extrapolateAcrossConditions(10.00, 'NM', 'LP')}`);
assert(extrapolateAcrossConditions(10.00, 'NM', 'MP') === 4.90, 'NM $10 → MP $4.90', `got ${extrapolateAcrossConditions(10.00, 'NM', 'MP')}`);
assert(extrapolateAcrossConditions(10.00, 'NM', 'HP') === 3.43, 'NM $10 → HP $3.43', `got ${extrapolateAcrossConditions(10.00, 'NM', 'HP')}`);
assert(extrapolateAcrossConditions(7.00, 'LP', 'NM') === 10.00, 'LP $7.00 → NM $10.00 (reverse)', `got ${extrapolateAcrossConditions(7.00, 'LP', 'NM')}`);

// In-between
const lpNm = extrapolateAcrossConditions(10.00, 'NM', 'LP-NM');
assert(lpNm !== null && Math.abs(lpNm - 8.50) < 0.02, 'NM $10 → LP-NM ≈ $8.50', `got ${lpNm}`);

assert(extrapolateAcrossConditions(10.00, 'NM', 'NM') === 10.00, 'Same condition → same price');
assert(extrapolateAcrossConditions(10.00, 'NM', 'GARBAGE') === null, 'Unknown condition → null');

// ══════════════════════════════════════════════════════════════
// 4. SERVICES (using in-memory DB)
// ══════════════════════════════════════════════════════════════

section('CollectionService');

const collectionSvc = new CollectionService(db);
const pricingSvc = new PricingService(db);

// Local operations (no network)
const localCard = collectionSvc.getCardLocal('42382');
assert(localCard !== null && localCard.card_name === 'Charizard', 'getCardLocal finds existing card');

const addedItem = collectionSvc.addToInventory({
  card_id: '42382',
  condition: 'NM',
  finish: 'Holo',
  qty: 2,
  tags: 'test-card',
});
assert(addedItem.qty === 2, 'addToInventory sets qty');
assert(addedItem.tags === 'test-card', 'addToInventory stores tags');

const invDetail = collectionSvc.getInventoryDetail(addedItem.inventory_id);
assert(invDetail !== null && invDetail.card_name === 'Charizard', 'getInventoryDetail joins card data');

const svcStats = collectionSvc.getStats();
assert(svcStats.total > 0, 'getStats returns non-zero total');

// Pricing service — local operations
const latestPrice = pricingSvc.getLatestPrice(sku1.sku_id);
assert(latestPrice !== null, 'PricingService.getLatestPrice finds stored price');

const priceHistory = pricingSvc.getPriceHistory(sku1.sku_id);
assert(priceHistory.length > 0, 'PricingService.getPriceHistory returns records');

// ══════════════════════════════════════════════════════════════
// 5. LIVE TCGPLAYER API TESTS
// ══════════════════════════════════════════════════════════════

section('Live TCGplayer API — fetchCardInfo');

const { fetchCardInfo } = await import('./src/helpers/tcgplayer/fetchCardInfo.js');

// Pikachu VMAX (226432) — well-known modern card
const pikaInfo = await fetchCardInfo('226432');
assert(pikaInfo !== null, 'fetchCardInfo(226432) returns data');
if (pikaInfo) {
  assert(pikaInfo.metadata.card_name.includes('Pikachu'), `Card name contains "Pikachu": "${pikaInfo.metadata.card_name}"`);
  assert(pikaInfo.metadata.set_name !== null && pikaInfo.metadata.set_name.includes('Vivid Voltage'), `Set contains "Vivid Voltage": "${pikaInfo.metadata.set_name}"`);
  assert(pikaInfo.metadata.rarity !== null, `Rarity present: "${pikaInfo.metadata.rarity}"`);
  assert(pikaInfo.priceInfo.total_listings !== null && pikaInfo.priceInfo.total_listings > 0, `Has listings: ${pikaInfo.priceInfo.total_listings}`);
  assert(pikaInfo.priceInfo.available_conditions.length > 0, `Has available conditions: ${pikaInfo.priceInfo.available_conditions.length}`);
}

// Invalid ID
const invalidCard = await fetchCardInfo('999999999');
assert(invalidCard === null, 'fetchCardInfo with invalid ID returns null');

section('Live TCGplayer API — fetchActiveListings');

const { fetchActiveListings } = await import('./src/helpers/tcgplayer/fetchPrices.js');

// Pikachu VMAX NM Holo listings
const pikaListings = await fetchActiveListings('226432', 'NM', 'Holo', 'Vivid Voltage');
assert(pikaListings.length > 0, `fetchActiveListings returns listings: ${pikaListings.length}`);
if (pikaListings.length > 0) {
  const first = pikaListings[0];
  assert(first.listed_price > 0, `First listing has price: $${first.listed_price}`);
  assert(first.seller_rating !== null && first.seller_rating >= 80, `Seller rating filtered ≥80: ${first.seller_rating}`);
  assert(first.condition === 'NM', 'Listing condition matches request');

  // Verify sorted by price
  let sorted = true;
  for (let i = 1; i < pikaListings.length; i++) {
    if (pikaListings[i].listed_price + pikaListings[i].shipping_price < pikaListings[i-1].listed_price + pikaListings[i-1].shipping_price - 0.01) {
      sorted = false;
      break;
    }
  }
  assert(sorted, 'Listings sorted by price ascending');
}

section('Live TCGplayer API — fetchSoldListings');

const { fetchSoldListings } = await import('./src/helpers/tcgplayer/fetchPrices.js');

const pikaSolds = await fetchSoldListings('226432', 'NM', 'Holo');
assert(pikaSolds.length > 0, `fetchSoldListings returns solds: ${pikaSolds.length}`);
if (pikaSolds.length > 0) {
  const first = pikaSolds[0];
  assert(first.sold_price > 0, `First sold has price: $${first.sold_price}`);
  assert(first.sold_date !== '', 'Sold has date');
  assert(first.condition === 'NM', `Sold condition: ${first.condition}`);
}

// All conditions (no filter)
const allSolds = await fetchSoldListings('226432');
assert(allSolds.length >= pikaSolds.length, `All-condition solds (${allSolds.length}) ≥ NM-only (${pikaSolds.length})`);

section('Live TCGplayer API — fetchSetInfo');

const { fetchSetInfo } = await import('./src/helpers/tcgplayer/fetchPrices.js');

const setInfo = await fetchSetInfo(604); // Base Set
assert(setInfo !== null, 'fetchSetInfo(604) returns data');
if (setInfo) {
  assert(setInfo.name === 'Base Set', `Set name: "${setInfo.name}"`);
  assert(setInfo.abbreviation !== '', `Has abbreviation: "${setInfo.abbreviation}"`);
}

// ══════════════════════════════════════════════════════════════
// 6. FULL INTEGRATION: fetch + price + store
// ══════════════════════════════════════════════════════════════

section('Integration — PricingService.fetchAndStorePrices (live)');

// Use a fresh in-memory DB service connected to our test DB
const integPricing = new PricingService(db);

// Pikachu VMAX — fetch all conditions, run algorithm, store
const fetchedPrices = await integPricing.fetchAndStorePrices('226432');
assert(fetchedPrices.length > 0, `fetchAndStorePrices returned ${fetchedPrices.length} condition/finish combos`);

for (const p of fetchedPrices) {
  assert(p.algorithm_version === 'lowest-listing-v1', `Algorithm version correct`);
  assert(p.reasoning.length > 0, `Has reasoning string`);
  if (p.estimated_price !== null) {
    assert(p.estimated_price > 0, `Price > 0: $${p.estimated_price}`);
    assert(p.confidence_percent > 0, `Confidence > 0: ${p.confidence_percent}%`);
  }
}

// Verify card was auto-created in DB
const storedCard = cards.getById('226432');
assert(storedCard !== null, 'Card auto-created in DB by ensureCardExists');
if (storedCard) {
  assert(storedCard.card_name.includes('Pikachu'), `Stored card name: "${storedCard.card_name}"`);
}

// Verify SKUs were created
const storedSkus = skus.search({ card_id: '226432' });
assert(storedSkus.length > 0, `SKUs created: ${storedSkus.length}`);

// Verify prices are retrievable from DB
for (const sku of storedSkus) {
  const storedPrice = prices.getLatest(sku.sku_id);
  if (storedPrice) {
    assert(storedPrice.estimated_price !== null || storedPrice.confidence_percent === 0,
      `Stored price for ${sku.condition}/${sku.finish}: $${storedPrice.estimated_price} @ ${storedPrice.confidence_percent}%`);
  }
}

section('Integration — PricingService.computeAndStorePrice (live, single condition)');

const singlePrice = await integPricing.computeAndStorePrice('226432', 'NM', 'Holo');
assert(singlePrice !== null, 'computeAndStorePrice returns result');
if (singlePrice) {
  assert(singlePrice.estimated_price !== null && singlePrice.estimated_price > 0,
    `NM Holo price: $${singlePrice.estimated_price}`);
  console.log(`    Reasoning: ${singlePrice.reasoning}`);
}

// ══════════════════════════════════════════════════════════════
// 7. EDGE CASE: Card with no listings (very old/rare)
// ══════════════════════════════════════════════════════════════

section('Edge Case — Rare/old card');

// Try a very niche card that might have no data
const edgeListings = await fetchActiveListings('42382', 'DMG', 'Holo', 'Base Set');
console.log(`  Charizard Base Set DMG Holo listings: ${edgeListings.length}`);

const edgeSolds = await fetchSoldListings('42382', 'DMG', 'Holo');
console.log(`  Charizard Base Set DMG Holo solds: ${edgeSolds.length}`);

const edgeResult = computePrice({
  activeListings: edgeListings,
  soldListings: edgeSolds,
  condition: 'DMG',
  finish: 'Holo',
  hasManualReviewSpecialty: false,
});
console.log(`  Algorithm result: $${edgeResult.estimated_price} @ ${edgeResult.confidence_percent}% confidence`);
console.log(`  Manual check: ${edgeResult.manual_check_necessary}`);
console.log(`  Reasoning: ${edgeResult.reasoning}`);
assert(edgeResult.algorithm_version === 'lowest-listing-v1', 'Edge case still returns algorithm version');

// ══════════════════════════════════════════════════════════════
// RESULTS
// ══════════════════════════════════════════════════════════════

db.close();

console.log(`\n${'═'.repeat(60)}`);
console.log(`RESULTS: ${passed} passed, ${failed} failed`);
if (failures.length > 0) {
  console.log('\nFailures:');
  for (const f of failures) {
    console.log(`  ✗ ${f}`);
  }
}
console.log('═'.repeat(60));

process.exit(failed > 0 ? 1 : 0);
