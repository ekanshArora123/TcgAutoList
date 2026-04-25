import { useEffect, useState } from "react";
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
  PieChart, Pie, Cell, Legend,
} from "recharts";
import {
  fetchSummary, fetchPriceHistogram, fetchConfidenceDistribution,
  fetchEraBreakdown, fetchConditionBreakdown, fetchRarityBreakdown,
  fetchTopCards, fetchSetBreakdown,
  type Summary, type HistogramBin, type ConfidenceBucket,
  type EraBreakdown, type ConditionBreakdown, type RarityBreakdown,
  type TopCard, type SetBreakdown,
} from "../api";

const COLORS = [
  "#1f6feb", "#3fb950", "#d29922", "#f85149", "#a371f7",
  "#79c0ff", "#56d364", "#e3b341", "#ff7b72", "#bc8cff",
  "#39d353", "#db6d28", "#7ee787", "#ffa657", "#d2a8ff",
];

const fmt = (n: number | null | undefined) => (n != null ? `$${n.toFixed(2)}` : "-");
const fmtK = (n: number | null | undefined) => {
  if (n == null) return "-";
  if (n >= 1000) return `$${(n / 1000).toFixed(1)}k`;
  return `$${n.toFixed(2)}`;
};

const CustomTooltip = ({ active, payload, label }: any) => {
  if (!active || !payload?.length) return null;
  return (
    <div style={{ background: "#161b22", border: "1px solid #30363d", padding: "8px 12px", borderRadius: 6, fontSize: 12 }}>
      <div style={{ color: "#e1e4e8", fontWeight: 600 }}>{label}</div>
      {payload.map((p: any, i: number) => (
        <div key={i} style={{ color: p.color }}>{p.name}: {typeof p.value === "number" ? p.value.toLocaleString() : p.value}</div>
      ))}
    </div>
  );
};

export default function AnalyticsPage() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [histogram, setHistogram] = useState<HistogramBin[]>([]);
  const [confidence, setConfidence] = useState<ConfidenceBucket[]>([]);
  const [eras, setEras] = useState<EraBreakdown[]>([]);
  const [conditions, setConditions] = useState<ConditionBreakdown[]>([]);
  const [rarities, setRarities] = useState<RarityBreakdown[]>([]);
  const [topCards, setTopCards] = useState<TopCard[]>([]);
  const [sets, setSets] = useState<SetBreakdown[]>([]);
  const [histMaxPrice, setHistMaxPrice] = useState(100);
  const [histBinSize, setHistBinSize] = useState(2);

  useEffect(() => {
    fetchSummary().then(setSummary);
    fetchPriceHistogram(histMaxPrice, histBinSize).then(setHistogram);
    fetchConfidenceDistribution().then(setConfidence);
    fetchEraBreakdown().then(setEras);
    fetchConditionBreakdown().then(setConditions);
    fetchRarityBreakdown().then(setRarities);
    fetchTopCards(25).then(setTopCards);
    fetchSetBreakdown().then(setSets);
  }, []);

  const reloadHistogram = () => {
    fetchPriceHistogram(histMaxPrice, histBinSize).then(setHistogram);
  };

  if (!summary) return <div className="loading">Loading analytics...</div>;

  return (
    <div>
      {/* Summary cards */}
      <div className="summary-grid">
        <div className="stat-card">
          <div className="stat-label">Total Cards</div>
          <div className="stat-value">{summary.total_inventory.toLocaleString()}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Unique Cards</div>
          <div className="stat-value">{summary.unique_cards.toLocaleString()}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Total Value</div>
          <div className="stat-value price">{fmtK(summary.total_value)}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Liquid Value</div>
          <div className="stat-value price">{fmtK(summary.total_liquid_value)}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Avg Price</div>
          <div className="stat-value price">{fmt(summary.avg_price)}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Avg Confidence</div>
          <div className="stat-value">{summary.avg_confidence != null ? `${summary.avg_confidence.toFixed(0)}%` : "-"}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Needs Manual Review</div>
          <div className="stat-value">{summary.pending_manual_checks.toLocaleString()}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Max Price</div>
          <div className="stat-value price">{fmt(summary.max_price)}</div>
        </div>
      </div>

      <div className="charts-grid">
        {/* Price histogram */}
        <div className="chart-card full-width">
          <h3>Price Distribution</h3>
          <div className="chart-controls">
            <label>Max Price: <input type="number" value={histMaxPrice} step="any" onChange={(e) => setHistMaxPrice(Number(e.target.value))} /></label>
            <label>Bin Size: <input type="number" value={histBinSize} step="any" min="0.01" onChange={(e) => setHistBinSize(Number(e.target.value))} /></label>
            <span style={{ fontSize: 11, color: "#484f58" }}>Presets:</span>
            {[0.25, 0.5, 1, 2, 5, 10].map((s) => (
              <button
                key={s}
                className="nav-btn"
                style={{ fontSize: 11, padding: "3px 8px", background: histBinSize === s ? "#1f6feb" : undefined, color: histBinSize === s ? "#fff" : undefined }}
                onClick={() => { setHistBinSize(s); fetchPriceHistogram(histMaxPrice, s).then(setHistogram); }}
              >${s}</button>
            ))}
            <button className="nav-btn" onClick={reloadHistogram} style={{ fontSize: 12, padding: "4px 10px" }}>Update</button>
          </div>
          <ResponsiveContainer width="100%" height={300}>
            <BarChart data={histogram}>
              <XAxis dataKey="range" tick={{ fill: "#8b949e", fontSize: 10 }} angle={-45} textAnchor="end" height={60} />
              <YAxis tick={{ fill: "#8b949e", fontSize: 11 }} />
              <Tooltip content={<CustomTooltip />} />
              <Bar dataKey="count" fill="#1f6feb" radius={[2, 2, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* Confidence distribution */}
        <div className="chart-card">
          <h3>Confidence Distribution</h3>
          <ResponsiveContainer width="100%" height={300}>
            <BarChart data={confidence}>
              <XAxis dataKey="bucket" tick={{ fill: "#8b949e", fontSize: 11 }} />
              <YAxis tick={{ fill: "#8b949e", fontSize: 11 }} />
              <Tooltip content={<CustomTooltip />} />
              <Bar dataKey="count" fill="#a371f7" radius={[2, 2, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* Era pie by quantity */}
        <div className="chart-card">
          <h3>Cards by Era (Quantity)</h3>
          <ResponsiveContainer width="100%" height={300}>
            <PieChart>
              <Pie
                data={eras}
                dataKey="quantity"
                nameKey="era"
                cx="50%" cy="50%"
                outerRadius={100}
                label={({ era, percent }: any) => `${era} ${(percent * 100).toFixed(0)}%`}
                labelLine={false}
              >
                {eras.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}
              </Pie>
              <Tooltip content={<CustomTooltip />} />
            </PieChart>
          </ResponsiveContainer>
        </div>

        {/* Era pie by value */}
        <div className="chart-card">
          <h3>Cards by Era (Value)</h3>
          <ResponsiveContainer width="100%" height={300}>
            <PieChart>
              <Pie
                data={eras.filter(e => e.total_value > 0)}
                dataKey="total_value"
                nameKey="era"
                cx="50%" cy="50%"
                outerRadius={100}
                label={({ era, percent }: any) => `${era} ${(percent * 100).toFixed(0)}%`}
                labelLine={false}
              >
                {eras.filter(e => e.total_value > 0).map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}
              </Pie>
              <Tooltip content={<CustomTooltip />} />
            </PieChart>
          </ResponsiveContainer>
        </div>

        {/* Condition breakdown */}
        <div className="chart-card">
          <h3>By Condition</h3>
          <ResponsiveContainer width="100%" height={300}>
            <BarChart data={conditions} layout="vertical">
              <XAxis type="number" tick={{ fill: "#8b949e", fontSize: 11 }} />
              <YAxis dataKey="condition" type="category" tick={{ fill: "#8b949e", fontSize: 11 }} width={60} />
              <Tooltip content={<CustomTooltip />} />
              <Bar dataKey="quantity" fill="#3fb950" radius={[0, 2, 2, 0]} name="Count" />
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* Rarity breakdown */}
        <div className="chart-card">
          <h3>By Rarity (Value)</h3>
          <ResponsiveContainer width="100%" height={300}>
            <BarChart data={rarities.slice(0, 15)} layout="vertical">
              <XAxis type="number" tick={{ fill: "#8b949e", fontSize: 11 }} tickFormatter={(v) => fmtK(v)} />
              <YAxis dataKey="rarity" type="category" tick={{ fill: "#8b949e", fontSize: 10 }} width={120} />
              <Tooltip content={<CustomTooltip />} />
              <Bar dataKey="total_value" fill="#d29922" radius={[0, 2, 2, 0]} name="Total Value" />
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* Top sets by value */}
        <div className="chart-card full-width">
          <h3>Top Sets by Value</h3>
          <ResponsiveContainer width="100%" height={400}>
            <BarChart data={sets.slice(0, 20)}>
              <XAxis dataKey="set_name" tick={{ fill: "#8b949e", fontSize: 9 }} angle={-45} textAnchor="end" height={100} />
              <YAxis tick={{ fill: "#8b949e", fontSize: 11 }} tickFormatter={(v) => fmtK(v)} />
              <Tooltip content={<CustomTooltip />} />
              <Bar dataKey="total_value" fill="#79c0ff" radius={[2, 2, 0, 0]} name="Total Value" />
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* Top 25 most valuable cards */}
        <div className="chart-card full-width">
          <h3>Top 25 Most Valuable Cards</h3>
          <div style={{ overflowX: "auto" }}>
            <table className="top-cards-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Card</th>
                  <th>Set</th>
                  <th>Era</th>
                  <th>Rarity</th>
                  <th>Condition</th>
                  <th>Price</th>
                  <th>Liquid</th>
                  <th>Confidence</th>
                </tr>
              </thead>
              <tbody>
                {topCards.map((card, i) => (
                  <tr key={card.inventory_id}>
                    <td className="rank">{i + 1}</td>
                    <td>{card.card_name}</td>
                    <td>{card.set_name || "-"}</td>
                    <td>{card.era || "-"}</td>
                    <td>{card.rarity || "-"}</td>
                    <td>{card.condition} {card.finish !== "Regular" ? `(${card.finish})` : ""}</td>
                    <td className="price-cell">{fmt(card.estimated_price)}</td>
                    <td className="price-cell">{fmt(card.estimated_liquid_value)}</td>
                    <td>{card.confidence_percent != null ? `${card.confidence_percent}%` : "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}
