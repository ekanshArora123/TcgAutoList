import { useEffect, useState } from "react";
import {
  ComposedChart, Bar, Line, ScatterChart, Scatter, XAxis, YAxis, ZAxis,
  Tooltip, ResponsiveContainer, PieChart, Pie, Cell,
} from "recharts";
import {
  fetchGradedAnalyticsSummary, fetchGradedBreakdown, fetchGradedPricePoints, fetchTopGradedSlabs,
  type GradedAnalyticsSummary, type GradedBreakdownRow, type GradedPricePoint, type TopGradedSlab,
} from "../api";

const COLORS = [
  "#b02a1f", "#1d6a4f", "#b8860b", "#2f6f9f", "#7c3aed", "#c2571c", "#0f766e", "#9d174d", "#4d7c0f", "#5b21b6",
];
const fmt = (n: number | null | undefined) => (n != null ? `$${n.toFixed(2)}` : "-");
const fmtK = (n: number | null | undefined) => {
  if (n == null) return "-";
  return n >= 1000 ? `$${(n / 1000).toFixed(1)}k` : `$${n.toFixed(2)}`;
};
const gradeText = (g: number) => (g > 0 ? String(g) : "ERR");

// Graded-native analytics (grade / company / grade×price), all scoped by the
// shared `crack` filter. Rendered on the Analytics page in Graded/All mode.
export default function GradedAnalytics({ crack }: { crack: string }) {
  const [summary, setSummary] = useState<GradedAnalyticsSummary | null>(null);
  const [byGrade, setByGrade] = useState<GradedBreakdownRow[]>([]);
  const [byCompany, setByCompany] = useState<GradedBreakdownRow[]>([]);
  const [points, setPoints] = useState<GradedPricePoint[]>([]);
  const [top, setTop] = useState<TopGradedSlab[]>([]);

  useEffect(() => {
    fetchGradedAnalyticsSummary(crack).then(setSummary);
    fetchGradedBreakdown("grade", crack).then(setByGrade);
    fetchGradedBreakdown("company", crack).then(setByCompany);
    fetchGradedPricePoints(crack).then(setPoints);
    fetchTopGradedSlabs(25, crack).then(setTop);
  }, [crack]);

  return (
    <>
      <h2 className="analytics-section-title">Graded</h2>

      {/* Summary tiles */}
      <div className="summary-grid">
        <Tile label="Total Slabs" value={summary ? summary.total_slabs.toLocaleString() : "-"} />
        <Tile label="Graded Value" value={fmtK(summary?.total_value)} price />
        <Tile label="Liquid Value" value={fmtK(summary?.total_liquid_value)} price />
        <Tile label="Avg Price" value={fmt(summary?.avg_price)} price />
        <Tile label="Avg Grade" value={summary?.avg_grade != null ? summary.avg_grade.toFixed(1) : "-"} />
        <Tile label="Marked to Crack" value={summary ? summary.to_crack_count.toLocaleString() : "-"} />
      </div>

      <div className="charts-grid">
        {/* By grade — count + value */}
        <div className="chart-card">
          <h3>By Grade (Count &amp; Value)</h3>
          <ResponsiveContainer width="100%" height={300}>
            <ComposedChart data={byGrade}>
              <XAxis dataKey="label" tick={{ fill: "var(--text-muted)", fontSize: 11 }} />
              <YAxis yAxisId="left" tick={{ fill: "var(--text-muted)", fontSize: 11 }} allowDecimals={false} />
              <YAxis yAxisId="right" orientation="right" tick={{ fill: "var(--text-muted)", fontSize: 11 }} tickFormatter={(v) => fmtK(v)} />
              <Tooltip isAnimationActive={false} content={({ active, payload, label }: any) => {
                if (!active || !payload?.length) return null;
                const d = payload[0]?.payload;
                return (
                  <div className="chart-tooltip">
                    <div className="chart-tooltip-title">Grade {label}</div>
                    <div style={{ color: "#1d6a4f" }}>Slabs: {d?.quantity?.toLocaleString()}</div>
                    <div style={{ color: "#b8860b" }}>Value: {fmt(d?.total_value)}</div>
                    <div style={{ color: "var(--text-muted)" }}>Avg: {fmt(d?.avg_price)}</div>
                  </div>
                );
              }} />
              <Bar yAxisId="left" dataKey="quantity" fill="#1d6a4f" radius={[2, 2, 0, 0]} name="Slabs" />
              <Line yAxisId="right" type="monotone" dataKey="total_value" stroke="#b8860b" strokeWidth={2} dot={{ fill: "#b8860b", r: 3 }} name="Value" />
            </ComposedChart>
          </ResponsiveContainer>
        </div>

        {/* By company — value pie */}
        <div className="chart-card">
          <h3>By Company (Value)</h3>
          <ResponsiveContainer width="100%" height={300}>
            <PieChart>
              <Pie
                data={byCompany.filter((c) => (c.total_value ?? 0) > 0)}
                dataKey="total_value"
                nameKey="label"
                cx="50%" cy="50%"
                outerRadius={100}
                label={({ label, percent }: any) => `${label} ${(percent * 100).toFixed(0)}%`}
                labelLine={false}
              >
                {byCompany.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}
              </Pie>
              <Tooltip isAnimationActive={false} content={({ active, payload }: any) => {
                if (!active || !payload?.length) return null;
                const d = payload[0]?.payload;
                return (
                  <div className="chart-tooltip">
                    <div className="chart-tooltip-title">{d?.label}</div>
                    <div style={{ color: "#1d6a4f" }}>Value: {fmt(d?.total_value)}</div>
                    <div style={{ color: "var(--text-muted)" }}>Slabs: {d?.quantity?.toLocaleString()}</div>
                  </div>
                );
              }} />
            </PieChart>
          </ResponsiveContainer>
        </div>

        {/* Grade × price scatter — one point per slab */}
        <div className="chart-card full-width">
          <h3>Grade &times; Price (per slab)</h3>
          <ResponsiveContainer width="100%" height={340}>
            <ScatterChart margin={{ top: 10, right: 20, bottom: 20, left: 10 }}>
              <XAxis
                type="number" dataKey="grade" name="Grade"
                domain={[0, 10]} tickCount={11}
                tick={{ fill: "var(--text-muted)", fontSize: 11 }}
                label={{ value: "Grade", position: "insideBottom", offset: -8, fill: "var(--text-muted)", fontSize: 12 }}
              />
              <YAxis
                type="number" dataKey="price" name="Price"
                tick={{ fill: "var(--text-muted)", fontSize: 11 }} tickFormatter={(v) => fmtK(v)}
              />
              <ZAxis range={[60, 60]} />
              <Tooltip isAnimationActive={false} cursor={{ strokeDasharray: "3 3" }} content={({ active, payload }: any) => {
                if (!active || !payload?.length) return null;
                const d = payload[0]?.payload as GradedPricePoint;
                return (
                  <div className="chart-tooltip">
                    <div className="chart-tooltip-title">{d.card_name}</div>
                    <div style={{ color: "var(--text-muted)" }}>{d.grading_company} {gradeText(d.grade)}{d.cert_id ? ` · #${d.cert_id}` : ""}</div>
                    <div style={{ color: "#1d6a4f" }}>{fmt(d.price)}</div>
                  </div>
                );
              }} />
              <Scatter data={points} fill="var(--primary)" fillOpacity={0.75} />
            </ScatterChart>
          </ResponsiveContainer>
        </div>

        {/* Top slabs by value */}
        <div className="chart-card full-width">
          <h3>Top Graded Slabs by Value</h3>
          {top.length === 0 ? (
            <div className="card-detail-empty">No priced slabs yet.</div>
          ) : (
            <div style={{ overflowX: "auto" }}>
              <table className="top-cards-table">
                <thead>
                  <tr>
                    <th>#</th><th>Card</th><th>Set</th><th>Year</th><th>Grade</th><th>Company</th><th>Price</th><th>Liquid</th>
                  </tr>
                </thead>
                <tbody>
                  {top.map((s, i) => (
                    <tr key={s.graded_inventory_id}>
                      <td className="rank">{i + 1}</td>
                      <td>{s.card_name}</td>
                      <td>{s.set_name || "-"}</td>
                      <td>{s.card_year || "-"}</td>
                      <td>{s.grade_label || `${s.grading_company} ${gradeText(s.grade)}`}</td>
                      <td>{s.grading_company}</td>
                      <td className="price-cell">{fmt(s.estimated_price)}</td>
                      <td className="price-cell">{fmt(s.estimated_liquid_value)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </>
  );
}

function Tile({ label, value, price }: { label: string; value: string; price?: boolean }) {
  return (
    <div className="stat-card">
      <div className="stat-label">{label}</div>
      <div className={`stat-value ${price ? "price" : ""}`}>{value}</div>
    </div>
  );
}
