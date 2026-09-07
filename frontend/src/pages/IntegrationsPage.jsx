import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../context/AuthContext";
import Sidebar from "../components/Sidebar";
import { connectorAccent } from "../constants";

// React port of studio.py's _integrations_page_html / _render_integ_card /
// _connector_card_data. Gmail's OAuth connect/reconnect stay real full-page
// navigations to /oauth/gmail/connect (Google's own sign-in page, then a
// real server redirect back to /integrations?notice=...&kind=...) — that
// can't be an in-SPA fetch, so this page reads the notice off the URL on
// mount, same as the old post-redirect-GET pattern just via a query string
// instead of a session flash.
function IntegCard({ item, onStartWorkflow, onDisconnectGmail, onSync }) {
  const accent = connectorAccent(item.key);
  const [busy, setBusy] = useState(false);

  const withBusy = (fn) => async () => {
    setBusy(true);
    try {
      await fn();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="integ-card">
      <div className="integ-card-top">
        <div className="integ-card-icon" style={{ background: accent.bg, color: accent.fg }}>
          {item.icon}
        </div>
        {item.installed ? <span className="integ-card-installed">Connected</span> : null}
      </div>
      <div>
        <p className="integ-card-name">{item.label}</p>
        <span className="integ-card-tag">{item.category}</span>
      </div>
      <p className="integ-card-desc">{item.description}</p>

      {item.key === "knowledge_base" ? (
        item.rag_configured ? (
          <p className="integ-card-status integ-status-connected">
            {item.rag_stats && item.rag_stats.total
              ? `${item.rag_stats.total} item(s) indexed.`
              : "Nothing indexed yet — sync to enable search across older history."}
          </p>
        ) : (
          <p className="integ-card-status">
            Not set up yet — add AWS_BEARER_TOKEN_BEDROCK to your .env file and restart Studio to enable syncing
            and search.
          </p>
        )
      ) : item.has_oauth ? (
        item.connected_as ? (
          <p className="integ-card-status integ-status-connected">Connected as {item.connected_as}</p>
        ) : (
          <p className="integ-card-status">
            Not connected yet — falls back to the shared inbox configured in .env, if any.
          </p>
        )
      ) : (
        <p className="integ-card-status">Connected via this Studio's server credentials.</p>
      )}

      <div className="integ-card-actions">
        {item.key === "knowledge_base" ? (
          item.rag_configured ? (
            <button type="button" className="btn" disabled={busy} onClick={withBusy(onSync)}>
              {busy ? "Syncing…" : "Sync now"}
            </button>
          ) : null
        ) : item.has_oauth ? (
          <>
            <a className="btn" href="/oauth/gmail/connect">
              {item.connected_as ? "Reconnect a different account" : `Connect ${item.label}`}
            </a>
            {item.connected_as ? (
              <button type="button" className="btn btn-secondary" disabled={busy} onClick={withBusy(onDisconnectGmail)}>
                {busy ? "Disconnecting…" : "Disconnect"}
              </button>
            ) : null}
            <a className="btn btn-secondary" href="/inbox?folder=inbox">
              Open full inbox
            </a>
            <button type="button" className="btn btn-secondary" onClick={() => onStartWorkflow(item.key)}>
              Start a workflow
            </button>
          </>
        ) : (
          <button type="button" className="btn btn-secondary" onClick={() => onStartWorkflow(item.key)}>
            Start a workflow
          </button>
        )}
      </div>
    </div>
  );
}

export default function IntegrationsPage() {
  const { user, logout } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();

  const [data, setData] = useState(null);
  const [workflows, setWorkflows] = useState([]);
  const [category, setCategory] = useState(null);
  const [installedOnly, setInstalledOnly] = useState(false);
  const [notice, setNotice] = useState(null);

  const load = async () => {
    const [integ, wfList] = await Promise.all([api.integrations(), api.workflows()]);
    setData(integ);
    setWorkflows(wfList.workflows);
  };

  useEffect(() => {
    load();
  }, []);

  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const n = params.get("notice");
    const kind = params.get("kind");
    if (n) {
      setNotice({ text: n, kind: kind || "ok" });
      navigate("/integrations", { replace: true });
    }
  }, [location.search, navigate]);

  const items = useMemo(() => {
    if (!data) return [];
    return data.items.filter((it) => {
      if (category && it.category !== category) return false;
      if (installedOnly && !it.installed) return false;
      return true;
    });
  }, [data, category, installedOnly]);

  const handleStartWorkflow = async (connectorKey) => {
    const created = await api.createWorkflow(connectorKey);
    navigate(`/studio/${created.workflow.id}`);
  };

  const handleDisconnectGmail = async () => {
    await api.disconnectGmail();
    await load();
  };

  const handleSync = async () => {
    await api.ragSync();
    await load();
  };

  return (
    <div className="studio-body">
      <div className="topbar">
        <div className="topbar-left">
          <a className="view-toggle-btn" href="/inbox?folder=inbox" title="Open the full inbox">
            📥
          </a>
          <p className="brand">Pilant Studio</p>
        </div>
        <span className="session-row">
          logged in as {user && user.name} ·{" "}
          <button
            type="button"
            onClick={async () => {
              await logout();
              navigate("/login");
            }}
          >
            log out
          </button>
        </span>
      </div>
      <div className="shell">
        <Sidebar workflows={workflows} activeWorkflowId={null} onNewWorkflow={() => handleStartWorkflow(null)} />
        <div className="integrations-main">
          <div className="integrations-page">
            <div className="integ-header">
              <h1 className="integ-page-title">🔌 Integrations</h1>
              <p className="integ-page-sub">Connect real apps, or search across everything once synced.</p>
            </div>
            {notice ? (
              <div className={`integ-notice integ-notice-${notice.kind === "error" ? "error" : "ok"}`}>
                {notice.text}
              </div>
            ) : null}
            {!data ? (
              <p className="integ-page-sub">Loading…</p>
            ) : (
              <div className="integ-layout">
                <div className="integ-categories">
                  <p className="integ-categories-title">Categories</p>
                  <div className="integ-cat-list">
                    <button
                      type="button"
                      className={`integ-cat-item integ-cat-all${category === null ? " active" : ""}`}
                      onClick={() => setCategory(null)}
                    >
                      <span className="n">All</span>
                      <span className="integ-cat-count">{data.items.length}</span>
                    </button>
                    {data.categories.map((c) => (
                      <button
                        key={c.key}
                        type="button"
                        className={`integ-cat-item${category === c.key ? " active" : ""}`}
                        onClick={() => setCategory(c.key)}
                      >
                        <span className="n">
                          {c.icon} {c.label}
                        </span>
                        <span className="integ-cat-count">{c.count}</span>
                      </button>
                    ))}
                  </div>
                </div>
                <div className="integ-main-col">
                  <div className="integ-main-head">
                    <div>
                      <h2>Available Integrations</h2>
                      <p>{items.length} shown</p>
                    </div>
                    <button
                      type="button"
                      className={`integ-toggle${installedOnly ? " on" : ""}`}
                      onClick={() => setInstalledOnly((v) => !v)}
                    >
                      <span className="integ-toggle-switch" />
                      Show only connected
                    </button>
                  </div>
                  <div className="integ-grid">
                    {items.length === 0 ? (
                      <div className="integ-empty">Nothing matches this filter.</div>
                    ) : (
                      items.map((it) => (
                        <IntegCard
                          key={it.key}
                          item={it}
                          onStartWorkflow={handleStartWorkflow}
                          onDisconnectGmail={handleDisconnectGmail}
                          onSync={handleSync}
                        />
                      ))
                    )}
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
