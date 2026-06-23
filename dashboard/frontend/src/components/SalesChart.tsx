import { useEffect, useMemo, useState } from "react";
import {
  ComposedChart, Bar, Line, Scatter, XAxis, YAxis, Tooltip, Legend,
  ResponsiveContainer, CartesianGrid,
} from "recharts";
import {
  fetchCardSalesHistory, fetchCardPriceHistory, fetchCardSalesPoints,
  type CardSalesHistory, type CardPriceHistory, type CardSalesPoints,
} from "../api";
import { conditionRank } from "../conditionOrder";

const RANGES = [
  { label: "1D", days: 1 },
  { label: "1W", days: 7 },
  { label: "1M", days: 30 },
  { label: "3M", days: 90 },
  { label: "1Y", days: 365 },
];

// One color per condition (assigned in best->worst order). A condition's median
// line, market line, and scatter points all share its color.
const LINE_COLORS = [
  "#3fb950", "#1f6feb", "#d29922", "#a371f7", "#f85149",
  "#79c0ff", "#ff7b72", "#56d364", "#e3b341",
];

function SalesTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div style={{ background: "#161b22", border: "1px solid #30363d", padding: "8px 12px", borderRadius: 6, fontSize: 12 }}>
      <div style={{ color: "#e1e4e8", marginBottom: 4 }}>{label}</div>
      {payload.map((p: any, i: number) => (
        <div key={`${p.dataKey}-${i}`} style={{ color: p.color }}>
          {p.name}: {p.value == null ? "-" : p.dataKey === "volume" ? p.value : `$${Number(p.value).toFixed(2)}`}
        </div>
      ))}
    </div>
  );
}

export default function SalesChart({ cardId, finish }: { cardId: string; finish: string }) {
  const [days, setDays] = useState(90);
  const [sales, setSales] = useState<CardSalesHistory | null>(null);
  const [prices, setPrices] = useState<CardPriceHistory | null>(null);
  const [rawPoints, setRawPoints] = useState<CardSalesPoints | null>(null);
  const [loading, setLoading] = useState(true);

  // Toggles: which conditions to show, and which layers to draw.
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [showSales, setShowSales] = useState(true);
  const [showMarket, setShowMarket] = useState(true);
  const [showPoints, setShowPoints] = useState(false);

  useEffect(() => {
    setLoading(true);
    Promise.all([
      fetchCardSalesHistory(cardId, finish, days).catch(() => null),
      fetchCardPriceHistory(cardId, finish, days).catch(() => null),
    ])
      .then(([s, p]) => { setSales(s); setPrices(p); })
      .finally(() => setLoading(false));
  }, [cardId, finish, days]);

  // Individual sale points are fetched only when the Points layer is on.
  useEffect(() => {
    if (!showPoints) { setRawPoints(null); return; }
    fetchCardSalesPoints(cardId, finish, days).then(setRawPoints).catch(() => setRawPoints(null));
  }, [cardId, finish, days, showPoints]);

  // Union of conditions across all series, best -> worst. Colors key off this
  // full list so a condition keeps its color regardless of what's filtered.
  const conditions = useMemo(() => {
    const set = new Set<string>();
    sales?.conditions.forEach((c) => set.add(c));
    prices?.conditions.forEach((c) => set.add(c));
    rawPoints?.conditions.forEach((c) => set.add(c));
    return [...set].sort((a, b) => conditionRank(a) - conditionRank(b));
  }, [sales, prices, rawPoints]);

  const colorFor = (c: string) => LINE_COLORS[Math.max(0, conditions.indexOf(c)) % LINE_COLORS.length];
  const visible = conditions.filter((c) => !hidden.has(c));

  const toggleCondition = (c: string) =>
    setHidden((prev) => {
      const next = new Set(prev);
      next.has(c) ? next.delete(c) : next.add(c);
      return next;
    });

  // Merge the line series into one row per date: s:<cond> = median sale price
  // (solid), m:<cond> = market price (dashed), volume = total daily volume (bars).
  const rows = useMemo(() => {
    const byDate: Record<string, any> = {};
    const at = (d: string) => byDate[d] || (byDate[d] = { date: d, volume: 0 });
    if (sales) for (const p of sales.points) {
      const r = at(p.date);
      r[`s:${p.condition}`] = p.median_price;
      r.volume += p.volume;
    }
    if (prices) for (const p of prices.points) {
      at(p.date)[`m:${p.condition}`] = p.market_price;
    }
    return Object.values(byDate).sort((a: any, b: any) => (a.date < b.date ? -1 : 1));
  }, [sales, prices]);

  // Scatter data per condition (each item carries the day category + price).
  const pointsByCondition = useMemo(() => {
    const map: Record<string, { date: string; price: number }[]> = {};
    if (rawPoints) for (const p of rawPoints.points) {
      (map[p.condition] || (map[p.condition] = [])).push({ date: p.date, price: p.price });
    }
    return map;
  }, [rawPoints]);

  return (
    <section className="card-detail-graph">
      <div className="card-detail-graph-head">
        <h3>Sales &amp; Market Price</h3>
        <div className="range-buttons">
          {RANGES.map((r) => (
            <button
              key={r.label}
              className={`range-btn ${days === r.days ? "active" : ""}`}
              onClick={() => setDays(r.days)}
            >
              {r.label}
            </button>
          ))}
        </div>
      </div>

      {conditions.length > 0 && (
        <div className="chart-controls">
          <div className="control-group">
            <span className="control-label">Conditions:</span>
            <button
              className={`range-btn ${hidden.size === 0 ? "active" : ""}`}
              onClick={() => setHidden(new Set())}
            >
              All
            </button>
            {conditions.map((c) => {
              const on = !hidden.has(c);
              return (
                <button
                  key={c}
                  className={`range-btn ${on ? "active" : ""}`}
                  style={on ? { borderColor: colorFor(c) } : undefined}
                  onClick={() => toggleCondition(c)}
                >
                  {c}
                </button>
              );
            })}
          </div>
          <div className="control-group">
            <span className="control-label">Series:</span>
            <button className={`range-btn ${showSales ? "active" : ""}`} onClick={() => setShowSales((v) => !v)}>
              Sales
            </button>
            <button className={`range-btn ${showMarket ? "active" : ""}`} onClick={() => setShowMarket((v) => !v)}>
              Market
            </button>
            <button className={`range-btn ${showPoints ? "active" : ""}`} onClick={() => setShowPoints((v) => !v)}>
              Points
            </button>
          </div>
        </div>
      )}

      {loading ? (
        <div className="card-detail-graph-placeholder">Loading...</div>
      ) : rows.length === 0 ? (
        <div className="card-detail-graph-placeholder">No sales or price history in this range.</div>
      ) : (
        <>
          <ResponsiveContainer width="100%" height={420}>
            <ComposedChart data={rows} margin={{ top: 10, right: 12, bottom: 0, left: 0 }}>
              <CartesianGrid stroke="#21262d" vertical={false} />
              <XAxis dataKey="date" type="category" allowDuplicatedCategory={false} tick={{ fill: "#8b949e", fontSize: 11 }} minTickGap={28} />
              <YAxis yAxisId="price" tick={{ fill: "#8b949e", fontSize: 11 }} tickFormatter={(v) => `$${v}`} />
              <YAxis yAxisId="vol" orientation="right" tick={{ fill: "#8b949e", fontSize: 11 }} allowDecimals={false} />
              <Tooltip content={<SalesTooltip />} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Bar yAxisId="vol" dataKey="volume" name="Volume" fill="#30363d" barSize={10} />
              {showMarket && visible.map((c) => (
                <Line
                  key={`m:${c}`}
                  yAxisId="price"
                  type="monotone"
                  dataKey={`m:${c}`}
                  name={`${c} market`}
                  stroke={colorFor(c)}
                  strokeDasharray="5 3"
                  dot={false}
                  connectNulls
                  strokeWidth={1.5}
                />
              ))}
              {showSales && visible.map((c) => (
                <Line
                  key={`s:${c}`}
                  yAxisId="price"
                  type="monotone"
                  dataKey={`s:${c}`}
                  name={`${c} median`}
                  stroke={colorFor(c)}
                  dot={false}
                  connectNulls
                  strokeWidth={2}
                />
              ))}
              {showPoints && visible.map((c) => (
                <Scatter
                  key={`p:${c}`}
                  yAxisId="price"
                  data={pointsByCondition[c] || []}
                  dataKey="price"
                  name={`${c} sales pts`}
                  fill={colorFor(c)}
                  fillOpacity={0.35}
                  isAnimationActive={false}
                />
              ))}
            </ComposedChart>
          </ResponsiveContainer>
          <div className="card-detail-graph-note">
            Solid = median sale price · dashed = TCGplayer market price · bars = sales volume
            {showPoints ? " · dots = individual sales (one per unit; overlaps show as denser)" : ""}
          </div>
        </>
      )}
    </section>
  );
}
