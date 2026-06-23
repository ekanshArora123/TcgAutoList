import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { fetchCardDetail, cardImageUrl, type CardDetail } from "../api";
import { conditionRank } from "../conditionOrder";
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

  useEffect(() => {
    if (!cardId) return;
    setLoading(true);
    setNotFound(false);
    fetchCardDetail(cardId, { finish, specialty_one, specialty_two })
      .then((d) => setDetail(d))
      .catch(() => setNotFound(true))
      .finally(() => setLoading(false));
  }, [cardId, finish, specialty_one, specialty_two]);

  if (loading) return <div className="loading">Loading...</div>;
  if (notFound || !detail) return <div className="loading">Card not found</div>;

  const { card } = detail;
  const isFirstEdition = specialty_one === "1st Edition" || specialty_one === "First Edition";
  const sortedConditions = [...detail.conditions].sort(
    (a, b) => conditionRank(a.condition) - conditionRank(b.condition),
  );

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

          {/* Owned-by-condition rollup (read-only; folds every condition of this
              variant together, excluding tagged cards). */}
          <div className="card-detail-panel">
            <h3>Owned by Condition</h3>
            {sortedConditions.length > 0 ? (
              <table className="card-table">
                <thead>
                  <tr>
                    <th>Condition</th>
                    <th>Qty</th>
                    <th>Est. Price</th>
                  </tr>
                </thead>
                <tbody>
                  {sortedConditions.map((row) => (
                    <tr key={row.condition}>
                      <td>{row.condition}</td>
                      <td>{row.qty}</td>
                      <td className={`price-cell ${row.estimated_price == null ? "no-price" : ""}`}>
                        {fmt(row.estimated_price)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <div className="card-detail-empty">No untagged copies of this variant in the collection.</div>
            )}
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
