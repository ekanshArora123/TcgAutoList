import { useEffect, useMemo, useState } from "react";
import {
  ComposedChart, Bar, Line, XAxis, YAxis, Tooltip, Legend,
  ResponsiveContainer, CartesianGrid,
} from "recharts";
import { fetchCardSalesHistory, type CardSalesHistory } from "../api";
import { conditionRank } from "../conditionOrder";

const RANGES = [
  { label: "1D", days: 1 },
  { label: "1W", days: 7 },
  { label: "1M", days: 30 },
  { label: "3M", days: 90 },
  { label: "1Y", days: 365 },
];

// One color per condition line (assigned in best->worst order).
const LINE_COLORS = [
  "#3fb950", "#1f6feb", "#d29922", "#a371f7", "#f85149",
  "#79c0ff", "#ff7b72", "#56d364", "#e3b341",
];

function SalesTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div style={{ background: "#161b22", border: "1px solid #30363d", padding: "8px 12px", borderRadius: 6, fontSize: 12 }}>
      <div style={{ color: "#e1e4e8", marginBottom: 4 }}>{label}</div>
      {payload.map((p: any) => (
        <div key={p.dataKey} style={{ color: p.color }}>
          {p.name}: {p.value == null ? "-" : p.dataKey === "volume" ? p.value : `$${Number(p.value).toFixed(2)}`}
        </div>
      ))}
    </div>
  );
}

export default function SalesChart({ cardId, finish }: { cardId: string; finish: string }) {
  const [days, setDays] = useState(90);
  const [data, setData] = useState<CardSalesHistory | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetchCardSalesHistory(cardId, finish, days)
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [cardId, finish, days]);

  const conditions = useMemo(
    () => (data ? [...data.conditions].sort((a, b) => conditionRank(a) - conditionRank(b)) : []),
    [data],
  );

  // Pivot the per-(condition,date) points into one row per date: a price field
  // per condition plus the total volume across conditions for the bars.
  const rows = useMemo(() => {
    if (!data) return [];
    const byDate: Record<string, any> = {};
    for (const p of data.points) {
      const row = byDate[p.date] || (byDate[p.date] = { date: p.date, volume: 0 });
      row[p.condition] = p.avg_price;
      row.volume += p.volume;
    }
    return Object.values(byDate).sort((a: any, b: any) => (a.date < b.date ? -1 : 1));
  }, [data]);

  return (
    <section className="card-detail-graph">
      <div className="card-detail-graph-head">
        <h3>Sales History</h3>
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

      {loading ? (
        <div className="card-detail-graph-placeholder">Loading...</div>
      ) : rows.length === 0 ? (
        <div className="card-detail-graph-placeholder">No sales recorded in this range.</div>
      ) : (
        <ResponsiveContainer width="100%" height={420}>
          <ComposedChart data={rows} margin={{ top: 10, right: 12, bottom: 0, left: 0 }}>
            <CartesianGrid stroke="#21262d" vertical={false} />
            <XAxis dataKey="date" tick={{ fill: "#8b949e", fontSize: 11 }} minTickGap={28} />
            <YAxis yAxisId="price" tick={{ fill: "#8b949e", fontSize: 11 }} tickFormatter={(v) => `$${v}`} />
            <YAxis yAxisId="vol" orientation="right" tick={{ fill: "#8b949e", fontSize: 11 }} allowDecimals={false} />
            <Tooltip content={<SalesTooltip />} />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            <Bar yAxisId="vol" dataKey="volume" name="Volume" fill="#30363d" barSize={10} />
            {conditions.map((c, i) => (
              <Line
                key={c}
                yAxisId="price"
                type="monotone"
                dataKey={c}
                name={c}
                stroke={LINE_COLORS[i % LINE_COLORS.length]}
                dot={false}
                connectNulls
                strokeWidth={2}
              />
            ))}
          </ComposedChart>
        </ResponsiveContainer>
      )}
    </section>
  );
}
