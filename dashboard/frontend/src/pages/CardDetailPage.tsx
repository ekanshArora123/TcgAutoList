import { useEffect, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { fetchCardDetail, setCardQuantities, cardImageUrl, type CardDetail } from "../api";
import { CONDITION_ORDER, conditionRank } from "../conditionOrder";
import SalesChart from "../components/SalesChart";

const fmt = (n: number | null | undefined) => (n != null ? `$${n.toFixed(2)}` : "-");

export default function CardDetailPage() {
  const { cardId } = useParams<{ cardId: string }>();
  const [params] = useSearchParams();
  const finish = params.get("finish") || "Regular";
  const specialty_one = params.get("specialty_one") || "None";
  const specialty_two = params.get("specialty_two") || "None";

  const [detail, setDetail] = useState<CardDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);
  const [imgError, setImgError] = useState(false);

  // Owned-by-condition editor: local qty inputs keyed by condition, re-seeded
  // whenever the detail (re)loads. Kept as strings so a cleared box isn't forced
  // back to 0 mid-edit.
  const [qtyInputs, setQtyInputs] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  useEffect(() => {
    if (!cardId) return;
    setLoading(true);
    setNotFound(false);
    fetchCardDetail(cardId, { finish, specialty_one, specialty_two })
      .then((d) => setDetail(d))
      .catch(() => setNotFound(true))
      .finally(() => setLoading(false));
  }, [cardId, finish, specialty_one, specialty_two]);

  // Every canonical grade gets a row (so an unowned condition can be added), plus
  // any owned condition outside the canonical list, sorted best -> worst.
  const detailConditions = detail?.conditions;
  const rows = useMemo(() => {
    const byCond = new Map((detailConditions ?? []).map((c) => [c.condition.toUpperCase(), c]));
    const names = new Set<string>(CONDITION_ORDER);
    for (const c of detailConditions ?? []) names.add(c.condition.toUpperCase());
    return [...names]
      .sort((a, b) => conditionRank(a) - conditionRank(b))
      .map((condition) => ({ condition, row: byCond.get(condition) ?? null }));
  }, [detailConditions]);

  // Seed the inputs from the current owned qty each time the detail changes.
  useEffect(() => {
    const seed: Record<string, string> = {};
    for (const { condition, row } of rows) seed[condition] = String(row?.qty ?? 0);
    setQtyInputs(seed);
    setSaveError(null);
  }, [rows]);

  if (loading) return <div className="loading">Loading...</div>;
  if (notFound || !detail) return <div className="loading">Card not found</div>;

  const { card } = detail;
  const isFirstEdition = specialty_one === "1st Edition" || specialty_one === "First Edition";

  // Only send conditions whose value actually changed (and is a valid number).
  const changed = rows.filter(({ condition, row }) => {
    const raw = qtyInputs[condition];
    if (raw === undefined || raw.trim() === "") return false;
    const n = Number(raw);
    return Number.isInteger(n) && n >= 0 && n !== (row?.qty ?? 0);
  });
  const dirty = changed.length > 0;

  const handleSave = () => {
    if (!cardId || !dirty || saving) return;
    const quantities: Record<string, number> = {};
    for (const { condition } of changed) quantities[condition] = Number(qtyInputs[condition]);
    setSaving(true);
    setSaveError(null);
    setCardQuantities(cardId, { finish, specialty_one, specialty_two }, quantities)
      .then((d) => setDetail(d))
      .catch((e) => setSaveError(e instanceof Error ? e.message : "Save failed"))
      .finally(() => setSaving(false));
  };

  // Variant chips that distinguish this page from the plain card.
  const variantTags: string[] = [];
  if (finish && finish !== "Regular") variantTags.push(finish);
  if (isFirstEdition) variantTags.push("1st Ed");
  else if (specialty_one !== "None") variantTags.push(specialty_one);
  if (specialty_two !== "None") variantTags.push(specialty_two);

  return (
    <div className="card-detail">
      <Link to="/" className="card-detail-back">&larr; Back to collection</Link>

      <div className="card-detail-header">
        <h2 className="card-detail-name">
          {card.card_name}
          {card.card_number ? <span className="card-number"> ({card.card_number})</span> : ""}
        </h2>
        <div className="card-detail-set">{card.set_name || "Unknown Set"}</div>
        <div className="card-detail-chips">
          {variantTags.map((t) => <span key={t} className="tag tag-special">{t}</span>)}
        </div>
      </div>

      <div className="card-detail-grid">
        {/* Left: image + basic info */}
        <aside className="card-detail-left">
          <div className="card-detail-image">
            {detail.has_image && !imgError ? (
              <img src={cardImageUrl(card.card_id)} alt={card.card_name} onError={() => setImgError(true)} />
            ) : (
              <div className="card-tile-no-image"><span>{card.card_name}</span></div>
            )}
          </div>

          <div className="card-detail-panel">
            <h3>Card Info</h3>
            <div className="card-detail-attrs">
              <div className="adv-row"><span>Set</span><span>{card.set_name || "-"}</span></div>
              <div className="adv-row"><span>Era</span><span>{card.era || "-"}</span></div>
              <div className="adv-row"><span>Rarity</span><span>{card.rarity || "-"}</span></div>
              <div className="adv-row"><span>Type</span><span>{card.card_type || "-"}</span></div>
              <div className="adv-row"><span>Finish</span><span>{finish}</span></div>
              <div className="adv-row"><span>TCGplayer ID</span><span>{card.card_id}</span></div>
              <div className="adv-row"><span>Total owned</span><span>{detail.total_qty}</span></div>
            </div>
          </div>

          {/* Owned-by-condition editor: one editable qty box per grade (folds
              every condition of this variant together, excluding tagged cards).
              Absolute set — type a new count and Save. */}
          <div className="card-detail-panel">
            <h3>Owned by Condition</h3>
            <table className="card-table">
              <thead>
                <tr>
                  <th>Condition</th>
                  <th>Qty</th>
                  <th>Est. Price</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(({ condition, row }) => (
                  <tr key={condition}>
                    <td>{condition}</td>
                    <td>
                      <input
                        type="number"
                        min={0}
                        step={1}
                        className="qty-input"
                        value={qtyInputs[condition] ?? ""}
                        disabled={saving}
                        onChange={(e) =>
                          setQtyInputs((prev) => ({ ...prev, [condition]: e.target.value }))
                        }
                        onKeyDown={(e) => e.key === "Enter" && handleSave()}
                      />
                    </td>
                    <td className={`price-cell ${row?.estimated_price == null ? "no-price" : ""}`}>
                      {fmt(row?.estimated_price)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="card-detail-qty-actions">
              <button
                type="button"
                className="qty-save-btn"
                onClick={handleSave}
                disabled={!dirty || saving}
              >
                {saving ? "Saving…" : "Save quantities"}
              </button>
              {saveError && <span className="qty-save-error">{saveError}</span>}
            </div>
          </div>
        </aside>

        {/* Middle: sales history graph (one line per condition). */}
        <SalesChart cardId={card.card_id} finish={finish} />

        {/* Right: pricing analytics — placeholder. */}
        <aside className="card-detail-analytics">
          <h3>Pricing Analytics</h3>
          <div className="card-detail-placeholder">
            Market trends, sales history, and condition spreads for this card
            will appear here.
          </div>
        </aside>
      </div>
    </div>
  );
}
