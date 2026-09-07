import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import RenderView from "./RenderView";

// The right-hand side of a Studio workflow, once the workflow has a
// connector attached. This used to be its own page (a standalone
// /workspace/<connector> screen with its own header, AI bar, and connector
// switcher) — that duplicated the chat that already lives in ChatPane, so
// it's now folded into StudioPage's existing preview pane instead: the
// SAME chat on the left (including its clarify follow-ups, which
// ChatPane already renders from wf.messages) drives what shows up here,
// and this panel is just the workspace-style *display* of that data —
// tabs (Inbox/Board/Feed/Table/Focus), a search/status/refresh row, and
// (for Gmail) folder tabs above the real live-inbox iframe.

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

export default function WorkspacePanel({ wf, onWfUpdate, onOpenJira }) {
  const navigate = useNavigate();
  const connector = wf.connector;

  const [connectorMeta, setConnectorMeta] = useState(null);
  const [needsConnection, setNeedsConnection] = useState(false);
  const [viewMode, setViewMode] = useState("inbox");
  const [gmailFolder, setGmailFolder] = useState("inbox");
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("all");
  const [refreshing, setRefreshing] = useState(false);
  const [savedFlash, setSavedFlash] = useState(false);

  // Connector metadata + connection status only depend on which connector
  // this workflow is wired to, not on the workflow's rendered content.
  useEffect(() => {
    if (!connector) return;
    let cancelled = false;
    Promise.all([api.connectors(), api.integrations()]).then(([connData, integData]) => {
      if (cancelled) return;
      setConnectorMeta(connData.connectors.find((c) => c.key === connector) || null);
      const integMeta = integData.items.find((it) => it.key === connector);
      setNeedsConnection(Boolean(integMeta && integMeta.has_oauth && !integMeta.connected_as));
    });
    return () => {
      cancelled = true;
    };
  }, [connector]);

  // Reset view state when switching to a different workflow entirely.
  useEffect(() => {
    setViewMode("inbox");
    setSearch("");
    setStatus("all");
  }, [wf.id]);

  const { rows: allRows, table } = useMemo(() => extractRowsAndTable(wf.last_render), [wf.last_render]);
  const statusOptions = useMemo(() => {
    const set = new Set(allRows.map((r) => (r.badge && r.badge.text) || "No status"));
    return ["all", ...set];
  }, [allRows]);
  const filteredRows = useMemo(() => allRows.filter((r) => matchesFilter(r, search, status)), [allRows, search, status]);

  const refresh = async () => {
    setRefreshing(true);
    try {
      const data = await api.getWorkflow(wf.id);
      onWfUpdate(data.workflow);
    } finally {
      setTimeout(() => setRefreshing(false), 500);
    }
  };

  const saveView = async () => {
    if (!wf.last_request_text) return;
    await api.saveView(wf.title !== "Untitled workflow" ? wf.title : wf.last_request_text, wf.last_request_text);
    setSavedFlash(true);
    setTimeout(() => setSavedFlash(false), 1800);
  };

  if (!connector) {
    // No connector chosen yet for this workflow — nothing to show a
    // workspace layout around until the chat on the left has one.
    return (
      <div className="preview-empty">
        <p>Live preview</p>
        <p className="preview-hint">Once you connect an app in the chat on the left, its workspace shows up here.</p>
      </div>
    );
  }

  return (
    <div className="workspace-panel">
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
          <button type="button" className={`btn ws-save-btn${savedFlash ? " saved" : ""}`} onClick={saveView} disabled={!wf.last_request_text}>
            🔖 {savedFlash ? "Saved" : "Save view"}
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
        <>
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
          <div className="ws-empty">
            <div className="ws-empty-icon">📥</div>
            <p className="ws-empty-title">Let's bring your work in.</p>
            <p className="ws-empty-sub">Connect your account to open this workspace with your data.</p>
          </div>
        </>
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
          <p className="ws-empty-sub">Describe what you want in the chat on the left, and it shows up here.</p>
        </div>
      ) : viewMode === "inbox" ? (
        <RenderView view={wf.last_render} jiraOnly={connector === "jira"} onOpenJira={onOpenJira} />
      ) : viewMode === "feed" ? (
        <FeedView rows={filteredRows} />
      ) : viewMode === "board" ? (
        <BoardView rows={filteredRows} />
      ) : viewMode === "table" ? (
        <TableView rows={filteredRows} table={table} />
      ) : (
        <FocusView rows={filteredRows} />
      )}
    </div>
  );
}
