import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  fetchGradedSlabDetail, setGradedSlabTag, setGradedCardLink, setGradedSlabPrice,
  cardImageUrl, gradedImageUrl, type GradedSlabDetail,
} from "../api";
import CardImage from "../components/CardImage";
import { computeLiquidValue } from "../pricing";

const fmt = (n: number | null | undefined) => (n != null ? `$${n.toFixed(2)}` : "-");
// Awkward/unavailable grades arrive as the -1 sentinel (see psa.py) → show ERR.
const gradeText = (g: number) => (g > 0 ? String(g) : "ERR");

// Graded card detail: one physical slab (the cert in the URL) shown large on the
// left — front + back image + the To-Crack action — with the spec-level context
// (every grade's qty/price, read-only) on the right. Reached at /graded/slab/:certId.
export default function GradedDetailPage() {
  const { certId } = useParams<{ certId: string }>();
  const [detail, setDetail] = useState<GradedSlabDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);

  // To-Crack toggle state.
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // TCGplayer-id editor state (seeded from the current link on every load).
  const [tcgInput, setTcgInput] = useState("");
  const [linkBusy, setLinkBusy] = useState(false);
  const [linkError, setLinkError] = useState<string | null>(null);

  useEffect(() => {
    if (!certId) return;
    setLoading(true);
    setNotFound(false);
    fetchGradedSlabDetail(certId)
      .then(setDetail)
      .catch(() => setNotFound(true))
      .finally(() => setLoading(false));
  }, [certId]);

  useEffect(() => {
    if (detail) setTcgInput(detail.identity.card_id ?? "");
  }, [detail]);

  // Price editor state, seeded from the focused grade's current manual price.
  const [priceInputs, setPriceInputs] = useState<Record<string, string>>({});
  const [priceBusy, setPriceBusy] = useState(false);
  const [priceError, setPriceError] = useState<string | null>(null);

  useEffect(() => {
    if (!detail) return;
    const g = detail.grades.find((row) => row.grade === detail.slab.grade);
    const s = (n: number | null | undefined) => (n != null ? String(n) : "");
    setPriceInputs({
      estimated_price: s(g?.estimated_price),
      estimated_low_price: s(g?.estimated_low_price),
      estimated_high_price: s(g?.estimated_high_price),
    });
    setPriceError(null);
  }, [detail]);

  if (loading) return <div className="loading">Loading...</div>;
  if (notFound || !detail) return <div className="loading">Slab not found</div>;

  const { identity, slab, grades } = detail;

  const toggleCrack = () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    setGradedSlabTag(slab.graded_inventory_id, "to_crack", !slab.to_crack)
      .then((r) =>
        setDetail((d) => (d ? { ...d, slab: { ...d.slab, tags: r.tags, to_crack: r.to_crack } } : d)),
      )
      .catch((e) => setError(e instanceof Error ? e.message : "Failed"))
      .finally(() => setBusy(false));
  };

  const linkDirty = tcgInput.trim() !== (identity.card_id ?? "");
  const saveLink = () => {
    if (!certId || linkBusy || !linkDirty) return;
    setLinkBusy(true);
    setLinkError(null);
    setGradedCardLink(certId, tcgInput.trim())
      .then(setDetail)
      .catch((e) => setLinkError(e instanceof Error ? e.message : "Failed"))
      .finally(() => setLinkBusy(false));
  };

  const savePrice = () => {
    if (!certId || priceBusy) return;
    setPriceBusy(true);
    setPriceError(null);
    setGradedSlabPrice(certId, priceInputs)
      .then(setDetail)
      .catch((e) => setPriceError(e instanceof Error ? e.message : "Failed"))
      .finally(() => setPriceBusy(false));
  };
  const focusedGrade = grades.find((g) => g.grade === slab.grade);
  const ownedOfGrade = focusedGrade?.qty ?? 0;
  // Live liquid preview from the Value input via the shared macro (the server
  // derives + stores the authoritative value on save).
  const priceNum = Number(priceInputs.estimated_price);
  const liquidPreview =
    priceInputs.estimated_price && Number.isFinite(priceNum) && priceNum >= 0
      ? computeLiquidValue(priceNum)
      : null;

  const chips = [
    identity.finish !== "Regular" ? identity.finish : null,
    identity.specialty_one !== "None" ? identity.specialty_one : null,
    identity.card_variety,
    identity.card_language,
    identity.card_year,
  ].filter(Boolean) as string[];

  const rawFallback = identity.card_id ? cardImageUrl(identity.card_id) : null;

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
        {/* Left: the focused slab — big front + back + the cert-specific action. */}
        <section className={`card-detail-panel slab-focus ${slab.to_crack ? "slab-focus-crack" : ""}`}>
          <h3>
            Slab · {slab.grade_label || `${slab.grading_company} ${gradeText(slab.grade)}`}
          </h3>
          <div className="slab-focus-images">
            <figure className="slab-focus-face">
              <CardImage
                srcs={[slab.cert_id ? gradedImageUrl(slab.cert_id, "front") : null, rawFallback]}
                alt="Front"
              />
              <figcaption>Front</figcaption>
            </figure>
            <figure className="slab-focus-face">
              <CardImage srcs={[slab.cert_id ? gradedImageUrl(slab.cert_id, "back") : null]} alt="Back" />
              <figcaption>Back</figcaption>
            </figure>
          </div>

          <div className="card-detail-attrs">
            {slab.cert_id && <div className="adv-row"><span>Cert #</span><span>{slab.cert_id}</span></div>}
            <div className="adv-row"><span>Grade</span><span>{gradeText(slab.grade)}</span></div>
            <div className="adv-row">
              <span>Status</span>
              <span className={`status-badge status-${slab.status}`}>{slab.status}</span>
            </div>
            {slab.to_crack && <div className="slab-crack-flag">Marked to crack</div>}
          </div>

          <div className="card-detail-qty-actions">
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
        </section>

        {/* Right: spec-level context — every grade's qty/price (read-only). The
            price-over-time graph lands in the reserved area below. */}
        <aside className="card-detail-panel">
          <h3>Grades ({identity.grading_company})</h3>
          <table className="card-table">
            <thead>
              <tr>
                <th>Grade</th>
                <th>Owned</th>
                <th>Population</th>
                <th>Est. Price</th>
              </tr>
            </thead>
            <tbody>
              {grades.map((g) => (
                <tr key={g.graded_sku_id} className={g.grade === slab.grade ? "row-current" : ""}>
                  <td><span className="graded-grade-pill">{gradeText(g.grade)}</span> {g.grade_label || ""}</td>
                  <td>{g.qty}</td>
                  <td>{g.population != null ? g.population : "-"}</td>
                  <td className={`price-cell ${g.estimated_price == null ? "no-price" : ""}`}>
                    {fmt(g.estimated_price)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {/* Manual price editor for the focused grade class. Graded cards have no
              price feed, so this is entered by hand; it's stored per grade, so it
              applies to every owned cert of this card + grade + company. */}
          <div className="graded-price-editor">
            <div className="graded-price-head">
              <span>Set price — {identity.grading_company} {gradeText(slab.grade)}</span>
              <span className="graded-price-note">
                applies to all {ownedOfGrade} owned of this grade
              </span>
            </div>
            <div className="graded-price-fields">
              {([
                ["estimated_price", "Value"],
                ["estimated_low_price", "Low"],
                ["estimated_high_price", "High"],
              ] as const).map(([key, label]) => (
                <label key={key} className="graded-price-field">
                  <span>{label}</span>
                  <input
                    type="number"
                    step="any"
                    min={0}
                    placeholder="$"
                    value={priceInputs[key] ?? ""}
                    disabled={priceBusy}
                    onChange={(e) => setPriceInputs((p) => ({ ...p, [key]: e.target.value }))}
                    onKeyDown={(e) => e.key === "Enter" && savePrice()}
                  />
                </label>
              ))}
              {/* Liquid is derived from Value (shared macro), not entered. */}
              <div className="graded-price-field">
                <span title="Value after fees + shipping (auto)">Liquid</span>
                <output className="graded-price-derived">{fmt(liquidPreview)}</output>
              </div>
            </div>
            <div className="card-detail-qty-actions">
              <button type="button" className="qty-save-btn" onClick={savePrice} disabled={priceBusy}>
                {priceBusy ? "Saving…" : "Save price"}
              </button>
              {priceError && <span className="qty-save-error">{priceError}</span>}
            </div>
          </div>

          <div className="graded-graph-placeholder">
            Price history across grades will graph here.
          </div>

          {/* TCGplayer link editor: set/add or edit the raw-card id for this
              graded card (applies to every grade). Blank unlinks. */}
          <div className="graded-link-editor">
            <label className="graded-link-label">TCGplayer ID</label>
            <div className="graded-link-row">
              <input
                className="search-input"
                placeholder="e.g. 42445 (blank to unlink)"
                value={tcgInput}
                disabled={linkBusy}
                onChange={(e) => setTcgInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && saveLink()}
              />
              <button
                type="button"
                className="qty-save-btn"
                onClick={saveLink}
                disabled={!linkDirty || linkBusy}
              >
                {linkBusy ? "Saving…" : "Save"}
              </button>
            </div>
            {linkError && <span className="qty-save-error">{linkError}</span>}
            {identity.card_id && (
              <div className="card-detail-qty-actions">
                <Link
                  to={`/card/${identity.card_id}?finish=${encodeURIComponent(identity.finish)}&specialty_one=${encodeURIComponent(identity.specialty_one)}`}
                  className="qty-save-btn"
                >
                  View raw card
                </Link>
                {identity.raw_estimated_price != null && (
                  <span style={{ color: "var(--text-muted)", fontSize: 12 }}>Raw ≈ {fmt(identity.raw_estimated_price)}</span>
                )}
              </div>
            )}
          </div>
        </aside>
      </div>
    </div>
  );
}
