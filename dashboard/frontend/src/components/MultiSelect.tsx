import { useEffect, useRef, useState } from "react";

interface MultiSelectProps {
  /** Plural noun for the summary line, e.g. "Eras" -> "All Eras". */
  label: string;
  options: string[];
  selected: string[];
  onChange: (values: string[]) => void;
}

/** Checkbox dropdown for multi-selecting from a list. Empty selection = all. */
export default function MultiSelect({ label, options, selected, onChange }: MultiSelectProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const ref = useRef<HTMLDivElement>(null);

  // Close when clicking outside.
  useEffect(() => {
    const onDocClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  const toggle = (opt: string) =>
    onChange(selected.includes(opt) ? selected.filter((o) => o !== opt) : [...selected, opt]);

  const summary =
    selected.length === 0 ? `All ${label}` : selected.length === 1 ? selected[0] : `${selected.length} selected`;

  const shown = query
    ? options.filter((o) => o.toLowerCase().includes(query.toLowerCase()))
    : options;

  return (
    <div ref={ref} style={{ position: "relative", width: "100%" }}>
      <button
        type="button"
        className="filter-select"
        onClick={() => setOpen((o) => !o)}
        style={{ width: "100%", cursor: "pointer", display: "flex", justifyContent: "space-between", alignItems: "center", gap: 6 }}
      >
        <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{summary}</span>
        <span style={{ color: "var(--text-muted)", flexShrink: 0 }}>{open ? "▴" : "▾"}</span>
      </button>
      {open && (
        <div
          style={{
            position: "absolute", top: "calc(100% + 4px)", left: 0, right: 0, zIndex: 30,
            background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 6,
            boxShadow: "0 6px 16px rgba(0,0,0,0.5)", padding: 6,
          }}
        >
          <div style={{ display: "flex", gap: 6, marginBottom: 6 }}>
            <input
              type="text"
              placeholder={`Search ${label.toLowerCase()}...`}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              style={{ flex: 1, minWidth: 0, fontSize: 12, padding: "3px 6px", background: "var(--bg-sunken)", color: "var(--text)", border: "1px solid var(--border)", borderRadius: 4 }}
            />
            {selected.length > 0 && (
              <button className="nav-btn" style={{ fontSize: 11, padding: "2px 8px" }} onClick={() => onChange([])}>
                Clear
              </button>
            )}
          </div>
          <div style={{ maxHeight: 220, overflowY: "auto" }}>
            {shown.length === 0 && <div style={{ padding: 6, color: "var(--text-muted)", fontSize: 12 }}>No matches</div>}
            {shown.map((opt) => (
              <label
                key={opt}
                style={{ display: "flex", alignItems: "center", gap: 6, padding: "4px 6px", fontSize: 12, color: "var(--text)", cursor: "pointer", borderRadius: 4 }}
              >
                <input type="checkbox" checked={selected.includes(opt)} onChange={() => toggle(opt)} />
                <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{opt}</span>
              </label>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
