import { useEffect, useState, useCallback } from "react";
import { Link } from "react-router-dom";
import {
  fetchCollection, fetchFilters,
  type CollectionItem, type CollectionResponse, type Filters,
} from "../api";
import CardImage from "../components/CardImage";

// Path to a card's detail page. Identity is the SKU minus condition; tags are
// excluded from the key (tagged cards don't route — see CardTile).
function cardDetailPath(card: CollectionItem): string {
  const qs = new URLSearchParams({
    finish: card.finish,
    specialty_one: card.specialty_one,
    specialty_two: card.specialty_two,
  }).toString();
  return `/card/${card.card_id}?${qs}`;
}

const fmt = (n: number | null | undefined) => (n != null ? `$${n.toFixed(2)}` : "-");

function confidenceClass(c: number | null) {
  if (c == null) return "";
  if (c >= 80) return "confidence-high";
  if (c >= 40) return "confidence-mid";
  return "confidence-low";
}

export default function CollectionGridPage() {
  const [data, setData] = useState<CollectionResponse | null>(null);
  const [filters, setFilters] = useState<Filters | null>(null);
  const [loading, setLoading] = useState(true);
  const [advanced, setAdvanced] = useState(false);

  // Filters
  const [searchInput, setSearchInput] = useState("");
  const [q, setQ] = useState("");
  const [setName, setSetName] = useState("");
  const [era, setEra] = useState("");
  const [rarity, setRarity] = useState("");
  const [condition, setCondition] = useState("");
  const [finish, setFinish] = useState("");
  const [status, setStatus] = useState("");
  const [specialty, setSpecialty] = useState("");
  const [tagsContain, setTagsContain] = useState("");
  const [manualCheck, setManualCheck] = useState(false);
  const [priceMin, setPriceMin] = useState("");
  const [priceMax, setPriceMax] = useState("");
  const [confidenceMin, setConfidenceMin] = useState("");
  const [confidenceMax, setConfidenceMax] = useState("");
  const [sort, setSort] = useState("estimated_price");
  const [order, setOrder] = useState("desc");
  const [page, setPage] = useState(1);
  const [perPage, setPerPage] = useState(48);

  const load = useCallback(async () => {
    setLoading(true);
    const params: Record<string, string> = { page: String(page), per_page: String(perPage), sort, order };
    if (q) params.q = q;
    if (setName) params.set_name = setName;
    if (era) params.era = era;
    if (rarity) params.rarity = rarity;
    if (condition) params.condition = condition;
    if (finish) params.finish = finish;
    if (status) params.status = status;
    if (specialty) params.specialty = specialty;
    if (tagsContain) params.tags_contain = tagsContain;
    if (manualCheck) params.manual_check = "true";
    if (priceMin) params.price_min = priceMin;
    if (priceMax) params.price_max = priceMax;
    if (confidenceMin) params.confidence_min = confidenceMin;
    if (confidenceMax) params.confidence_max = confidenceMax;
    const result = await fetchCollection(params);
    setData(result);
    setLoading(false);
  }, [q, setName, era, rarity, condition, finish, status, specialty, tagsContain, manualCheck, priceMin, priceMax, confidenceMin, confidenceMax, sort, order, page, perPage]);

  useEffect(() => { fetchFilters().then(setFilters); }, []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    const t = setTimeout(() => { setQ(searchInput); setPage(1); }, 300);
    return () => clearTimeout(t);
  }, [searchInput]);

  const PRICE_RANGES = [
    { label: "All", min: "", max: "" },
    { label: "<$0.50", min: "", max: "0.5" },
    { label: "$0.50-$1", min: "0.5", max: "1" },
    { label: "$1-$5", min: "1", max: "5" },
    { label: "$5-$10", min: "5", max: "10" },
    { label: "$10-$30", min: "10", max: "30" },
    { label: "$30-$100", min: "30", max: "100" },
    { label: "$100+", min: "100", max: "" },
  ];

  const activePriceRange = PRICE_RANGES.find(r => r.min === priceMin && r.max === priceMax);

  const SORT_OPTIONS = [
    { value: "estimated_price", label: "Price" },
    { value: "card_name", label: "Name" },
    { value: "set_name", label: "Set" },
    { value: "era", label: "Era" },
    { value: "rarity", label: "Rarity" },
    { value: "condition", label: "Condition" },
    { value: "confidence", label: "Confidence" },
  ];

  return (
    <div>
      {/* Search + main filters */}
      <div className="filters-bar">
        <input className="search-input" placeholder="Search card name..." value={searchInput} onChange={(e) => setSearchInput(e.target.value)} />
        {filters && (
          <>
            <select className="filter-select" value={era} onChange={(e) => { setEra(e.target.value); setPage(1); }}>
              <option value="">All Eras</option>
              {filters.eras.map((e) => <option key={e} value={e}>{e}</option>)}
            </select>
            <select className="filter-select" value={setName} onChange={(e) => { setSetName(e.target.value); setPage(1); }}>
              <option value="">All Sets</option>
              {filters.sets.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
            <select className="filter-select" value={rarity} onChange={(e) => { setRarity(e.target.value); setPage(1); }}>
              <option value="">All Rarities</option>
              {filters.rarities.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
            <select className="filter-select" value={condition} onChange={(e) => { setCondition(e.target.value); setPage(1); }}>
              <option value="">All Conditions</option>
              {filters.conditions.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
            <select className="filter-select" value={finish} onChange={(e) => { setFinish(e.target.value); setPage(1); }}>
              <option value="">All Finishes</option>
              {filters.finishes.map((f) => <option key={f} value={f}>{f}</option>)}
            </select>
            <select className="filter-select" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
              <option value="">All Statuses</option>
              {filters.statuses.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </>
        )}
      </div>

      {/* Price range quick filters + sort + advanced toggle */}
      <div className="filters-bar">
        <span style={{ fontSize: 12, color: "#8b949e" }}>Price:</span>
        {PRICE_RANGES.map((r) => (
          <button
            key={r.label}
            className="price-range-btn"
            style={{
              background: activePriceRange === r ? "#1f6feb" : "#161b22",
              color: activePriceRange === r ? "#fff" : "#8b949e",
            }}
            onClick={() => { setPriceMin(r.min); setPriceMax(r.max); setPage(1); }}
          >{r.label}</button>
        ))}
        <input className="search-input" style={{ width: 70, minWidth: 0 }} placeholder="Min $" type="number" step="any" value={priceMin} onChange={(e) => { setPriceMin(e.target.value); setPage(1); }} />
        <span style={{ color: "#484f58" }}>-</span>
        <input className="search-input" style={{ width: 70, minWidth: 0 }} placeholder="Max $" type="number" step="any" value={priceMax} onChange={(e) => { setPriceMax(e.target.value); setPage(1); }} />

        <span style={{ color: "#30363d", margin: "0 4px" }}>|</span>
        <select className="filter-select" value={sort} onChange={(e) => { setSort(e.target.value); setPage(1); }}>
          {SORT_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
        <button className="price-range-btn" style={{ background: "#161b22", color: "#8b949e" }} onClick={() => setOrder(order === "asc" ? "desc" : "asc")}>
          {order === "asc" ? "\u25B2 Asc" : "\u25BC Desc"}
        </button>

        <span style={{ color: "#30363d", margin: "0 4px" }}>|</span>
        <label className="toggle-label">
          <input type="checkbox" checked={manualCheck} onChange={(e) => { setManualCheck(e.target.checked); setPage(1); }} />
          Needs Review
        </label>
      </div>

      {/* Extra filters row */}
      <div className="filters-bar">
        <input className="search-input" style={{ width: 120, minWidth: 0 }} placeholder="Tags contain..." value={tagsContain} onChange={(e) => { setTagsContain(e.target.value); setPage(1); }} />
        <input className="search-input" style={{ width: 80, minWidth: 0 }} placeholder="Conf min" type="number" value={confidenceMin} onChange={(e) => { setConfidenceMin(e.target.value); setPage(1); }} />
        <span style={{ color: "#484f58" }}>-</span>
        <input className="search-input" style={{ width: 80, minWidth: 0 }} placeholder="Conf max" type="number" value={confidenceMax} onChange={(e) => { setConfidenceMax(e.target.value); setPage(1); }} />
        {filters && (
          <select className="filter-select" value={specialty} onChange={(e) => { setSpecialty(e.target.value); setPage(1); }}>
            <option value="">All Specialties</option>
            <option value="First Edition">1st Edition</option>
            <option value="None">None (Standard)</option>
          </select>
        )}
        <select className="filter-select" value={String(perPage)} onChange={(e) => { setPerPage(Number(e.target.value)); setPage(1); }}>
          <option value="24">24 per page</option>
          <option value="48">48 per page</option>
          <option value="96">96 per page</option>
          <option value="144">144 per page</option>
        </select>

        <span style={{ flex: 1 }} />
        <label className="toggle-label advanced-toggle">
          <input type="checkbox" checked={advanced} onChange={(e) => setAdvanced(e.target.checked)} />
          Display Advanced
        </label>
      </div>

      {data && <div className="results-count">{data.total.toLocaleString()} cards found</div>}

      {loading ? (
        <div className="loading">Loading...</div>
      ) : data && data.items.length > 0 ? (
        <>
          <div className="card-grid">
            {data.items.map((card) => (
              <CardTile key={card.inventory_id} card={card} advanced={advanced} />
            ))}
          </div>

          <div className="pagination">
            <button disabled={page <= 1} onClick={() => setPage(1)}>First</button>
            <button disabled={page <= 1} onClick={() => setPage(page - 1)}>Prev</button>
            <span className="page-info">Page {data.page} of {data.total_pages}</span>
            <button disabled={page >= data.total_pages} onClick={() => setPage(page + 1)}>Next</button>
            <button disabled={page >= data.total_pages} onClick={() => setPage(data.total_pages)}>Last</button>
          </div>
        </>
      ) : (
        <div className="loading">No cards found</div>
      )}
    </div>
  );
}

function CardTile({ card, advanced }: { card: CollectionItem; advanced: boolean }) {
  const isFirstEdition = card.specialty_one === "1st Edition" || card.specialty_one === "First Edition";
  // Tagged cards don't get their own page yet — only untagged tiles link out.
  const hasTags = !!(card.tags && card.tags.trim());

  const imageInner = (
    <>
      <CardImage cardId={card.card_id} alt={card.card_name} />
      {isFirstEdition && <div className="first-edition-badge">1st Ed</div>}
    </>
  );

  return (
    <div className={`card-tile ${advanced ? "card-tile-advanced" : ""}`}>
      {hasTags ? (
        <div className="card-tile-image">{imageInner}</div>
      ) : (
        <Link to={cardDetailPath(card)} className="card-tile-image card-tile-image-link">
          {imageInner}
        </Link>
      )}

      <div className="card-tile-info">
        <div className="card-tile-name" title={card.card_name}>
          {card.card_name}
          {card.card_number ? <span className="card-number"> ({card.card_number})</span> : ""}
        </div>
        <div className="card-tile-set">{card.set_name || "Unknown Set"}</div>
        <div className="card-tile-meta">
          <span className="tag">{card.condition}</span>
          {card.finish !== "Regular" && <span className="tag">{card.finish}</span>}
          {isFirstEdition && <span className="tag tag-special">1st Ed</span>}
          {card.qty > 1 && <span className="tag tag-special">Qty: {card.qty}</span>}
        </div>
        <div className="card-tile-price-row">
          <span className={`card-tile-price ${card.estimated_price == null ? "no-price" : ""}`}>
            {fmt(card.estimated_price)}
          </span>
          {card.confidence_percent != null && (
            <span className={`confidence-badge ${confidenceClass(card.confidence_percent)}`}>
              {card.confidence_percent}%
            </span>
          )}
        </div>

        {/* Tags and specialties */}
        <div className="card-tile-tags">
          {card.specialty_two !== "None" && <span className="tag tag-special">{card.specialty_two}</span>}
          {card.tags && card.tags.split(",").map((t, i) => <span key={i} className="tag">{t.trim()}</span>)}
          {card.manual_check_necessary ? <span className="tag tag-review">Review</span> : null}
        </div>

        {/* Advanced info */}
        {advanced && (
          <div className="card-tile-advanced-info">
            <div className="adv-section">
              <div className="adv-header">Card Details</div>
              <div className="adv-row"><span>Era</span><span>{card.era || "-"}</span></div>
              <div className="adv-row"><span>Rarity</span><span>{card.rarity || "-"}</span></div>
              <div className="adv-row"><span>Condition</span><span>{card.condition}</span></div>
              <div className="adv-row"><span>Finish</span><span>{card.finish}</span></div>
              <div className="adv-row"><span>Type</span><span>{card.card_type || "-"}</span></div>
              <div className="adv-row"><span>Layout</span><span>{card.visual_layout || "-"}</span></div>
              <div className="adv-row"><span>Status</span><span className={`status-badge status-${card.status}`}>{card.status}</span></div>
              <div className="adv-row"><span>Qty</span><span>{card.qty}</span></div>
              <div className="adv-row"><span>TCGplayer ID</span><span>{card.card_id}</span></div>
            </div>

            <div className="adv-section">
              <div className="adv-header">Pricing Algorithm</div>
              <div className="adv-row"><span>Est. Price</span><span className="price-cell">{fmt(card.estimated_price)}</span></div>
              <div className="adv-row"><span>Range</span><span>{fmt(card.estimated_low_price)} - {fmt(card.estimated_high_price)}</span></div>
              <div className="adv-row"><span>Liquid Value</span><span className="price-cell">{fmt(card.estimated_liquid_value)}</span></div>
              <div className="adv-row"><span>Liquid Range</span><span>{fmt(card.estimated_low_price_liquid)} - {fmt(card.estimated_high_price_liquid)}</span></div>
              <div className="adv-row"><span>Confidence</span><span>{card.confidence_percent != null ? `${card.confidence_percent}%` : "-"}</span></div>
              <div className="adv-row"><span>Manual Check</span><span>{card.manual_check_necessary ? "Yes" : "No"}{card.manually_checked ? " (checked)" : ""}</span></div>
              <div className="adv-row"><span>Calc Date</span><span>{card.calculation_date || "-"}</span></div>
              <div className="adv-row"><span>Algorithm</span><span>v{card.algorithm_version || "?"}</span></div>
            </div>

            <div className="adv-section">
              <div className="adv-header">Market Data</div>
              {card.market_snapshot_date ? (
                <>
                  <div className="adv-row"><span>Snapshot</span><span>{card.market_snapshot_date}</span></div>
                  <div className="adv-row"><span>Listings</span><span>{card.listing_count ?? "-"}</span></div>
                  <div className="adv-row"><span>Lowest</span><span>{fmt(card.lowest_listing_price)}</span></div>
                  <div className="adv-row"><span>Median List</span><span>{fmt(card.median_listing_price)}</span></div>
                  <div className="adv-row"><span>Mean List</span><span>{fmt(card.mean_listing_price)}</span></div>
                  <div className="adv-row"><span>P25/P75</span><span>{fmt(card.p25_listing_price)} / {fmt(card.p75_listing_price)}</span></div>
                  <div className="adv-row"><span>Recent Sales</span><span>{card.recent_sales_count ?? "-"}</span></div>
                  <div className="adv-row"><span>Avg Sale</span><span>{fmt(card.avg_sale_price)}</span></div>
                  <div className="adv-row"><span>Median Sale</span><span>{fmt(card.median_sale_price)}</span></div>
                  <div className="adv-row"><span>Sale Range</span><span>{fmt(card.min_sale_price)} - {fmt(card.max_sale_price)}</span></div>
                  <div className="adv-row"><span>Newest Sale</span><span>{card.newest_sale_date?.split("T")[0] || "-"}</span></div>
                  <div className="adv-row"><span>Oldest Sale</span><span>{card.oldest_sale_date?.split("T")[0] || "-"}</span></div>
                </>
              ) : (
                <div className="adv-row"><span>No market data</span><span></span></div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
