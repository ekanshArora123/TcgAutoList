import { useEffect, useState } from "react";
import {
  ComposedChart, BarChart, Bar, Line, XAxis, YAxis, Tooltip, ResponsiveContainer,
  PieChart, Pie, Cell,
} from "recharts";
import {
  fetchSummary, fetchPriceHistogram, fetchConfidenceDistribution,
  fetchEraBreakdown, fetchConditionBreakdown, fetchRarityBreakdown,
  fetchTopCards, fetchSetBreakdown, fetchFilters,
  type Summary, type HistogramBin, type ConfidenceBucket,
  type EraBreakdown, type ConditionBreakdown, type RarityBreakdown,
  type TopCard, type SetBreakdown, type Filters,
} from "../api";

const COLORS = [
  "#1f6feb", "#3fb950", "#d29922", "#f85149", "#a371f7",
  "#79c0ff", "#56d364", "#e3b341", "#ff7b72", "#bc8cff",
  "#39d353", "#db6d28", "#7ee787", "#ffa657", "#d2a8ff",
];

// Card conditions ordered best -> worst (mirrors PRIMARY_CONDITIONS + the
// in-between grades on the backend). Used to sort the By Condition chart.
const CONDITION_ORDER = ["MINT", "NM", "LP-NM", "LP", "MP-LP", "MP", "HP-MP", "HP", "DMG"];
const conditionRank = (c: string) => {
  const i = CONDITION_ORDER.indexOf(c);
  return i === -1 ? CONDITION_ORDER.length : i; // unknown grades sort last
};

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

  // Price-distribution filters (mirror the collection page; default = All).
  const [filterOpts, setFilterOpts] = useState<Filters | null>(null);
  const [hEra, setHEra] = useState("");
  const [hSet, setHSet] = useState("");
  const [hCondition, setHCondition] = useState("");
  const PRESETS: Record<string, number[]> = {
    "Fine": [0, 0.2, 0.5, 1, 2, 3, 5, 10, 20, 30, 50, 100],
    "Standard": [0, 1, 2, 5, 10, 20, 30, 50, 100],
    "Coarse": [0, 5, 10, 25, 50, 100, 250, 500],
    "Under $10": [0, 0.25, 0.5, 1, 2, 3, 4, 5, 7, 10],
    "High Value": [0, 10, 25, 50, 100, 200, 500, 1000],
  };
  const [breaksInput, setBreaksInput] = useState("0, 0.2, 0.5, 1, 2, 5, 10, 20, 30, 50, 100");
  const [activePreset, setActivePreset] = useState<string | null>(null);

  const parseBreaks = (s: string): number[] => {
    const nums = s.split(",").map((b) => parseFloat(b.trim())).filter((n) => !isNaN(n));
    return [...new Set(nums)].sort((a, b) => a - b);
  };

  const histogramFilters = (): Record<string, string> => {
    const f: Record<string, string> = {};
    if (hEra) f.era = hEra;
    if (hSet) f.set_name = hSet;
    if (hCondition) f.condition = hCondition;
    return f;
  };

  const loadHistogram = (breaks: number[]) => {
    if (breaks.length >= 2) fetchPriceHistogram(breaks, histogramFilters()).then(setHistogram);
  };

  useEffect(() => {
    fetchSummary().then(setSummary);
    fetchFilters().then(setFilterOpts);
    fetchConfidenceDistribution().then(setConfidence);
    fetchEraBreakdown().then(setEras);
    fetchConditionBreakdown().then(setConditions);
    fetchRarityBreakdown().then(setRarities);
    fetchTopCards(25).then(setTopCards);
    fetchSetBreakdown().then(setSets);
  }, []);

  // Reload the histogram on mount and whenever a filter changes (reads fresh
  // selection state, avoiding stale closures from the dropdown handlers).
  useEffect(() => {
    loadHistogram(parseBreaks(breaksInput));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hEra, hSet, hCondition]);

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
            <label>Breakpoints: <input
              type="text"
              value={breaksInput}
              onChange={(e) => { setBreaksInput(e.target.value); setActivePreset(null); }}
              style={{ width: 320 }}
              placeholder="0, 1, 5, 10, 50, 100"
            /></label>
            <button className="nav-btn" onClick={() => loadHistogram(parseBreaks(breaksInput))} style={{ fontSize: 12, padding: "4px 10px" }}>Update</button>
          </div>
          <div className="chart-controls">
            <span style={{ fontSize: 11, color: "#484f58" }}>Presets:</span>
            {Object.entries(PRESETS).map(([name, breaks]) => (
              <button
                key={name}
                className="nav-btn"
                style={{ fontSize: 11, padding: "3px 8px", background: activePreset === name ? "#1f6feb" : undefined, color: activePreset === name ? "#fff" : undefined }}
                onClick={() => {
                  setBreaksInput(breaks.join(", "));
                  setActivePreset(name);
                  loadHistogram(breaks);
                }}
              >{name}</button>
            ))}
          </div>
          <div className="chart-controls">
            <span style={{ fontSize: 11, color: "#484f58" }}>Filters:</span>
            <select className="filter-select" value={hEra} onChange={(e) => setHEra(e.target.value)}>
              <option value="">All Eras</option>
              {filterOpts?.eras.map((e) => <option key={e} value={e}>{e}</option>)}
            </select>
            <select className="filter-select" value={hSet} onChange={(e) => setHSet(e.target.value)}>
              <option value="">All Sets</option>
              {filterOpts?.sets.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
            <select className="filter-select" value={hCondition} onChange={(e) => setHCondition(e.target.value)}>
              <option value="">All Conditions</option>
              {filterOpts?.conditions.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
            {(hEra || hSet || hCondition) && (
              <button
                className="nav-btn"
                style={{ fontSize: 11, padding: "3px 8px" }}
                onClick={() => { setHEra(""); setHSet(""); setHCondition(""); }}
              >Clear</button>
            )}
          </div>
          <ResponsiveContainer width="100%" height={320}>
            <ComposedChart data={(() => {
              const totalCount = histogram.reduce((s, b) => s + b.count, 0);
              const totalValue = histogram.reduce((s, b) => s + b.total_value, 0);
              return histogram.map((b) => ({
                ...b,
                pctQty: totalCount > 0 ? +((b.count / totalCount) * 100).toFixed(1) : 0,
                pctValue: totalValue > 0 ? +((b.total_value / totalValue) * 100).toFixed(1) : 0,
              }));
            })()}>
              <XAxis dataKey="range" tick={{ fill: "#8b949e", fontSize: 10 }} angle={-45} textAnchor="end" height={60} />
              <YAxis yAxisId="left" tick={{ fill: "#8b949e", fontSize: 11 }} />
              <YAxis yAxisId="right" orientation="right" tick={{ fill: "#8b949e", fontSize: 11 }} tickFormatter={(v) => `${v}%`} />
              <Tooltip isAnimationActive={false} content={({ active, payload, label }: any) => {
                if (!active || !payload?.length) return null;
                const d = payload[0]?.payload;
                return (
                  <div style={{ background: "#161b22", border: "1px solid #30363d", padding: "8px 12px", borderRadius: 6, fontSize: 12 }}>
                    <div style={{ color: "#e1e4e8", fontWeight: 600, marginBottom: 4 }}>{label}</div>
                    <div style={{ color: "#1f6feb" }}>Count: {d?.count?.toLocaleString()}</div>
                    <div style={{ color: "#d29922" }}>{d?.pctQty}% of cards</div>
                    <div style={{ color: "#3fb950" }}>{d?.pctValue}% of value (${d?.total_value?.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })})</div>
                  </div>
                );
              }} />
              <Bar yAxisId="left" dataKey="count" fill="#1f6feb" radius={[2, 2, 0, 0]} name="Count" />
              <Line yAxisId="right" type="monotone" dataKey="pctQty" stroke="#d29922" strokeWidth={2} dot={{ fill: "#d29922", r: 3 }} name="% of Cards" />
              <Line yAxisId="right" type="monotone" dataKey="pctValue" stroke="#3fb950" strokeWidth={2} dot={{ fill: "#3fb950", r: 3 }} name="% of Value" />
            </ComposedChart>
          </ResponsiveContainer>
        </div>

        {/* Confidence distribution */}
        <div className="chart-card">
          <h3>Confidence Distribution</h3>
          <ResponsiveContainer width="100%" height={300}>
            <BarChart data={confidence}>
              <XAxis dataKey="bucket" tick={{ fill: "#8b949e", fontSize: 11 }} />
              <YAxis tick={{ fill: "#8b949e", fontSize: 11 }} />
              <Tooltip content={<CustomTooltip />} isAnimationActive={false} />
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
              <Tooltip content={<CustomTooltip />} isAnimationActive={false} />
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
              <Tooltip content={<CustomTooltip />} isAnimationActive={false} />
            </PieChart>
          </ResponsiveContainer>
        </div>

        {/* Condition breakdown — count + value */}
        <div className="chart-card">
          <h3>By Condition (Count &amp; Value)</h3>
          <ResponsiveContainer width="100%" height={300}>
            <ComposedChart data={[...conditions].sort((a, b) => conditionRank(a.condition) - conditionRank(b.condition))}>
              <XAxis dataKey="condition" tick={{ fill: "#8b949e", fontSize: 11 }} />
              <YAxis yAxisId="left" tick={{ fill: "#8b949e", fontSize: 11 }} />
              <YAxis yAxisId="right" orientation="right" tick={{ fill: "#8b949e", fontSize: 11 }} tickFormatter={(v) => fmtK(v)} />
              <Tooltip isAnimationActive={false} content={({ active, payload, label }: any) => {
                if (!active || !payload?.length) return null;
                const d = payload[0]?.payload;
                return (
                  <div style={{ background: "#161b22", border: "1px solid #30363d", padding: "8px 12px", borderRadius: 6, fontSize: 12 }}>
                    <div style={{ color: "#e1e4e8", fontWeight: 600, marginBottom: 4 }}>{label}</div>
                    <div style={{ color: "#3fb950" }}>Count: {d?.quantity?.toLocaleString()}</div>
                    <div style={{ color: "#d29922" }}>Value: {fmt(d?.total_value)}</div>
                  </div>
                );
              }} />
              <Bar yAxisId="left" dataKey="quantity" fill="#3fb950" radius={[2, 2, 0, 0]} name="Count" />
              <Line yAxisId="right" type="monotone" dataKey="total_value" stroke="#d29922" strokeWidth={2} dot={{ fill: "#d29922", r: 3 }} name="Total Value" />
            </ComposedChart>
          </ResponsiveContainer>
        </div>

        {/* Rarity breakdown */}
        <div className="chart-card">
          <h3>By Rarity (Value)</h3>
          <ResponsiveContainer width="100%" height={300}>
            <BarChart data={rarities.slice(0, 15)} layout="vertical">
              <XAxis type="number" tick={{ fill: "#8b949e", fontSize: 11 }} tickFormatter={(v) => fmtK(v)} />
              <YAxis dataKey="rarity" type="category" tick={{ fill: "#8b949e", fontSize: 10 }} width={120} />
              <Tooltip content={<CustomTooltip />} isAnimationActive={false} />
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
              <Tooltip content={<CustomTooltip />} isAnimationActive={false} />
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
