import { useEffect, useState } from "react";
import MultiSelect from "../components/MultiSelect";
import {
  ComposedChart, BarChart, Bar, Line, XAxis, YAxis, Tooltip, ResponsiveContainer,
  PieChart, Pie, Cell,
} from "recharts";
import {
  fetchSummary, fetchCombinedSummary, fetchPriceHistogram, fetchConfidenceDistribution,
  fetchEraBreakdown, fetchConditionBreakdown, fetchRarityBreakdown,
  fetchTopCards, fetchSetBreakdown, fetchFilters,
  type Summary, type CombinedSummary, type HistogramBin, type ConfidenceBucket,
  type EraBreakdown, type ConditionBreakdown, type RarityBreakdown,
  type TopCard, type SetBreakdown, type Filters,
} from "../api";
import GradedAnalytics from "../components/GradedAnalytics";
import { conditionRank } from "../conditionOrder";

type Kind = "raw" | "graded" | "all";

const COLORS = [
  "#b02a1f", "#1d6a4f", "#b8860b", "#2f6f9f", "#7c3aed", "#c2571c", "#0f766e", "#9d174d", "#4d7c0f", "#5b21b6", "#a16207", "#1e40af", "#be123c", "#047857", "#7c2d12",
];

// Card conditions ordered best -> worst (mirrors PRIMARY_CONDITIONS + the
// in-between grades on the backend). Used to sort the By Condition chart.

const fmt = (n: number | null | undefined) => (n != null ? `$${n.toFixed(2)}` : "-");
const fmtK = (n: number | null | undefined) => {
  if (n == null) return "-";
  if (n >= 1000) return `$${(n / 1000).toFixed(1)}k`;
  return `$${n.toFixed(2)}`;
};

const CustomTooltip = ({ active, payload, label }: any) => {
  if (!active || !payload?.length) return null;
  return (
    <div style={{ background: "var(--surface)", border: "1px solid var(--border)", padding: "8px 12px", borderRadius: 6, fontSize: 12 }}>
      <div style={{ color: "var(--text)", fontWeight: 600 }}>{label}</div>
      {payload.map((p: any, i: number) => (
        <div key={i} style={{ color: p.color }}>{p.name}: {typeof p.value === "number" ? p.value.toLocaleString() : p.value}</div>
      ))}
    </div>
  );
};

export default function AnalyticsPage() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [combined, setCombined] = useState<CombinedSummary | null>(null);
  const [histogram, setHistogram] = useState<HistogramBin[]>([]);
  const [confidence, setConfidence] = useState<ConfidenceBucket[]>([]);
  const [eras, setEras] = useState<EraBreakdown[]>([]);
  const [conditions, setConditions] = useState<ConditionBreakdown[]>([]);
  const [rarities, setRarities] = useState<RarityBreakdown[]>([]);
  const [topCards, setTopCards] = useState<TopCard[]>([]);
  const [sets, setSets] = useState<SetBreakdown[]>([]);

  // Price-distribution filters. Era and Set are mutually exclusive (a set
  // already implies an era), so a toggle picks ONE dimension to filter on;
  // that dimension supports multi-select. Condition is orthogonal. Empty
  // selection = all (no filter).
  const [filterOpts, setFilterOpts] = useState<Filters | null>(null);
  const [groupDim, setGroupDim] = useState<"era" | "set">("era");
  const [selEras, setSelEras] = useState<string[]>([]);
  const [selSets, setSelSets] = useState<string[]>([]);
  const [selConditions, setSelConditions] = useState<string[]>([]);
  const PRESETS: Record<string, number[]> = {
    "Fine": [0, 0.2, 0.5, 1, 2, 3, 5, 10, 20, 30, 50, 100],
    "Standard": [0, 1, 2, 5, 10, 20, 30, 50, 100],
    "Coarse": [0, 5, 10, 25, 50, 100, 250, 500],
    "Under $10": [0, 0.25, 0.5, 1, 2, 3, 4, 5, 7, 10],
    "High Value": [0, 10, 25, 50, 100, 200, 500, 1000],
  };
  const [breaksInput, setBreaksInput] = useState("0, 0.2, 0.5, 1, 2, 5, 10, 20, 30, 50, 100");
  const [activePreset, setActivePreset] = useState<string | null>(null);

  // Kind = which prices/breakdowns to show; crack = graded-only slab filter.
  const [kind, setKind] = useState<Kind>("raw");
  const [crack, setCrack] = useState(""); // "" | "yes" | "no"

  const parseBreaks = (s: string): number[] => {
    const nums = s.split(",").map((b) => parseFloat(b.trim())).filter((n) => !isNaN(n));
    return [...new Set(nums)].sort((a, b) => a - b);
  };

  const histogramFilters = (): Record<string, string | string[]> => {
    const f: Record<string, string | string[]> = { kind };
    // Raw set/era/condition filters only apply to the raw prices.
    if (kind !== "graded") {
      if (groupDim === "era" && selEras.length) f.eras = selEras;
      if (groupDim === "set" && selSets.length) f.sets = selSets;
      if (selConditions.length) f.conditions = selConditions;
    }
    // Crack only applies to the graded prices.
    if (kind !== "raw" && crack) f.crack = crack;
    return f;
  };

  const loadHistogram = (breaks: number[]) => {
    if (breaks.length >= 2) fetchPriceHistogram(breaks, histogramFilters()).then(setHistogram);
  };

  useEffect(() => {
    fetchSummary().then(setSummary);
    fetchCombinedSummary().then(setCombined);
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
  }, [groupDim, selEras, selSets, selConditions, kind, crack]);

  if (!summary) return <div className="loading">Loading analytics...</div>;

  return (
    <div>
      {/* Kind + crack selectors */}
      <div className="analytics-mode-bar">
        <span style={{ fontSize: 12, color: "var(--text-muted)" }}>Show:</span>
        {(["raw", "graded", "all"] as const).map((k) => (
          <button key={k} className={`seg-btn ${kind === k ? "active" : ""}`} style={{ textTransform: "capitalize" }} onClick={() => setKind(k)}>{k}</button>
        ))}
        {kind !== "raw" && (
          <>
            <span style={{ color: "var(--border)", margin: "0 4px" }}>|</span>
            <span style={{ fontSize: 12, color: "var(--text-muted)" }}>Crack:</span>
            {([["", "All"], ["yes", "To crack"], ["no", "Not to crack"]] as const).map(([v, lbl]) => (
              <button key={v} className={`seg-btn ${crack === v ? "active" : ""}`} onClick={() => setCrack(v)}>{lbl}</button>
            ))}
          </>
        )}
      </div>

      {/* Combined totals (All mode) — cross-kind dimensions only. */}
      {kind === "all" && (
        <div className="summary-grid">
          <div className="stat-card">
            <div className="stat-label">Total Cards</div>
            <div className="stat-value">{combined ? combined.total_cards.toLocaleString() : "-"}</div>
          </div>
          <div className="stat-card">
            <div className="stat-label">Total Value</div>
            <div className="stat-value price">{fmtK(combined?.total_value)}</div>
          </div>
          <div className="stat-card">
            <div className="stat-label">Liquid Value</div>
            <div className="stat-value price">{fmtK(combined?.total_liquid_value)}</div>
          </div>
          <div className="stat-card">
            <div className="stat-label">Avg Price</div>
            <div className="stat-value price">{fmt(combined?.avg_price)}</div>
          </div>
          <div className="stat-card">
            <div className="stat-label">Max Price</div>
            <div className="stat-value price">{fmt(combined?.max_price)}</div>
          </div>
        </div>
      )}

      {/* Summary cards (raw) — only in Raw mode; unchanged. */}
      {kind === "raw" && (
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
      )}

      <div className="charts-grid">
        {/* Price histogram — the one chart that pools raw + graded. */}
        <div className="chart-card full-width">
          <h3>Price Distribution{kind === "all" ? " (Raw + Graded)" : kind === "graded" ? " (Graded)" : ""}</h3>
          <div style={{ display: "flex", gap: 16, alignItems: "flex-start" }}>
            <div style={{ flex: 1, minWidth: 0 }}>
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
            <span style={{ fontSize: 11, color: "var(--border-strong)" }}>Presets:</span>
            {Object.entries(PRESETS).map(([name, breaks]) => (
              <button
                key={name}
                className="nav-btn"
                style={{ fontSize: 11, padding: "3px 8px", background: activePreset === name ? "var(--primary)" : undefined, color: activePreset === name ? "#fff" : undefined }}
                onClick={() => {
                  setBreaksInput(breaks.join(", "));
                  setActivePreset(name);
                  loadHistogram(breaks);
                }}
              >{name}</button>
            ))}
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
              <XAxis dataKey="range" tick={{ fill: "var(--text-muted)", fontSize: 10 }} angle={-45} textAnchor="end" height={60} />
              <YAxis yAxisId="left" tick={{ fill: "var(--text-muted)", fontSize: 11 }} />
              <YAxis yAxisId="right" orientation="right" tick={{ fill: "var(--text-muted)", fontSize: 11 }} tickFormatter={(v) => `${v}%`} />
              <Tooltip isAnimationActive={false} content={({ active, payload, label }: any) => {
                if (!active || !payload?.length) return null;
                const d = payload[0]?.payload;
                return (
                  <div style={{ background: "var(--surface)", border: "1px solid var(--border)", padding: "8px 12px", borderRadius: 6, fontSize: 12 }}>
                    <div style={{ color: "var(--text)", fontWeight: 600, marginBottom: 4 }}>{label}</div>
                    <div style={{ color: "var(--primary)" }}>Count: {d?.count?.toLocaleString()}</div>
                    <div style={{ color: "#b8860b" }}>{d?.pctQty}% of cards</div>
                    <div style={{ color: "#1d6a4f" }}>{d?.pctValue}% of value (${d?.total_value?.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })})</div>
                  </div>
                );
              }} />
              <Bar yAxisId="left" dataKey="count" fill="var(--primary)" radius={[2, 2, 0, 0]} name="Count" />
              <Line yAxisId="right" type="monotone" dataKey="pctQty" stroke="#b8860b" strokeWidth={2} dot={{ fill: "#b8860b", r: 3 }} name="% of Cards" />
              <Line yAxisId="right" type="monotone" dataKey="pctValue" stroke="#1d6a4f" strokeWidth={2} dot={{ fill: "#1d6a4f", r: 3 }} name="% of Value" />
            </ComposedChart>
          </ResponsiveContainer>
            </div>

            {/* Filter panel — raw only (set/era/condition don't apply to graded). */}
            {kind !== "graded" && (
            <div style={{ width: 230, flexShrink: 0, borderLeft: "1px solid var(--border)", paddingLeft: 16, display: "flex", flexDirection: "column", gap: 14 }}>
              <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text)" }}>Filters</div>

              <div>
                {/* Era and Set are exclusive — a set already implies its era. */}
                <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 4 }}>Filter by</div>
                <div style={{ display: "flex", gap: 4 }}>
                  {(["era", "set"] as const).map((dim) => (
                    <button
                      key={dim}
                      className="nav-btn"
                      style={{ flex: 1, fontSize: 11, padding: "4px 0", textTransform: "capitalize",
                        background: groupDim === dim ? "var(--primary)" : undefined,
                        color: groupDim === dim ? "#fff" : undefined }}
                      onClick={() => setGroupDim(dim)}
                    >{dim}</button>
                  ))}
                </div>
              </div>

              <div>
                <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 4 }}>{groupDim === "era" ? "Eras" : "Sets"}</div>
                {groupDim === "era" ? (
                  <MultiSelect label="Eras" options={filterOpts?.eras ?? []} selected={selEras} onChange={setSelEras} />
                ) : (
                  <MultiSelect label="Sets" options={filterOpts?.sets ?? []} selected={selSets} onChange={setSelSets} />
                )}
              </div>

              <div>
                <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 4 }}>Condition</div>
                <MultiSelect label="Conditions" options={filterOpts?.conditions ?? []} selected={selConditions} onChange={setSelConditions} />
              </div>

              {(selEras.length > 0 || selSets.length > 0 || selConditions.length > 0) && (
                <button
                  className="nav-btn"
                  style={{ fontSize: 11, padding: "4px 8px", alignSelf: "flex-start" }}
                  onClick={() => { setSelEras([]); setSelSets([]); setSelConditions([]); }}
                >Clear all</button>
              )}
            </div>
            )}
          </div>
        </div>

        {/* Raw breakdowns — hidden in Graded mode (these dimensions don't apply). */}
        {kind !== "graded" && (<>
        {/* Confidence distribution */}
        <div className="chart-card">
          <h3>Confidence Distribution</h3>
          <ResponsiveContainer width="100%" height={300}>
            <BarChart data={confidence}>
              <XAxis dataKey="bucket" tick={{ fill: "var(--text-muted)", fontSize: 11 }} />
              <YAxis tick={{ fill: "var(--text-muted)", fontSize: 11 }} />
              <Tooltip content={<CustomTooltip />} isAnimationActive={false} />
              <Bar dataKey="count" fill="#7c3aed" radius={[2, 2, 0, 0]} />
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
              <XAxis dataKey="condition" tick={{ fill: "var(--text-muted)", fontSize: 11 }} />
              <YAxis yAxisId="left" tick={{ fill: "var(--text-muted)", fontSize: 11 }} />
              <YAxis yAxisId="right" orientation="right" tick={{ fill: "var(--text-muted)", fontSize: 11 }} tickFormatter={(v) => fmtK(v)} />
              <Tooltip isAnimationActive={false} content={({ active, payload, label }: any) => {
                if (!active || !payload?.length) return null;
                const d = payload[0]?.payload;
                return (
                  <div style={{ background: "var(--surface)", border: "1px solid var(--border)", padding: "8px 12px", borderRadius: 6, fontSize: 12 }}>
                    <div style={{ color: "var(--text)", fontWeight: 600, marginBottom: 4 }}>{label}</div>
                    <div style={{ color: "#1d6a4f" }}>Count: {d?.quantity?.toLocaleString()}</div>
                    <div style={{ color: "#b8860b" }}>Value: {fmt(d?.total_value)}</div>
                  </div>
                );
              }} />
              <Bar yAxisId="left" dataKey="quantity" fill="#1d6a4f" radius={[2, 2, 0, 0]} name="Count" />
              <Line yAxisId="right" type="monotone" dataKey="total_value" stroke="#b8860b" strokeWidth={2} dot={{ fill: "#b8860b", r: 3 }} name="Total Value" />
            </ComposedChart>
          </ResponsiveContainer>
        </div>

        {/* Rarity breakdown */}
        <div className="chart-card">
          <h3>By Rarity (Value)</h3>
          <ResponsiveContainer width="100%" height={300}>
            <BarChart data={rarities.slice(0, 15)} layout="vertical">
              <XAxis type="number" tick={{ fill: "var(--text-muted)", fontSize: 11 }} tickFormatter={(v) => fmtK(v)} />
              <YAxis dataKey="rarity" type="category" tick={{ fill: "var(--text-muted)", fontSize: 10 }} width={120} />
              <Tooltip content={<CustomTooltip />} isAnimationActive={false} />
              <Bar dataKey="total_value" fill="#b8860b" radius={[0, 2, 2, 0]} name="Total Value" />
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* Top sets by value */}
        <div className="chart-card full-width">
          <h3>Top Sets by Value</h3>
          <ResponsiveContainer width="100%" height={400}>
            <BarChart data={sets.slice(0, 20)}>
              <XAxis dataKey="set_name" tick={{ fill: "var(--text-muted)", fontSize: 9 }} angle={-45} textAnchor="end" height={100} />
              <YAxis tick={{ fill: "var(--text-muted)", fontSize: 11 }} tickFormatter={(v) => fmtK(v)} />
              <Tooltip content={<CustomTooltip />} isAnimationActive={false} />
              <Bar dataKey="total_value" fill="#2f6f9f" radius={[2, 2, 0, 0]} name="Total Value" />
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
        </>)}
      </div>

      {kind !== "raw" && <GradedAnalytics crack={crack} />}
    </div>
  );
}
