// Backend origin, configurable per environment. Set API_BASE in the repo-root
// .env locally (read via Vite's envDir), or in the host's env (e.g. Vercel) for
// the deployed site — e.g. "http://localhost:5000" or "https://api.example.com".
// No /api suffix and no trailing slash (trimmed below); the code appends /api.
const API_BASE = (import.meta.env.API_BASE ?? "http://localhost:5000").replace(/\/+$/, "");
const BASE = `${API_BASE}/api`;

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

export function fetchPriceHistogram(
  breaks: number[],
  filters: Record<string, string | string[]> = {},
): Promise<HistogramBin[]> {
  const qs = new URLSearchParams();
  qs.set("breaks", breaks.join(","));
  for (const [key, val] of Object.entries(filters)) {
    if (Array.isArray(val)) val.forEach((v) => v && qs.append(key, v));
    else if (val) qs.set(key, val);
  }
  return fetchJson(`${BASE}/analytics/price-histogram?${qs.toString()}`);
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

// A graded slab image by cert number (front by default). 404s until it's been
// downloaded at add time; the graded tile then falls back to the raw card image.
export function gradedImageUrl(certId: string, side: "front" | "back" = "front"): string {
  return side === "back" ? `${BASE}/graded-images/${certId}/back` : `${BASE}/graded-images/${certId}`;
}

// ── Card detail page ──

// The variant axes that, together with card_id, identify a card's page
// (everything except condition). Tagged cards are intentionally excluded
// upstream, so tags are not part of the key.
export interface CardVariant {
  finish: string;
  specialty_one: string;
  specialty_two: string;
}

export interface CardConditionRow {
  condition: string;
  qty: number;
  estimated_price: number | null;
  confidence_percent: number | null;
}

export interface CardDetail {
  card: {
    card_id: string;
    card_name: string;
    set_name: string | null;
    rarity: string | null;
    card_number: string | null;
    era: string | null;
    card_type: string | null;
  } & CardVariant;
  has_image: boolean;
  conditions: CardConditionRow[];
  total_qty: number;
}

export function fetchCardDetail(cardId: string, variant: CardVariant): Promise<CardDetail> {
  const qs = new URLSearchParams({
    finish: variant.finish,
    specialty_one: variant.specialty_one,
    specialty_two: variant.specialty_two,
  }).toString();
  return fetchJson(`${BASE}/card/${cardId}?${qs}`);
}

// Per-card sales graph: one daily point per (condition, date).
export interface SalesPoint {
  date: string;
  condition: string;
  median_price: number | null;
  min_price: number | null;
  max_price: number | null;
  volume: number;
}

export interface CardSalesHistory {
  card_id: string;
  finish: string;
  source: string;
  days: number;
  include_images: boolean;
  conditions: string[];
  points: SalesPoint[];
}

// Photo/custom-listing sales are excluded by default; pass includeImages to fold them in.
export function fetchCardSalesHistory(
  cardId: string,
  finish: string,
  days: number,
  includeImages = false,
): Promise<CardSalesHistory> {
  const params: Record<string, string> = { finish, days: String(days) };
  if (includeImages) params.include_images = "true";
  const qs = new URLSearchParams(params).toString();
  return fetchJson(`${BASE}/card/${cardId}/sales?${qs}`);
}

// Individual sales as graph points (one per unit), for the scatter view.
// order_date is the sale's full timestamp, used to place points along the time axis.
export interface SalesRawPoint {
  order_date: string;
  condition: string;
  price: number;
}

export interface CardSalesPoints {
  card_id: string;
  finish: string;
  source: string;
  days: number;
  conditions: string[];
  points: SalesRawPoint[];
}

export function fetchCardSalesPoints(
  cardId: string,
  finish: string,
  days: number,
  includeImages = false,
): Promise<CardSalesPoints> {
  const params: Record<string, string> = { finish, days: String(days) };
  if (includeImages) params.include_images = "true";
  const qs = new URLSearchParams(params).toString();
  return fetchJson(`${BASE}/card/${cardId}/sales-points?${qs}`);
}

// TCGplayer "market price" over time (weekly), one series per condition.
export interface MarketPricePoint {
  date: string;
  condition: string;
  market_price: number | null;
}

export interface CardPriceHistory {
  card_id: string;
  finish: string;
  source: string;
  days: number;
  conditions: string[];
  points: MarketPricePoint[];
}

export function fetchCardPriceHistory(
  cardId: string,
  finish: string,
  days: number,
): Promise<CardPriceHistory> {
  const qs = new URLSearchParams({ finish, days: String(days) }).toString();
  return fetchJson(`${BASE}/card/${cardId}/price-history?${qs}`);
}

// ── Graded cards (parallel to the raw collection types above) ──

export interface GradedCardItem {
  graded_inventory_id: number;
  graded_sku_id: number;
  cert_id: string | null;
  qty: number;
  tags: string | null;
  status: string;
  front_photo_path: string | null;
  back_photo_path: string | null;
  ebay_listing_id: string | null;
  // card_id is the OPTIONAL TCGplayer link (null until the graded->raw converter
  // or a manual entry sets it).
  card_id: string | null;
  finish: string;
  grading_company: string;
  grade: number;
  grade_label: string | null;
  grader_spec_id: string | null;
  // Grader-supplied identity (card_name/set_name/card_number are COALESCEd
  // grader-first on the backend).
  card_name: string;
  set_name: string | null;
  card_number: string | null;
  card_year: string | null;
  card_variety: string | null;
  card_language: string | null;
  population: number | null;
  population_higher: number | null;
  rarity: string | null;
  era: string | null;
  estimated_price: number | null;
  confidence_percent: number | null;
  calculation_date: string | null;
}

export interface GradedResponse {
  items: GradedCardItem[];
  total: number;
  page: number;
  per_page: number;
  total_pages: number;
}

export interface GradedFilters {
  grading_companies: string[];
  statuses: string[];
}

export interface GradedCardDetail {
  card: {
    card_id: string;
    card_name: string;
    set_name: string | null;
    rarity: string | null;
    card_number: string | null;
    era: string | null;
    card_type: string | null;
    finish: string;
    specialty_one: string;
    grading_company: string;
    grade: number;
  };
  kind: "graded";
  has_image: boolean;
  qty: number;
  estimated_price: number | null;
  confidence_percent: number | null;
  raw_estimated_price: number | null;
}

export function fetchGradedCollection(params: Record<string, string>): Promise<GradedResponse> {
  const qs = new URLSearchParams(params).toString();
  return fetchJson(`${BASE}/graded?${qs}`);
}

export function fetchGradedFilters(): Promise<GradedFilters> {
  return fetchJson(`${BASE}/graded/companies`);
}

export function fetchGradedCardDetail(
  cardId: string,
  key: { finish: string; specialty_one: string; grading_company: string; grade: number },
): Promise<GradedCardDetail> {
  const qs = new URLSearchParams({
    finish: key.finish,
    specialty_one: key.specialty_one,
    grading_company: key.grading_company,
    grade: String(key.grade),
  }).toString();
  return fetchJson(`${BASE}/graded/${cardId}?${qs}`);
}

// Add a graded slab by cert number (POST — the dashboard's first write call).
// card_id is the optional, manually-entered TCGplayer id.
export async function addGradedByCert(
  certId: string,
  gradingCompany: string,
  cardId?: string,
): Promise<GradedCardItem> {
  const res = await fetch(`${BASE}/graded`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      cert_id: certId,
      grading_company: gradingCompany,
      ...(cardId ? { card_id: cardId } : {}),
    }),
  });
  if (!res.ok) {
    let msg = `Add failed (${res.status})`;
    try {
      const body = await res.json();
      if (body?.error) msg = body.error;
    } catch {
      /* ignore */
    }
    throw new Error(msg);
  }
  return res.json();
}
