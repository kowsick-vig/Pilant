// React port of renderer.py's _component_html / render_fragment — turns a
// render_view JSON object (validated server-side against schema.py's
// UI_SCHEMA, 17 component types) into the same visual language the old
// server-rendered HTML used (same CSS classes, see index.css's ported
// "app-window" block), just built as JSX instead of string concatenation.
//
// Jira row-linking: the original renderer rewrote a matching list/timeline/
// task_queue row's `url` field server-side (jira_detail_base + issue key)
// so a click opened studio.py's /studio?jira_detail=<key> panel. Here that
// becomes a client-side callback instead (`onOpenJira`) — see StudioPage,
// which swaps the whole preview pane for <JiraDetail> on a match rather
// than navigating away. `jiraOnly` mirrors studio.py's own flag (true for
// a workflow whose connector IS "jira"); a titled list containing "jira"
// (case-insensitive) is linked either way, matching the "unified" merged-
// screen fallback the Python version used.

const ISSUE_KEY_RE = /^([A-Z][A-Z0-9]*-\d+)/;

function toneClass(tone) {
  return tone || "default";
}

function Badge({ badge }) {
  if (!badge) return null;
  return (
    <span className={`badge ${toneClass(badge.tone)}`} title={badge.hint || undefined}>
      {badge.text || ""}
    </span>
  );
}

function FieldsGrid({ fields, className }) {
  if (!fields || fields.length === 0) return null;
  return (
    <dl className={className}>
      {fields.map((f, i) => (
        <div key={i}>
          <dt title={f.hint || undefined}>{f.label}</dt>
          <dd>{f.value}</dd>
        </div>
      ))}
    </dl>
  );
}

function rowIsJiraLinked(title, jiraOnly) {
  return Boolean(jiraOnly || (title && title.toLowerCase().includes("jira")));
}

function RowName({ row, jiraLinked, onOpenJira }) {
  const name = row.name;
  if (row.url) {
    return (
      <a href={row.url} target="_blank" rel="noopener noreferrer">
        {name}
      </a>
    );
  }
  if (jiraLinked && onOpenJira) {
    const m = typeof name === "string" ? name.trim().match(ISSUE_KEY_RE) : null;
    if (m) {
      return <>{name}</>;
    }
  }
  return <>{name}</>;
}

function rowJiraKey(row, jiraLinked) {
  if (row.url || !jiraLinked) return null;
  const m = typeof row.name === "string" ? row.name.trim().match(ISSUE_KEY_RE) : null;
  return m ? m[1] : null;
}

function ListComponent({ c, jiraOnly, onOpenJira }) {
  const rows = c.rows || [];
  const title = c.title;
  const jiraLinked = rowIsJiraLinked(title, jiraOnly);
  const body = rows.length ? (
    rows.map((r, i) => {
      const key = rowJiraKey(r, jiraLinked);
      const clickable = Boolean(key && onOpenJira);
      return (
        <div
          key={i}
          className={`list-row${clickable ? " clickable" : ""}`}
          onClick={clickable ? () => onOpenJira(key) : undefined}
        >
          <div>
            <div className="list-name">
              <RowName row={r} jiraLinked={jiraLinked} onOpenJira={onOpenJira} />
            </div>
            {r.note ? <div className="list-note">{r.note}</div> : null}
          </div>
          {r.badge ? <Badge badge={r.badge} /> : r.action ? <div className="list-action">{r.action}</div> : null}
        </div>
      );
    })
  ) : (
    <div className="panel-sub" style={{ padding: "8px 0" }}>
      Nothing matched — try a broader request (e.g. a longer time range).
    </div>
  );
  return (
    <div className="panel">
      {title ? <p className="panel-sub" style={{ marginBottom: 10 }}>{title}</p> : null}
      {c.subtitle ? <p className="panel-sub" style={{ marginBottom: 10 }}>{c.subtitle}</p> : null}
      {body}
    </div>
  );
}

function TimelineComponent({ c, jiraOnly, onOpenJira }) {
  const rows = c.rows || [];
  const title = c.title;
  const jiraLinked = rowIsJiraLinked(title, jiraOnly);
  return (
    <div className="panel">
      {title ? <p className="panel-title">{title}</p> : null}
      {c.subtitle ? <p className="panel-sub" style={{ marginBottom: 10 }}>{c.subtitle}</p> : null}
      {rows.length ? (
        <ul className="timeline">
          {rows.map((r, i) => {
            const key = rowJiraKey(r, jiraLinked);
            const clickable = Boolean(key && onOpenJira);
            return (
              <li
                key={i}
                className={`timeline-item${clickable ? " clickable" : ""}`}
                onClick={clickable ? () => onOpenJira(key) : undefined}
              >
                <span className="timeline-dot" />
                <div className="timeline-name">
                  <RowName row={r} jiraLinked={jiraLinked} onOpenJira={onOpenJira} />
                </div>
                {r.note ? <div className="timeline-note">{r.note}</div> : null}
                {r.badge ? <Badge badge={r.badge} /> : null}
              </li>
            );
          })}
        </ul>
      ) : (
        <div className="panel-sub" style={{ padding: "8px 0" }}>Nothing to show yet.</div>
      )}
    </div>
  );
}

function TaskQueueComponent({ c, jiraOnly, onOpenJira }) {
  const rows = c.rows || [];
  const title = c.title;
  const jiraLinked = rowIsJiraLinked(title, jiraOnly);
  return (
    <div className="panel">
      {title ? <p className="panel-title">{title}</p> : null}
      {c.subtitle ? <p className="panel-sub" style={{ marginBottom: 10 }}>{c.subtitle}</p> : null}
      {rows.length ? (
        <ul className="task-queue">
          {rows.map((r, i) => {
            const key = rowJiraKey(r, jiraLinked);
            const clickable = Boolean(key && onOpenJira);
            return (
              <li
                key={i}
                className={`task-row${clickable ? " clickable" : ""}`}
                onClick={clickable ? () => onOpenJira(key) : undefined}
              >
                <span className="task-check" />
                <div className="task-body">
                  <div className="task-name">
                    <RowName row={r} jiraLinked={jiraLinked} onOpenJira={onOpenJira} />
                  </div>
                  {r.note ? <div className="task-note">{r.note}</div> : null}
                </div>
                {r.badge ? <Badge badge={r.badge} /> : null}
              </li>
            );
          })}
        </ul>
      ) : (
        <div className="panel-sub" style={{ padding: "8px 0" }}>Nothing outstanding.</div>
      )}
    </div>
  );
}

function parseNumeric(value) {
  if (typeof value !== "string") return 0;
  const m = value.replace(/,/g, "").match(/-?\d+(?:\.\d+)?/);
  return m ? parseFloat(m[0]) : 0;
}

function Component({ c, jiraOnly, onOpenJira }) {
  switch (c.type) {
    case "stat_grid":
      return (
        <div className="stat-grid">
          {(c.stats || []).map((s, i) => (
            <div key={i} className={`stat-card tone-${toneClass(s.tone)}`}>
              <span className="stat-label">{s.label}</span>
              <span className="stat-value">{s.value}</span>
            </div>
          ))}
        </div>
      );

    case "panel":
      return (
        <div className="panel">
          <div className="panel-head">
            <div>
              <p className="panel-title">{c.title}</p>
              {c.subtitle ? <p className="panel-sub">{c.subtitle}</p> : null}
            </div>
            <Badge badge={c.badge} />
          </div>
          <FieldsGrid fields={c.fields} className="kv-grid" />
          {c.action ? <button className="app-btn">{c.action}</button> : null}
        </div>
      );

    case "list":
      return <ListComponent c={c} jiraOnly={jiraOnly} onOpenJira={onOpenJira} />;

    case "suggestions": {
      const items = (c.suggestions || []).filter((s) => typeof s === "string" && s.trim());
      if (!items.length) return null;
      return (
        <div className="suggestion-panel">
          <span className="suggestion-tag">AI suggestion</span>
          <p className="suggestion-title">{c.title || "Suggested actions"}</p>
          <ul className="suggestion-list">
            {items.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
          </ul>
        </div>
      );
    }

    case "timeline":
      return <TimelineComponent c={c} jiraOnly={jiraOnly} onOpenJira={onOpenJira} />;

    case "metric": {
      const stats = c.stats || [];
      if (!stats.length) return null;
      const s = stats[0];
      return (
        <div className={`panel metric-card tone-${toneClass(s.tone)}`}>
          <span className="metric-value">{s.value}</span>
          <span className="metric-label">{s.label}</span>
        </div>
      );
    }

    case "data_table": {
      const columns = (c.columns || []).filter((col) => typeof col === "string");
      const rows = c.table_rows || [];
      if (!columns.length || !rows.length) {
        return (
          <div className="panel">
            {c.title ? <p className="panel-title">{c.title}</p> : null}
            {c.subtitle ? <p className="panel-sub" style={{ marginBottom: 10 }}>{c.subtitle}</p> : null}
            <div className="panel-sub" style={{ padding: "8px 0" }}>Nothing to show yet.</div>
          </div>
        );
      }
      const hasBadge = rows.some((r) => r.badge);
      return (
        <div className="panel">
          {c.title ? <p className="panel-title">{c.title}</p> : null}
          {c.subtitle ? <p className="panel-sub" style={{ marginBottom: 10 }}>{c.subtitle}</p> : null}
          <div className="data-table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  {columns.map((col, i) => (
                    <th key={i}>{col}</th>
                  ))}
                  {hasBadge ? <th /> : null}
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={i}>
                    {(r.values || []).map((v, j) => (
                      <td key={j}>{v}</td>
                    ))}
                    {r.badge ? (
                      <td>
                        <Badge badge={r.badge} />
                      </td>
                    ) : hasBadge ? (
                      <td />
                    ) : null}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      );
    }

    case "chart": {
      const stats = c.stats || [];
      if (!stats.length) {
        return (
          <div className="panel">
            {c.title ? <p className="panel-title">{c.title}</p> : null}
            {c.subtitle ? <p className="panel-sub" style={{ marginBottom: 10 }}>{c.subtitle}</p> : null}
            <div className="panel-sub" style={{ padding: "8px 0" }}>Nothing to show yet.</div>
          </div>
        );
      }
      const magnitudes = stats.map((s) => parseNumeric(s.value));
      const maxVal = Math.max(...magnitudes, 0) > 0 ? Math.max(...magnitudes) : 1;
      return (
        <div className="panel">
          {c.title ? <p className="panel-title">{c.title}</p> : null}
          {c.subtitle ? <p className="panel-sub" style={{ marginBottom: 10 }}>{c.subtitle}</p> : null}
          <div className="chart">
            {stats.map((s, i) => (
              <div key={i} className="chart-row">
                <span className="chart-label">{s.label}</span>
                <div className="chart-track">
                  <div
                    className={`chart-bar tone-${toneClass(s.tone)}`}
                    style={{ width: `${Math.max((parseNumeric(s.value) / maxVal) * 100, 3).toFixed(1)}%` }}
                  />
                </div>
                <span className="chart-value">{s.value}</span>
              </div>
            ))}
          </div>
        </div>
      );
    }

    case "alert": {
      const tone = (c.badge && c.badge.tone) || "warning";
      return (
        <div className={`alert-banner tone-${toneClass(tone)}`}>
          <p className="alert-title">{c.title}</p>
          {c.subtitle ? <p className="alert-sub">{c.subtitle}</p> : null}
        </div>
      );
    }

    case "task_queue":
      return <TaskQueueComponent c={c} jiraOnly={jiraOnly} onOpenJira={onOpenJira} />;

    case "detail_view":
      return (
        <div className="detail-view">
          <div className="detail-head">
            <div>
              <p className="detail-title">{c.title}</p>
              {c.subtitle ? <p className="detail-sub">{c.subtitle}</p> : null}
            </div>
            <Badge badge={c.badge} />
          </div>
          <FieldsGrid fields={c.fields} className="detail-grid" />
          {c.action ? <button className="app-btn">{c.action}</button> : null}
        </div>
      );

    case "status_badge":
      return (
        <div className="status-badge-row">
          <span className="status-badge-label">{c.title}</span>
          <Badge badge={c.badge} />
        </div>
      );

    case "empty_state":
      return (
        <div className="empty-state">
          <p className="empty-state-title">{c.title}</p>
          {c.subtitle ? <p className="empty-state-sub">{c.subtitle}</p> : null}
        </div>
      );

    case "error_state":
      return (
        <div className="error-state">
          <div className="error-state-head">
            <div>
              <p className="error-state-title">{c.title}</p>
              {c.subtitle ? <p className="error-state-sub">{c.subtitle}</p> : null}
            </div>
            <Badge badge={c.badge || { text: "Error", tone: "critical" }} />
          </div>
          {c.action ? <button className="app-btn">{c.action}</button> : null}
        </div>
      );

    case "connection_state":
      return (
        <div className="connection-state">
          {(c.stats || []).map((s, i) => (
            <div key={i} className={`connection-pill tone-${toneClass(s.tone)}`}>
              <span className="connection-pill-label">{s.label}</span>
              <span className="connection-pill-value">{s.value}</span>
            </div>
          ))}
        </div>
      );

    case "pagination": {
      const { page, total_pages: totalPages, total_count: totalCount } = c;
      const parts = [];
      if (page != null && totalPages != null) parts.push(`Page ${page} of ${totalPages}`);
      if (totalCount != null) parts.push(`${totalCount} total`);
      if (!parts.length) return null;
      return <p className="pagination-status">{parts.join(" · ")}</p>;
    }

    case "popover":
      return (
        <details className="popover">
          <summary className="popover-trigger">{c.title}</summary>
          <div className="popover-body">
            <FieldsGrid fields={c.fields} className="popover-grid" />
          </div>
        </details>
      );

    default:
      return (
        <div className="panel">
          <p className="panel-sub">Unknown component type: {c.type}</p>
        </div>
      );
  }
}

export default function RenderView({ view, jiraOnly = false, onOpenJira }) {
  if (!view) return null;
  return (
    <div className="app-window">
      {view.heading ? <p className="app-h1">{view.heading}</p> : null}
      {view.meta ? <p className="app-meta">{view.meta}</p> : null}
      {(view.components || []).map((c, i) => (
        <Component key={i} c={c} jiraOnly={jiraOnly} onOpenJira={onOpenJira} />
      ))}
    </div>
  );
}
