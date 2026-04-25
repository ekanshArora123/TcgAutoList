import { useState } from "react";
import CollectionPage from "./pages/CollectionPage";
import AnalyticsPage from "./pages/AnalyticsPage";
import "./App.css";

type Page = "collection" | "analytics";

function App() {
  const [page, setPage] = useState<Page>("collection");

  return (
    <div className="app">
      <nav className="navbar">
        <h1 className="nav-title">TCG Collection Dashboard</h1>
        <div className="nav-links">
          <button
            className={`nav-btn ${page === "collection" ? "active" : ""}`}
            onClick={() => setPage("collection")}
          >
            Collection
          </button>
          <button
            className={`nav-btn ${page === "analytics" ? "active" : ""}`}
            onClick={() => setPage("analytics")}
          >
            Analytics
          </button>
        </div>
      </nav>
      <main className="main-content">
        {page === "collection" ? <CollectionPage /> : <AnalyticsPage />}
      </main>
    </div>
  );
}

export default App;
