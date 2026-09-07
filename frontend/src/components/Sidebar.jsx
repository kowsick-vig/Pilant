import { Link, useLocation, useNavigate } from "react-router-dom";
import { connectorAccent } from "../constants";

// React port of studio.py's _sidebar_html. /inbox, /composer, /customers stay
// legacy server-rendered pages (out of scope for the React migration — see
// STUDIO_ROUTE_MAP.md and this project's own scope decision) so those links
// are plain <a> tags that leave the SPA; only /studio and /integrations are
// React Router's own routes.
export default function Sidebar({ workflows, activeWorkflowId, onNewWorkflow }) {
  const location = useLocation();
  const navigate = useNavigate();
  const onIntegrations = location.pathname.startsWith("/integrations");

  return (
    <div className="sidebar">
      <button className="new-wf-btn" type="button" onClick={onNewWorkflow}>
        + New workflow
      </button>
      <p className="sidebar-label">Workflows</p>
      <div className="wf-list">
        {workflows.length === 0 ? (
          <p className="wf-empty">No workflows yet.</p>
        ) : (
          workflows.map((w) => {
            const accent = connectorAccent(w.connector);
            const active = w.id === activeWorkflowId;
            return (
              <a
                key={w.id}
                className={`wf-card${active ? " active" : ""}`}
                href={`/studio/${w.id}`}
                onClick={(e) => {
                  e.preventDefault();
                  navigate(`/studio/${w.id}`);
                }}
              >
                <span className="wf-avatar" style={{ background: accent.bg, color: accent.fg }}>
                  {w.connector_icon || "⋯"}
                </span>
                <span className="wf-card-text">
                  <span className="wf-card-title">{w.title}</span>
                  <span className="wf-card-sub">{w.connector_label || "choosing app…"}</span>
                </span>
              </a>
            );
          })
        )}
      </div>
      <div className="sidebar-nav">
        <a className="wf-item nav-integrations" href="/inbox?folder=inbox">
          📩 Full inbox
        </a>
        <a className="wf-item nav-integrations" href="/composer">
          🤝 Composer
        </a>
        <a className="wf-item nav-integrations" href="/customers">
          👤 Customers
        </a>
        <Link className={`wf-item nav-integrations${onIntegrations ? " active" : ""}`} to="/integrations">
          🔌 Integrations
        </Link>
      </div>
    </div>
  );
}
