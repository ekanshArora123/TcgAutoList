import { NavLink, Route, Routes } from "react-router-dom";
import CollectionGridPage from "./pages/CollectionGridPage";
import CollectionPage from "./pages/CollectionPage";
import AnalyticsPage from "./pages/AnalyticsPage";
import CardDetailPage from "./pages/CardDetailPage";
import GradedCollectionPage from "./pages/GradedCollectionPage";
import GradedDetailPage from "./pages/GradedDetailPage";
import "./App.css";

function App() {
  return (
    <div className="app">
      <nav className="navbar">
        <h1 className="nav-title">TCG Collection Dashboard</h1>
        <div className="nav-links">
          <NavLink to="/" end className={({ isActive }) => `nav-btn ${isActive ? "active" : ""}`}>
            Collection
          </NavLink>
          <NavLink to="/info" className={({ isActive }) => `nav-btn ${isActive ? "active" : ""}`}>
            Info
          </NavLink>
          <NavLink to="/graded" className={({ isActive }) => `nav-btn ${isActive ? "active" : ""}`}>
            Graded
          </NavLink>
          <NavLink to="/analytics" className={({ isActive }) => `nav-btn ${isActive ? "active" : ""}`}>
            Analytics
          </NavLink>
        </div>
      </nav>
      {/* Card-show disclaimer. Sticky directly under the navbar (which is itself
          sticky at height 56px) so it stays on screen at any scroll position on
          every page — a customer deep in the grid still sees it. */}
      <div className="price-disclaimer" role="note">
        <span className="price-disclaimer-icon" aria-hidden="true">&#9888;</span>
        Prices shown are automated estimates and may be inaccurate &mdash; higher or lower.
      </div>
      <main className="main-content">
        <Routes>
          <Route path="/" element={<CollectionGridPage />} />
          <Route path="/info" element={<CollectionPage />} />
          <Route path="/graded" element={<GradedCollectionPage />} />
          <Route path="/graded/slab/:certId" element={<GradedDetailPage />} />
          <Route path="/analytics" element={<AnalyticsPage />} />
          <Route path="/card/:cardId" element={<CardDetailPage />} />
        </Routes>
      </main>
    </div>
  );
}

export default App;
