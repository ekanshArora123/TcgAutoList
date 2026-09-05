import { useEffect, useState, useCallback } from "react";
import {
  fetchCollection, fetchFilters, cardImageUrl,
  type CollectionItem, type CollectionResponse, type Filters,
} from "../api";
import CardImage from "../components/CardImage";

const fmt = (n: number | null | undefined) => (n != null ? `$${n.toFixed(2)}` : "-");

export default function CollectionGridPage() {
  const [data, setData] = useState<CollectionResponse | null>(null);
  const [filters, setFilters] = useState<Filters | null>(null);
  const [loading, setLoading] = useState(true);

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
  const [priceMin, setPriceMin] = useState("");
  const [priceMax, setPriceMax] = useState("");
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
    if (priceMin) params.price_min = priceMin;
    if (priceMax) params.price_max = priceMax;
    const result = await fetchCollection(params);
    setData(result);
    setLoading(false);
  }, [q, setName, era, rarity, condition, finish, status, specialty, priceMin, priceMax, sort, order, page, perPage]);

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
        <span className="filter-label">Price:</span>
        {PRICE_RANGES.map((r) => (
          <button
            key={r.label}
            className="price-range-btn"
            style={{
              background: activePriceRange === r ? "var(--primary)" : "var(--surface)",
              color: activePriceRange === r ? "#fff" : "var(--text-muted)",
            }}
            onClick={() => { setPriceMin(r.min); setPriceMax(r.max); setPage(1); }}
          >{r.label}</button>
        ))}
        <input className="search-input price-bound-input" placeholder="Min $" type="number" step="any" value={priceMin} onChange={(e) => { setPriceMin(e.target.value); setPage(1); }} />
        <span className="filter-sep-dash">&ndash;</span>
        <input className="search-input price-bound-input" placeholder="Max $" type="number" step="any" value={priceMax} onChange={(e) => { setPriceMax(e.target.value); setPage(1); }} />

        <span className="filter-sep" aria-hidden="true" />
        <select className="filter-select" value={sort} onChange={(e) => { setSort(e.target.value); setPage(1); }}>
          {SORT_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
        <button className="price-range-btn" style={{ background: "var(--surface)", color: "var(--text-muted)" }} onClick={() => setOrder(order === "asc" ? "desc" : "asc")}>
          {order === "asc" ? "\u25B2 Asc" : "\u25BC Desc"}
        </button>

      </div>

      {/* Extra filters row */}
      <div className="filters-bar">
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

      </div>

      {data && <div className="results-count">{data.total.toLocaleString()} cards found</div>}

      {loading ? (
        <div className="loading">Loading...</div>
      ) : data && data.items.length > 0 ? (
        <>
          <div className="card-grid">
            {data.items.map((card) => (
              <CardTile key={card.inventory_id} card={card} />
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

function CardTile({ card }: { card: CollectionItem }) {
  const isFirstEdition = card.specialty_one === "1st Edition" || card.specialty_one === "First Edition";

  const imageInner = (
    <>
      <CardImage srcs={[card.card_id ? cardImageUrl(card.card_id) : null]} alt={card.card_name} />
      {isFirstEdition && <div className="first-edition-badge">1st Ed</div>}
    </>
  );

  return (
    <div className="card-tile">
      <div className="card-tile-image">{imageInner}</div>

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
        </div>

        {/* Specialties. Tags are never shown — tagged inventory is hidden — and
            neither is the internal manual-review flag. */}
        {card.specialty_two !== "None" && (
          <div className="card-tile-tags">
            <span className="tag tag-special">{card.specialty_two}</span>
          </div>
        )}

      </div>
    </div>
  );
}
