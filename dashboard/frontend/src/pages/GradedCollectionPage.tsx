import { useEffect, useState, useCallback } from "react";
import {
  fetchGradedCollection, fetchGradedFilters, cardImageUrl, gradedImageUrl,
  type GradedCardItem, type GradedResponse, type GradedFilters,
} from "../api";
import CardImage from "../components/CardImage";
import MultiSelect from "../components/MultiSelect";

const fmt = (n: number | null | undefined) => (n != null ? `$${n.toFixed(2)}` : "-");

// Sort keys must match reporting_service._GRADED_SORT_COLUMNS.
const GRADED_SORT_OPTIONS = [
  { value: "grade", label: "Grade" },
  { value: "card_name", label: "Name" },
  { value: "grading_company", label: "Company" },
  { value: "estimated_price", label: "Price" },
];

// The graded collection view. Parallel to CollectionGridPage but reads the
// graded endpoints; the raw collection page is untouched.
export default function GradedCollectionPage() {
  const [data, setData] = useState<GradedResponse | null>(null);
  const [filters, setFilters] = useState<GradedFilters | null>(null);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);

  // Search / filter / sort
  const [searchInput, setSearchInput] = useState("");
  const [q, setQ] = useState("");
  const [company, setCompany] = useState("");
  const [status, setStatus] = useState("");
  const [grades, setGrades] = useState<string[]>([]);
  const [sort, setSort] = useState("grade");
  const [order, setOrder] = useState("desc");
  const [perPage, setPerPage] = useState(48);
  // Cards per row; "auto" keeps the responsive default (fit as many as fit).
  const [columns, setColumns] = useState("auto");

  // Add-by-cert form
  const [addError, setAddError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    const params: Record<string, string | string[]> = {
      page: String(page), per_page: String(perPage), sort, order,
    };
    if (q) params.q = q;
    if (company) params.grading_company = company;
    if (status) params.status = status;
    if (grades.length) params.grades = grades;
    setData(await fetchGradedCollection(params));
    setLoading(false);
  }, [q, company, status, grades, sort, order, page, perPage]);

  useEffect(() => { fetchGradedFilters().then(setFilters); }, []);
  useEffect(() => { load(); }, [load]);
  // Debounce the search box into the applied query.
  useEffect(() => {
    const t = setTimeout(() => { setQ(searchInput); setPage(1); }, 300);
    return () => clearTimeout(t);
  }, [searchInput]);


  return (
    <div>
      {/* The "add graded card by cert" form is deliberately absent: it is a write
          control (it scrapes PSA and inserts rows), and this dashboard is shown
          to customers. Adding slabs is an owner task, not a browse action. */}

      {/* Search + filters */}
      <div className="filters-bar">
        <span style={{ fontSize: 18, fontWeight: 600 }}>Graded Slabs</span>
        <input
          className="search-input"
          placeholder="Search card name..."
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
        />
        {filters && (
          <>
            <select className="filter-select" value={company} onChange={(e) => { setCompany(e.target.value); setPage(1); }}>
              <option value="">All Companies</option>
              {filters.grading_companies.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
            <select className="filter-select" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
              <option value="">All Statuses</option>
              {filters.statuses.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
            <div style={{ width: 140 }}>
              <MultiSelect label="Grades" options={filters.grades ?? []} selected={grades} onChange={(v) => { setGrades(v); setPage(1); }} />
            </div>
          </>
        )}
      </div>

      {/* Sort + result count */}
      <div className="filters-bar">
        <span style={{ fontSize: 12, color: "var(--text-muted)" }}>Sort:</span>
        <select className="filter-select" value={sort} onChange={(e) => { setSort(e.target.value); setPage(1); }}>
          {GRADED_SORT_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
        <button
          className="price-range-btn"
          style={{ background: "var(--surface)", color: "var(--text-muted)" }}
          onClick={() => { setOrder(order === "asc" ? "desc" : "asc"); setPage(1); }}
        >
          {order === "asc" ? "▲ Asc" : "▼ Desc"}
        </button>

        <span style={{ color: "var(--border)", margin: "0 4px" }}>|</span>
        <select className="filter-select" value={String(perPage)} onChange={(e) => { setPerPage(Number(e.target.value)); setPage(1); }}>
          {[24, 48, 96, 144].map((n) => <option key={n} value={n}>{n} per page</option>)}
        </select>
        <select className="filter-select" value={columns} onChange={(e) => setColumns(e.target.value)}>
          <option value="auto">Auto columns</option>
          {[3, 4, 5, 6, 8].map((n) => <option key={n} value={String(n)}>{n} per row</option>)}
        </select>

        <span style={{ flex: 1 }} />
        {data && <span style={{ color: "var(--text-muted)", fontSize: 12 }}>{data.total} slab(s)</span>}
      </div>

      {loading ? (
        <div className="loading">Loading...</div>
      ) : !data || data.items.length === 0 ? (
        <div className="loading">
          {q || company || status || grades.length
            ? "No graded slabs match these filters."
            : "No graded slabs yet. Add one by cert number above."}
        </div>
      ) : (
        <div
          className="card-grid"
          style={columns !== "auto" ? { gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` } : undefined}
        >
          {data.items.map((c) => <GradedTile key={c.graded_inventory_id} card={c} />)}
        </div>
      )}

      {data && data.total_pages > 1 && (
        <div className="pagination">
          <button disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Prev</button>
          <span>Page {page} / {data.total_pages}</span>
          <button disabled={page >= data.total_pages} onClick={() => setPage((p) => p + 1)}>Next</button>
        </div>
      )}
    </div>
  );
}

// Matches the raw card tile (dark bg, light font): a big slab image with the
// grade overlaid as a badge, then a compact info box (name, set, price/status).
// Fuller per-slab detail lives on the slab detail page. Reuses the shared
// CardImage + the raw .card-tile-* styles.
function GradedTile({ card }: { card: GradedCardItem }) {
  // Awkward/unavailable grades come through as the -1 sentinel (see psa.py); show
  // "ERR" rather than a number.
  const gradeText = card.grade > 0 ? String(card.grade) : "ERR";
  // Condensed extra details (omit blanks) — one small line under the set.
  const meta = [
    card.card_year,
    card.card_variety,
    card.card_language,
    card.population != null ? `Pop ${card.population}` : null,
    card.cert_id ? `Cert ${card.cert_id}` : null,
  ].filter(Boolean);

  const inner = (
    <>
      <div className="card-tile-image graded-tile-image">
        {/* PSA slab front first; fall back to the linked raw card image, then a
            placeholder. (Only the front is shown; the back is stored for later.) */}
        <CardImage
          srcs={[
            card.cert_id ? gradedImageUrl(card.cert_id) : null,
            card.card_id ? cardImageUrl(card.card_id) : null,
          ]}
          alt={card.card_name}
        />
        <div className="graded-grade-badge">
          <span className="ggb-company">{card.grading_company}</span>
          <span className="ggb-grade">{gradeText}</span>
        </div>
      </div>

      <div className="card-tile-info">
        <div className="card-tile-name" title={card.card_name}>
          {card.card_name}
          {card.card_number ? <span className="card-number"> #{card.card_number}</span> : ""}
        </div>
        <div className="card-tile-set">{card.set_name || "Unknown Set"}</div>
        {meta.length > 0 && <div className="graded-tile-meta">{meta.join(" · ")}</div>}
        <div className="graded-tile-status-row">
          <span className={`card-tile-price ${card.estimated_price == null ? "no-price" : ""}`}>
            {card.estimated_price != null ? fmt(card.estimated_price) : "—"}
          </span>
          <span className={`status-badge status-${card.status}`}>{card.status}</span>
        </div>
      </div>
    </>
  );

  // Browse-only: tiles do not link to the slab detail page. The route itself is
  // still registered, so it stays reachable by URL.
  return <div className="graded-tile">{inner}</div>;
}
