import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../context/AuthContext";
import Sidebar from "../components/Sidebar";
import RenderView from "../components/RenderView";
import { connectorAccent } from "../constants";

// A new screen — "your work, with room to think": one persistent, connector-
// scoped workspace instead of a chat thread. It reuses the SAME backend a
// Studio workflow does (a workflow is found-or-created per connector, and
// the AI bar posts to the same /api/workflows/<id>/message pipeline
// _handle_chat_message already runs) — this is a different shell around
// real, existing data, not a second backend. Gmail's "Inbox" tab embeds the
// same real gmail_panel_frame iframe Studio uses, since that's real
// read+write data with folders already wired server-side (see
// resolve_panel_folder's FOLDERS keys); every other connector's real data
// comes back as render_view JSON, which the four other view tabs
// (Board/Feed/Table/Focus) each re-lay-out client-side from the same
// fetched rows — a real, working reshaping of real data, not five separate
// backend calls.

const TAGLINES = {
  gmail: { eyebrow: "Your work, with room to think", subtitle: "Read, respond, and make room for what matters." },
  slack: { eyebrow: "Every channel, one calm view", subtitle: "Catch up without opening a dozen threads." },
  github: { eyebrow: "Code, reviews, and what's blocking you", subtitle: "See what needs your eyes across every repo." },
  helpdesk: { eyebrow: "Every ticket, triaged for you", subtitle: "Know what's urgent before a customer has to ask twice." },
  jira: { eyebrow: "Your board, without the board", subtitle: "The issues that matter, shaped the way you think." },
  unified: { eyebrow: "Everything, in one place", subtitle: "Whatever's relevant across your connected apps." },
  custom: { eyebrow: "Describe it, and it's here", subtitle: "A workspace built around your own request." },
};
const DEFAULT_TAGLINE = { eyebrow: "Your work, with room to think", subtitle: "Ask for it, and it shows up here." };

const VIEW_TABS = [
  { key: "inbox", label: "Inbox", icon: "📥" },
  { key: "board", label: "Board", icon: "🗂️" },
  { key: "feed", label: "Feed", icon: "📋" },
  { key: "table", label: "Table", icon: "🗓️" },
  { key: "focus", label: "Focus", icon: "🎯" },
];

const GMAIL_FOLDER_TABS = [
  { key: "inbox", label: "Inbox" },
  { key: "starred", label: "Starred" },
  { key: "sent", label: "Sent" },
  { key: "all", label: "All Mail" },
];

// Flattens every list/timeline/task_queue component's rows into one array
// (each row tagged with which section it came from), and separately keeps
// the first real data_table component — the two shapes Board/Feed/Table/
// Focus below actually reshape.
function extractRowsAndTable(view) {
  const rows = [];
  let table = null;
  for (const c of (view && view.components) || []) {
    if (["list", "timeline", "task_queue"].includes(c.type)) {
      for (const r of c.rows || []) {
        if (r && typeof r === "object") rows.push({ ...r, section: c.title || null });
      }
    } else if (c.type === "data_table" && !table) {
      table = c;
    }
  }
  return { rows, table };
}

function matchesFilter(row, search, status) {
  if (status !== "all" && ((row.badge && row.badge.text) || "No status") !== status) return false;
  if (!search) return true;
  const haystack = `${row.name || ""} ${row.note || ""}`.toLowerCase();
  return haystack.includes(search.toLowerCase());
}

function FeedView({ rows }) {
  if (!rows.length) return <p className="panel-sub" style={{ padding: "8px 0" }}>Nothing matches.</p>;
  return (
    <div className="panel">
      {rows.map((r, i) => (
        <div className="list-row" key={i}>
          <div>
            <div className="list-name">{r.name}</div>
            {r.note ? <div className="list-note">{r.note}</div> : null}
          </div>
          {r.badge ? <span className={`badge ${r.badge.tone || "default"}`}>{r.badge.text}</span> : null}
        </div>
      ))}
    </div>
  );
}

function BoardView({ rows }) {
  if (!rows.length) return <p className="panel-sub" style={{ padding: "8px 0" }}>Nothing matches.</p>;
  const columns = new Map();
  for (const r of rows) {
    const key = (r.badge && r.badge.text) || "No status";
    if (!columns.has(key)) columns.set(key, []);
    columns.get(key).push(r);
  }
  return (
    <div className="ws-board">
      {[...columns.entries()].map(([status, items]) => (
        <div className="ws-board-col" key={status}>
          <div className="ws-board-col-head">
            <span>{status}</span>
            <span>{items.length}</span>
          </div>
          {items.map((r, i) => (
            <div className="ws-board-card" key={i}>
              <div className="ws-board-card-name">{r.name}</div>
              {r.note ? <div className="ws-board-card-note">{r.note}</div> : null}
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

function TableView({ rows, table }) {
  if (table) {
    return (
      <div className="panel">
        <div className="data-table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                {(table.columns || []).map((col, i) => (
                  <th key={i}>{col}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {(table.table_rows || []).map((r, i) => (
                <tr key={i}>
                  {(r.values || []).map((v, j) => (
                    <td key={j}>{v}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  }
  if (!rows.length) return <p className="panel-sub" style={{ padding: "8px 0" }}>Nothing matches.</p>;
  return (
    <div className="panel">
      <div className="data-table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Note</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>
                <td>{r.name}</td>
                <td>{r.note || "—"}</td>
                <td>{r.badge ? <span className={`badge ${r.badge.tone || "default"}`}>{r.badge.text}</span> : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function FocusView({ rows }) {
  if (!rows.length) return <p className="panel-sub" style={{ padding: "8px 0" }}>Nothing matches.</p>;
  const r = rows[0];
  return (
    <div className="detail-view ws-focus-card">
      <div className="detail-head">
        <div>
          <p className="detail-title">{r.name}</p>
          {r.section ? <p className="detail-sub">{r.section}</p> : null}
        </div>
        {r.badge ? <span className={`badge ${r.badge.tone || "default"}`}>{r.badge.text}</span> : null}
      </div>
      {r.note ? (
        <dl className="detail-grid">
          <div>
            <dt>Detail</dt>
            <dd>{r.note}</dd>
          </div>
        </dl>
      ) : null}
      {rows.length > 1 ? <p className="panel-sub" style={{ marginTop: 14 }}>+{rows.length - 1} more — switch to Feed to see the rest.</p> : null}
    </div>
  );
}

export default function WorkspacePage() {
  const { user, logout } = useAuth();
  const { connector } = useParams();
  const navigate = useNavigate();

  const [connectors, setConnectors] = useState([]);
  const [integrations, setIntegrations] = useState(null);
  const [workflows, setWorkflows] = useState([]);
  const [wf, setWf] = useState(null);
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [text, setText] = useState("");
  const [toolsMenuOpen, setToolsMenuOpen] = useState(false);
  const [viewMode, setViewMode] = useState("inbox");
  const [gmailFolder, setGmailFolder] = useState("inbox");
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("all");
  const [refreshing, setRefreshing] = useState(false);
  const [savedFlash, setSavedFlash] = useState(false);

  // Resolve a default connector (from /api/connectors' own "default") if
  // none is in the URL yet.
  useEffect(() => {
    if (connector) return;
    api.connectors().then((data) => navigate(`/workspace/${data.default || "unified"}`, { replace: true }));
  }, [connector, navigate]);

  useEffect(() => {
    if (!connector) return;
    let cancelled = false;
    setLoading(true);
    setViewMode("inbox");
    setSearch("");
    setStatus("all");
    (async () => {
      const [connData, integData, wfList] = await Promise.all([api.connectors(), api.integrations(), api.workflows()]);
      if (cancelled) return;
      setConnectors(connData.connectors);
      setIntegrations(integData);
      setWorkflows(wfList.workflows);

      const existing = wfList.workflows.find((w) => w.connector === connector);
      let workflow;
      if (existing) {
        workflow = (await api.openWorkflow(existing.id)).workflow;
      } else {
        workflow = (await api.createWorkflow(connector)).workflow;
      }
      if (!cancelled) {
        setWf(workflow);
        setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [connector]);

  const connectorMeta = connectors.find((c) => c.key === connector);
  const integMeta = integrations && integrations.items.find((it) => it.key === connector);
  const tagline = TAGLINES[connector] || DEFAULT_TAGLINE;
  const needsConnection = Boolean(integMeta && integMeta.has_oauth && !integMeta.connected_as);

  const { rows: allRows, table } = useMemo(() => extractRowsAndTable(wf && wf.last_render), [wf]);
  const statusOptions = useMemo(() => {
    const set = new Set(allRows.map((r) => (r.badge && r.badge.text) || "No status"));
    return ["all", ...set];
  }, [allRows]);
  const filteredRows = useMemo(() => allRows.filter((r) => matchesFilter(r, search, status)), [allRows, search, status]);

  const send = async (e) => {
    e.preventDefault();
    const trimmed = text.trim();
    if (!trimmed || sending || !wf) return;
    setSending(true);
    setText("");
    try {
      const data = await api.sendMessage(wf.id, trimmed);
      setWf(data.workflow);
    } finally {
      setSending(false);
    }
  };

  const refresh = async () => {
    if (!wf) return;
    setRefreshing(true);
    try {
      const data = await api.getWorkflow(wf.id);
      setWf(data.workflow);
    } finally {
      setTimeout(() => setRefreshing(false), 500);
    }
  };

  const saveView = async () => {
    if (!wf || !wf.last_request_text) return;
    await api.saveView(wf.title !== "Untitled workflow" ? wf.title : wf.last_request_text, wf.last_request_text);
    setSavedFlash(true);
    setTimeout(() => setSavedFlash(false), 1800);
  };

  const clarifyHint =
    wf && wf.pending_clarify && wf.messages.length
      ? wf.messages[wf.messages.length - 1].text
      : null;

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
        <Sidebar workflows={workflows} activeWorkflowId={null} onNewWorkflow={() => navigate("/studio")} />
        <div className="workspace-main">
          <div className="workspace-page">
            {loading || !wf ? (
              <p className="preview-hint">Loading…</p>
            ) : (
              <>
                <div className="ws-header">
                  <div>
                    <p className="ws-eyebrow">{tagline.eyebrow}</p>
                    <h1 className="ws-title">{connectorMeta ? connectorMeta.label : connector} workspace</h1>
                    <p className="ws-subtitle">{tagline.subtitle}</p>
                  </div>
                  <button type="button" className={`btn ws-save-btn${savedFlash ? " saved" : ""}`} onClick={saveView} disabled={!wf.last_request_text}>
                    🔖 {savedFlash ? "Saved" : "Save view"}
                  </button>
                </div>

                <form className="ws-ai-bar" onSubmit={send}>
                  <span className="ws-ai-icon">✨</span>
                  <input
                    type="text"
                    placeholder={`Make this workspace yours. Try "show ${connectorMeta ? connectorMeta.label : "it"} as a table"…`}
                    value={text}
                    onChange={(e) => setText(e.target.value)}
                    disabled={sending}
                  />
                  <button type="submit" className="ws-ai-submit" disabled={sending}>
                    →
                  </button>
                </form>
                {clarifyHint ? <p className="ws-ai-hint">{clarifyHint}</p> : null}

                <div className="ws-tools-row">
                  <span>Showing</span>
                  <span className="ws-tools-chip">
                    {connectorMeta ? connectorMeta.icon : "🔌"} {connectorMeta ? connectorMeta.label : connector}
                  </span>
                  <button type="button" className="ws-choose-tools" onClick={() => setToolsMenuOpen((v) => !v)}>
                    + Choose tools
                  </button>
                  {toolsMenuOpen ? (
                    <div className="ws-tools-menu">
                      {connectors
                        .filter((c) => !c.freeform)
                        .map((c) => (
                          <button
                            key={c.key}
                            type="button"
                            className={`ws-tools-menu-item${c.key === connector ? " active" : ""}`}
                            onClick={() => {
                              setToolsMenuOpen(false);
                              navigate(`/workspace/${c.key}`);
                            }}
                          >
                            {c.icon} {c.label}
                          </button>
                        ))}
                    </div>
                  ) : null}
                </div>

                <div className="ws-tabs-row">
                  <div className="ws-view-tabs">
                    {VIEW_TABS.map((t) => (
                      <button
                        key={t.key}
                        type="button"
                        className={`ws-view-tab${viewMode === t.key ? " active" : ""}`}
                        onClick={() => setViewMode(t.key)}
                      >
                        {t.icon} {t.label}
                      </button>
                    ))}
                  </div>
                  <div className="ws-utility-row">
                    <span className="ws-search">
                      🔎 <input placeholder="Search…" value={search} onChange={(e) => setSearch(e.target.value)} />
                    </span>
                    <select className="ws-status-select" value={status} onChange={(e) => setStatus(e.target.value)}>
                      {statusOptions.map((s) => (
                        <option key={s} value={s}>
                          {s === "all" ? "All statuses" : s}
                        </option>
                      ))}
                    </select>
                    <button type="button" className={`btn ws-refresh-btn${refreshing ? " spinning" : ""}`} onClick={refresh} title="Refresh">
                      ⟳
                    </button>
                  </div>
                </div>

                {connector === "gmail" && viewMode === "inbox" && !needsConnection ? (
                  <div className="ws-folder-tabs">
                    {GMAIL_FOLDER_TABS.map((f) => (
                      <button
                        key={f.key}
                        type="button"
                        className={`ws-folder-tab${gmailFolder === f.key ? " active" : ""}`}
                        onClick={() => setGmailFolder(f.key)}
                      >
                        {f.label}
                      </button>
                    ))}
                  </div>
                ) : null}

                {needsConnection ? (
                  <div className="ws-connect-row">
                    <span className="ws-connect-icon">{connectorMeta ? connectorMeta.icon : "🔌"}</span>
                    <div className="ws-connect-text">
                      <strong>{connectorMeta ? connectorMeta.label : connector}</strong>
                      <span>Connect this source in Connections to see your data.</span>
                    </div>
                    <button type="button" className="btn btn-secondary" onClick={() => navigate("/integrations")}>
                      Connections →
                    </button>
                  </div>
                ) : null}

                {needsConnection ? (
                  <div className="ws-empty">
                    <div className="ws-empty-icon">📥</div>
                    <p className="ws-empty-title">Let's bring your work in.</p>
                    <p className="ws-empty-sub">Connect your account to open this workspace with your data.</p>
                  </div>
                ) : connector === "gmail" && viewMode === "inbox" ? (
                  <iframe
                    className="gmail-panel-frame"
                    title="Gmail workspace inbox"
                    src={`/studio/gmail_panel_frame?workflow_id=${encodeURIComponent(wf.id)}&panel_folder=${encodeURIComponent(gmailFolder)}`}
                  />
                ) : !wf.last_render ? (
                  <div className="ws-empty">
                    <div className="ws-empty-icon">✨</div>
                    <p className="ws-empty-title">Nothing here yet.</p>
                    <p className="ws-empty-sub">Ask for it in the bar above — e.g. "{tagline.subtitle.split(".")[0]}".</p>
                  </div>
                ) : viewMode === "inbox" ? (
                  <RenderView view={wf.last_render} jiraOnly={connector === "jira"} />
                ) : viewMode === "feed" ? (
                  <FeedView rows={filteredRows} />
                ) : viewMode === "board" ? (
                  <BoardView rows={filteredRows} />
                ) : viewMode === "table" ? (
                  <TableView rows={filteredRows} table={table} />
                ) : (
                  <FocusView rows={filteredRows} />
                )}
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
