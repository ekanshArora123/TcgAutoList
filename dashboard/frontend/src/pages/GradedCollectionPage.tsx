import { useEffect, useState, useCallback } from "react";
import { Link } from "react-router-dom";
import {
  fetchGradedCollection, fetchGradedFilters, addGradedByCert, cardImageUrl, gradedImageUrl,
  type GradedCardItem, type GradedResponse, type GradedFilters,
} from "../api";
import CardImage from "../components/CardImage";

const fmt = (n: number | null | undefined) => (n != null ? `$${n.toFixed(2)}` : "-");

// The graded collection view. Parallel to CollectionGridPage but reads the
// graded endpoints; the raw collection page is untouched.
export default function GradedCollectionPage() {
  const [data, setData] = useState<GradedResponse | null>(null);
  const [filters, setFilters] = useState<GradedFilters | null>(null);
  const [company, setCompany] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);

  // Add-by-cert form
  const [certInput, setCertInput] = useState("");
  const [tcgIdInput, setTcgIdInput] = useState("");
  const [addCompany, setAddCompany] = useState("PSA");
  const [adding, setAdding] = useState(false);
  const [addError, setAddError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    const params: Record<string, string> = { page: String(page), per_page: "48" };
    if (company) params.grading_company = company;
    if (status) params.status = status;
    setData(await fetchGradedCollection(params));
    setLoading(false);
  }, [company, status, page]);

  useEffect(() => { fetchGradedFilters().then(setFilters); }, []);
  useEffect(() => { load(); }, [load]);

  async function handleAdd(e: React.FormEvent) {
    e.preventDefault();
    if (!certInput.trim() || adding) return;
    setAdding(true);
    setAddError("");
    try {
      await addGradedByCert(certInput.trim(), addCompany, tcgIdInput.trim() || undefined);
      setCertInput("");
      setTcgIdInput("");
      setPage(1);
      await load();
    } catch (err) {
      setAddError(err instanceof Error ? err.message : "Add failed");
    } finally {
      setAdding(false);
    }
  }

  return (
    <div>
      {/* Add a graded card by cert number */}
      <form className="graded-add-form" onSubmit={handleAdd}>
        <span className="graded-add-title">Add graded card</span>
        <input
          className="search-input"
          placeholder="Cert #"
          value={certInput}
          onChange={(e) => setCertInput(e.target.value)}
        />
        <input
          className="search-input"
          placeholder="TCGplayer ID (optional)"
          value={tcgIdInput}
          onChange={(e) => setTcgIdInput(e.target.value)}
        />
        <select className="filter-select" value={addCompany} onChange={(e) => setAddCompany(e.target.value)}>
          {Array.from(new Set(["PSA", ...(filters?.grading_companies || [])])).map((c) => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>
        <button className="price-range-btn" type="submit" disabled={adding || !certInput.trim()}>
          {adding ? "Fetching…" : "Add"}
        </button>
        {addError && <span className="graded-add-error">{addError}</span>}
      </form>

      <div className="filters-bar">
        <span style={{ fontSize: 18, fontWeight: 600 }}>Graded Slabs</span>
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
          </>
        )}
        {data && <span style={{ color: "#8b949e", fontSize: 12 }}>{data.total} slab(s)</span>}
      </div>

      {loading ? (
        <div className="loading">Loading...</div>
      ) : !data || data.items.length === 0 ? (
        <div className="loading">No graded slabs yet. Add one by cert number above.</div>
      ) : (
        <div className="card-grid">
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
  const toCrack = (card.tags || "").split(",").map((t) => t.trim()).includes("to_crack");

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
        {toCrack && <span className="graded-tile-crack">Crack</span>}
      </div>

      <div className="card-tile-info">
        <div className="card-tile-name" title={card.card_name}>
          {card.card_name}
          {card.card_number ? <span className="card-number"> #{card.card_number}</span> : ""}
        </div>
        <div className="card-tile-set">{card.set_name || "Unknown Set"}</div>
        <div className="graded-tile-status-row">
          <span className={`card-tile-price ${card.estimated_price == null ? "no-price" : ""}`}>
            {card.estimated_price != null ? fmt(card.estimated_price) : "—"}
          </span>
          <span className={`status-badge status-${card.status}`}>{card.status}</span>
        </div>
      </div>
    </>
  );

  // The whole tile links to the slab detail page (keyed by cert). Cert-less
  // manual adds have no detail route, so they render as a plain, unlinked tile.
  return card.cert_id ? (
    <Link to={`/graded/slab/${encodeURIComponent(card.cert_id)}`} className="graded-tile graded-tile-link">
      {inner}
    </Link>
  ) : (
    <div className="graded-tile">{inner}</div>
  );
}
