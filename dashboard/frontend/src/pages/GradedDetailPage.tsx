import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  fetchGradedSlabDetail, setGradedSlabTag, cardImageUrl, gradedImageUrl,
  type GradedSlabDetail, type GradedSlab,
} from "../api";
import CardImage from "../components/CardImage";

const fmt = (n: number | null | undefined) => (n != null ? `$${n.toFixed(2)}` : "-");
// Awkward/unavailable grades arrive as the -1 sentinel (see psa.py) → show ERR.
const gradeText = (g: number) => (g > 0 ? String(g) : "ERR");

// Graded card detail: one card's grade-class rollup (all grades within a company,
// grouped by grader spec) plus each owned slab. Reached at /graded/slab/:certId;
// the entered cert is highlighted in the slabs list.
export default function GradedDetailPage() {
  const { certId } = useParams<{ certId: string }>();
  const [detail, setDetail] = useState<GradedSlabDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    if (!certId) return;
    setLoading(true);
    setNotFound(false);
    fetchGradedSlabDetail(certId)
      .then(setDetail)
      .catch(() => setNotFound(true))
      .finally(() => setLoading(false));
  }, [certId]);

  if (loading) return <div className="loading">Loading...</div>;
  if (notFound || !detail) return <div className="loading">Slab not found</div>;

  const { identity, grades, slabs } = detail;

  // Patch a single slab in place after a tag toggle (avoids a full refetch).
  const patchSlab = (id: number, changes: Partial<GradedSlab>) =>
    setDetail((d) =>
      d
        ? { ...d, slabs: d.slabs.map((s) => (s.graded_inventory_id === id ? { ...s, ...changes } : s)) }
        : d,
    );

  const chips = [
    identity.finish !== "Regular" ? identity.finish : null,
    identity.specialty_one !== "None" ? identity.specialty_one : null,
    identity.card_variety,
    identity.card_language,
    identity.card_year,
  ].filter(Boolean) as string[];

  return (
    <div className="card-detail">
      <Link to="/graded" className="card-detail-back">&larr; Back to graded</Link>

      <div className="card-detail-header">
        <h2 className="card-detail-name">
          {identity.card_name || "Unknown Card"}
          {identity.card_number ? <span className="card-number"> (#{identity.card_number})</span> : ""}
        </h2>
        <div className="card-detail-set">
          {identity.set_name || "Unknown Set"} · {identity.grading_company}
          {identity.grader_spec_id ? ` · spec ${identity.grader_spec_id}` : ""}
        </div>
        <div className="card-detail-chips">
          {chips.map((c) => <span key={c} className="tag tag-special">{c}</span>)}
        </div>
      </div>

      <div className="graded-detail-grid">
        {/* Grade classes: standardized per (company, grade). Price-over-time lands
            in the reserved column later. */}
        <section className="card-detail-panel">
          <h3>Grades ({identity.grading_company})</h3>
          <table className="card-table">
            <thead>
              <tr>
                <th>Grade</th>
                <th>Owned</th>
                <th>Population</th>
                <th>Est. Price</th>
                <th>Price history</th>
              </tr>
            </thead>
            <tbody>
              {grades.map((g) => (
                <tr key={g.graded_sku_id}>
                  <td><span className="graded-grade-pill">{gradeText(g.grade)}</span> {g.grade_label || ""}</td>
                  <td>{g.qty}</td>
                  <td>{g.population != null ? g.population : "-"}</td>
                  <td className={`price-cell ${g.estimated_price == null ? "no-price" : ""}`}>
                    {fmt(g.estimated_price)}
                  </td>
                  <td className="graded-soon">soon</td>
                </tr>
              ))}
            </tbody>
          </table>
          {identity.card_id ? (
            <div className="card-detail-qty-actions">
              <Link
                to={`/card/${identity.card_id}?finish=${encodeURIComponent(identity.finish)}&specialty_one=${encodeURIComponent(identity.specialty_one)}`}
                className="qty-save-btn"
              >
                View raw card
              </Link>
              {identity.raw_estimated_price != null && (
                <span className="qty-save-error" style={{ color: "#8b949e" }}>
                  Raw ≈ {fmt(identity.raw_estimated_price)}
                </span>
              )}
            </div>
          ) : (
            <div className="card-detail-empty">Not linked to a raw card yet.</div>
          )}
        </section>

        {/* Slabs: cert-specific. Front + back image, status, and the To-Crack mark. */}
        <section className="card-detail-panel">
          <h3>Your Slabs ({slabs.length})</h3>
          <div className="slab-list">
            {slabs.map((s) => (
              <SlabCard
                key={s.graded_inventory_id}
                slab={s}
                selected={s.cert_id === detail.selected_cert}
                rawCardId={identity.card_id}
                onToggleCrack={patchSlab}
              />
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}

function SlabCard({
  slab, selected, rawCardId, onToggleCrack,
}: {
  slab: GradedSlab;
  selected: boolean;
  rawCardId: string | null;
  onToggleCrack: (id: number, changes: Partial<GradedSlab>) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const toggleCrack = () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    setGradedSlabTag(slab.graded_inventory_id, "to_crack", !slab.to_crack)
      .then((r) => onToggleCrack(slab.graded_inventory_id, { tags: r.tags, to_crack: r.to_crack }))
      .catch((e) => setError(e instanceof Error ? e.message : "Failed"))
      .finally(() => setBusy(false));
  };

  const rawFallback = rawCardId ? cardImageUrl(rawCardId) : null;
  return (
    <div className={`slab-card ${selected ? "slab-card-selected" : ""} ${slab.to_crack ? "slab-card-crack" : ""}`}>
      <div className="slab-card-images">
        <div className="slab-face">
          <CardImage
            srcs={[slab.cert_id ? gradedImageUrl(slab.cert_id, "front") : null, rawFallback]}
            alt="Front"
          />
          <span className="slab-face-label">Front</span>
        </div>
        <div className="slab-face">
          <CardImage srcs={[slab.cert_id ? gradedImageUrl(slab.cert_id, "back") : null]} alt="Back" />
          <span className="slab-face-label">Back</span>
        </div>
      </div>

      <div className="slab-card-info">
        <div className="adv-row">
          <span>Grade</span>
          <span>{slab.grade_label || `${slab.grading_company} ${gradeText(slab.grade)}`}</span>
        </div>
        {slab.cert_id && <div className="adv-row"><span>Cert #</span><span>{slab.cert_id}</span></div>}
        <div className="adv-row">
          <span>Status</span>
          <span className={`status-badge status-${slab.status}`}>{slab.status}</span>
        </div>
        {slab.to_crack && <div className="slab-crack-flag">Marked to crack</div>}
        <button
          type="button"
          className={`slab-crack-btn ${slab.to_crack ? "active" : ""}`}
          onClick={toggleCrack}
          disabled={busy}
        >
          {busy ? "…" : slab.to_crack ? "Unmark to crack" : "Mark to crack"}
        </button>
        {error && <span className="qty-save-error">{error}</span>}
      </div>
    </div>
  );
}
