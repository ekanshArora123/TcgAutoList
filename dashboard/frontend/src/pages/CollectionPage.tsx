import { useEffect, useState, useCallback } from "react";
import { fetchCards, fetchFilters, type CardItem, type CardsResponse, type Filters } from "../api";

const fmt = (n: number | null) => (n != null ? `$${n.toFixed(2)}` : "-");

export default function CollectionPage() {
  const [data, setData] = useState<CardsResponse | null>(null);
  const [filters, setFilters] = useState<Filters | null>(null);
  const [loading, setLoading] = useState(true);

  // Filter state
  const [q, setQ] = useState("");
  const [setName, setSetName] = useState("");
  const [era, setEra] = useState("");
  const [rarity, setRarity] = useState("");
  const [condition, setCondition] = useState("");
  const [finish, setFinish] = useState("");
  const [status, setStatus] = useState("");
  const [sort, setSort] = useState("card_name");
  const [order, setOrder] = useState("asc");
  const [page, setPage] = useState(1);

  const load = useCallback(async () => {
    setLoading(true);
    const params: Record<string, string> = { page: String(page), per_page: "50", sort, order };
    if (q) params.q = q;
    if (setName) params.set_name = setName;
    if (era) params.era = era;
    if (rarity) params.rarity = rarity;
    if (condition) params.condition = condition;
    if (finish) params.finish = finish;
    if (status) params.status = status;
    const result = await fetchCards(params);
    setData(result);
    setLoading(false);
  }, [q, setName, era, rarity, condition, finish, status, sort, order, page]);

  useEffect(() => {
    fetchFilters().then(setFilters);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const handleSort = (col: string) => {
    if (sort === col) {
      setOrder(order === "asc" ? "desc" : "asc");
    } else {
      setSort(col);
      setOrder("asc");
    }
    setPage(1);
  };

  const sortArrow = (col: string) => {
    if (sort !== col) return "";
    return order === "asc" ? " \u25B2" : " \u25BC";
  };

  // Debounce search
  const [searchInput, setSearchInput] = useState("");
  useEffect(() => {
    const t = setTimeout(() => {
      setQ(searchInput);
      setPage(1);
    }, 300);
    return () => clearTimeout(t);
  }, [searchInput]);

  return (
    <div>
      <div className="filters-bar">
        <input
          className="search-input"
          placeholder="Search card name..."
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
        />
        {filters && (
          <>
            <select className="filter-select" value={era} onChange={(e) => { setEra(e.target.value); setPage(1); }}>
              <option value="">All Eras</option>
              {filters.eras.map((e) => <option key={e} value={e}>{e}</option>)}
            </select>
            <select className="filter-select" value={setName} onChange={(e) => { setSetName(e.target.value); setPage(1); }}>
              <option value="">All Sets</option>
              {filters.sets.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
            <select className="filter-select" value={rarity} onChange={(e) => { setRarity(e.target.value); setPage(1); }}>
              <option value="">All Rarities</option>
              {filters.rarities.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
            <select className="filter-select" value={condition} onChange={(e) => { setCondition(e.target.value); setPage(1); }}>
              <option value="">All Conditions</option>
              {filters.conditions.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
            <select className="filter-select" value={finish} onChange={(e) => { setFinish(e.target.value); setPage(1); }}>
              <option value="">All Finishes</option>
              {filters.finishes.map((f) => <option key={f} value={f}>{f}</option>)}
            </select>
            <select className="filter-select" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
              <option value="">All Statuses</option>
              {filters.statuses.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </>
        )}
      </div>

      {data && <div className="results-count">{data.total.toLocaleString()} cards found</div>}

      {loading ? (
        <div className="loading">Loading...</div>
      ) : data && data.items.length > 0 ? (
        <>
          <div className="card-table-wrapper">
            <table className="card-table">
              <thead>
                <tr>
                  <th onClick={() => handleSort("card_name")}>Name<span className="sort-arrow">{sortArrow("card_name")}</span></th>
                  <th onClick={() => handleSort("set_name")}>Set<span className="sort-arrow">{sortArrow("set_name")}</span></th>
                  <th onClick={() => handleSort("era")}>Era<span className="sort-arrow">{sortArrow("era")}</span></th>
                  <th onClick={() => handleSort("rarity")}>Rarity<span className="sort-arrow">{sortArrow("rarity")}</span></th>
                  <th onClick={() => handleSort("condition")}>Cond<span className="sort-arrow">{sortArrow("condition")}</span></th>
                  <th>Finish</th>
                  <th onClick={() => handleSort("estimated_price")}>Price<span className="sort-arrow">{sortArrow("estimated_price")}</span></th>
                  <th onClick={() => handleSort("status")}>Status<span className="sort-arrow">{sortArrow("status")}</span></th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((card: CardItem) => (
                  <tr key={card.inventory_id}>
                    <td>{card.card_name} {card.card_number ? `(${card.card_number})` : ""}</td>
                    <td>{card.set_name || "-"}</td>
                    <td>{card.era || "-"}</td>
                    <td>{card.rarity || "-"}</td>
                    <td>{card.condition}</td>
                    <td>{card.finish}</td>
                    <td className={`price-cell ${card.estimated_price == null ? "no-price" : ""}`}>
                      {fmt(card.estimated_price)}
                    </td>
                    <td>
                      <span className={`status-badge status-${card.status}`}>
                        {card.status}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="pagination">
            <button disabled={page <= 1} onClick={() => setPage(1)}>First</button>
            <button disabled={page <= 1} onClick={() => setPage(page - 1)}>Prev</button>
            <span className="page-info">
              Page {data.page} of {data.total_pages}
            </span>
            <button disabled={page >= data.total_pages} onClick={() => setPage(page + 1)}>Next</button>
            <button disabled={page >= data.total_pages} onClick={() => setPage(data.total_pages)}>Last</button>
          </div>
        </>
      ) : (
        <div className="loading">No cards found</div>
      )}
    </div>
  );
}
