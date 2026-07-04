import { useEffect, useState, useCallback } from "react";
import {
  fetchGradedCollection, fetchGradedFilters, cardImageUrl,
  type GradedCardItem, type GradedResponse, type GradedFilters,
} from "../api";

const fmt = (n: number | null | undefined) => (n != null ? `$${n.toFixed(2)}` : "-");
const gradeLabel = (c: GradedCardItem) => `${c.grading_company} ${c.grade}`;

// The graded collection view. Parallel to CollectionGridPage but reads the
// graded endpoints; the raw collection page is untouched.
export default function GradedCollectionPage() {
  const [data, setData] = useState<GradedResponse | null>(null);
  const [filters, setFilters] = useState<GradedFilters | null>(null);
  const [company, setCompany] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);

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

  return (
    <div>
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
        <div className="loading">No graded slabs yet.</div>
      ) : (
        <div className="card-grid">
          {data.items.map((c) => (
            <div key={c.graded_inventory_id} className="card-tile">
              <div className="card-tile-image">
                <img
                  src={cardImageUrl(c.card_id)}
                  alt={c.card_name}
                  onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }}
                />
              </div>
              <div className="card-tile-name">{c.card_name}</div>
              <div className="card-tile-tags">
                <span className="tag tag-special">{gradeLabel(c)}</span>
                {c.finish !== "Regular" && <span className="tag">{c.finish}</span>}
                {c.cert_id && <span className="tag">Cert #{c.cert_id}</span>}
              </div>
              <div className="card-tile-price-row">
                <span className={`card-tile-price ${c.estimated_price == null ? "no-price" : ""}`}>
                  {fmt(c.estimated_price)}
                </span>
                <span className={`status-badge status-${c.status}`}>{c.status}</span>
              </div>
              <div style={{ fontSize: 11, color: "#8b949e" }}>
                {c.set_name || "Unknown Set"}{c.card_number ? ` · ${c.card_number}` : ""}
              </div>
            </div>
          ))}
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
