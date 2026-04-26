const BASE = "http://localhost:5000/api";

async function fetchJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

export interface CardItem {
  inventory_id: number;
  sku_id: number;
  qty: number;
  tags: string | null;
  status: string;
  front_photo_path: string | null;
  back_photo_path: string | null;
  ebay_listing_id: string | null;
  condition: string;
  finish: string;
  card_id: string;
  specialty_one: string;
  specialty_two: string;
  card_name: string;
  set_name: string | null;
  rarity: string | null;
  card_number: string | null;
  era: string | null;
  card_type: string | null;
  visual_layout: string | null;
  estimated_price: number | null;
  estimated_liquid_value: number | null;
  confidence_percent: number | null;
  manual_check_necessary: number | null;
  estimated_low_price: number | null;
  estimated_high_price: number | null;
  calculation_date: string | null;
  algorithm_version: string | null;
}

export interface CardsResponse {
  items: CardItem[];
  total: number;
  page: number;
  per_page: number;
  total_pages: number;
}

export interface Filters {
  sets: string[];
  eras: string[];
  rarities: string[];
  conditions: string[];
  finishes: string[];
  statuses: string[];
}

export interface Summary {
  total_inventory: number;
  unique_cards: number;
  total_skus: number;
  total_value: number | null;
  total_liquid_value: number | null;
  avg_price: number | null;
  min_price: number | null;
  max_price: number | null;
  by_status: Record<string, number>;
  pending_manual_checks: number;
  avg_confidence: number | null;
}

export interface HistogramBin {
  range: string;
  count: number;
  total_value: number;
}

export interface ConfidenceBucket {
  bucket: string;
  count: number;
}

export interface EraBreakdown {
  era: string;
  quantity: number;
  total_qty: number;
  total_value: number;
  avg_price: number | null;
  avg_confidence: number | null;
}

export interface ConditionBreakdown {
  condition: string;
  quantity: number;
  avg_price: number | null;
  total_value: number;
}

export interface RarityBreakdown {
  rarity: string;
  quantity: number;
  avg_price: number | null;
  total_value: number;
}

export interface TopCard {
  card_name: string;
  set_name: string | null;
  era: string | null;
  rarity: string | null;
  condition: string;
  finish: string;
  estimated_price: number | null;
  estimated_liquid_value: number | null;
  confidence_percent: number | null;
  inventory_id: number;
  qty: number;
}

export interface SetBreakdown {
  set_name: string;
  quantity: number;
  total_value: number;
  avg_price: number | null;
}

export function fetchCards(params: Record<string, string>): Promise<CardsResponse> {
  const qs = new URLSearchParams(params).toString();
  return fetchJson(`${BASE}/cards?${qs}`);
}

export function fetchFilters(): Promise<Filters> {
  return fetchJson(`${BASE}/filters`);
}

export function fetchSummary(): Promise<Summary> {
  return fetchJson(`${BASE}/analytics/summary`);
}

export function fetchPriceHistogram(breaks: number[]): Promise<HistogramBin[]> {
  return fetchJson(`${BASE}/analytics/price-histogram?breaks=${breaks.join(",")}`);
}

export function fetchConfidenceDistribution(): Promise<ConfidenceBucket[]> {
  return fetchJson(`${BASE}/analytics/confidence-distribution`);
}

export function fetchEraBreakdown(): Promise<EraBreakdown[]> {
  return fetchJson(`${BASE}/analytics/era-breakdown`);
}

export function fetchConditionBreakdown(): Promise<ConditionBreakdown[]> {
  return fetchJson(`${BASE}/analytics/condition-breakdown`);
}

export function fetchRarityBreakdown(): Promise<RarityBreakdown[]> {
  return fetchJson(`${BASE}/analytics/rarity-breakdown`);
}

export function fetchTopCards(n = 25): Promise<TopCard[]> {
  return fetchJson(`${BASE}/analytics/top-cards?n=${n}`);
}

export function fetchSetBreakdown(): Promise<SetBreakdown[]> {
  return fetchJson(`${BASE}/analytics/set-breakdown`);
}

// ── Collection grid ──

export interface CollectionItem extends CardItem {
  product_type: string | null;
  manually_checked: number | null;
  estimated_low_price_liquid: number | null;
  estimated_high_price_liquid: number | null;
  listing_count: number | null;
  lowest_listing_price: number | null;
  median_listing_price: number | null;
  mean_listing_price: number | null;
  p25_listing_price: number | null;
  p75_listing_price: number | null;
  recent_sales_count: number | null;
  avg_sale_price: number | null;
  median_sale_price: number | null;
  min_sale_price: number | null;
  max_sale_price: number | null;
  newest_sale_date: string | null;
  oldest_sale_date: string | null;
  market_snapshot_date: string | null;
  has_image: boolean;
}

export interface CollectionResponse {
  items: CollectionItem[];
  total: number;
  page: number;
  per_page: number;
  total_pages: number;
}

export function fetchCollection(params: Record<string, string>): Promise<CollectionResponse> {
  const qs = new URLSearchParams(params).toString();
  return fetchJson(`${BASE}/collection?${qs}`);
}

export function cardImageUrl(cardId: string): string {
  return `${BASE}/images/${cardId}`;
}
