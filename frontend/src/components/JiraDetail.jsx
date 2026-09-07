import { useEffect, useState } from "react";
import { api } from "../api";

// React port of studio.py's _jira_detail_panel_html — the real read+write
// detail view opened when a Jira-flavored list/timeline/task_queue row is
// clicked (see RenderView's onOpenJira). Re-fetches the issue's own current
// fields via GET /api/jira/<key> rather than trusting the model's trimmed
// row summary, same as the original.

const FIELD_ORDER = [
  ["Project", "project"],
  ["Type", "type"],
  ["Status", "status"],
  ["Priority", "priority"],
  ["Assignee", "assignee"],
  ["Reporter", "reporter"],
  ["Sprint", "sprint"],
  ["Epic", "epic"],
  ["Story points", "story_points"],
  ["Labels", "labels"],
  ["Components", "components"],
  ["Fix version", "fix_version"],
  ["Due", "due"],
  ["Created", "created"],
  ["Updated", "updated"],
  ["Watchers", "watchers"],
  ["Comments", "comments"],
];

function displayValue(field, issue) {
  const v = issue[field];
  if (field === "assignee") return v || "Unassigned";
  if (field === "sprint") return v || "Backlog";
  if (field === "epic" || field === "fix_version" || field === "due") return v || "—";
  if (field === "story_points") return v != null ? v : "—";
  if (field === "labels" || field === "components") return (v || []).join(", ") || "—";
  return v;
}

export default function JiraDetail({ issueKey, onBack }) {
  const [issue, setIssue] = useState(null);
  const [error, setError] = useState(null);
  const [resolving, setResolving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setIssue(null);
    setError(null);
    api
      .jiraIssue(issueKey)
      .then((data) => !cancelled && setIssue(data.issue))
      .catch(() => !cancelled && setError("Couldn't load that issue."));
    return () => {
      cancelled = true;
    };
  }, [issueKey]);

  const resolve = async () => {
    setResolving(true);
    try {
      const data = await api.jiraResolve(issueKey);
      setIssue(data.issue);
    } finally {
      setResolving(false);
    }
  };

  return (
    <>
      <button className="back-link" onClick={onBack}>
        ← back to results
      </button>
      {error ? (
        <div className="panel">
          <p className="panel-sub">{error}</p>
        </div>
      ) : !issue ? (
        <div className="panel">
          <p className="panel-sub">Loading…</p>
        </div>
      ) : (
        <div className="app-window">
          <div className="panel">
            <div className="panel-head">
              <div>
                <p className="panel-title">
                  {issue.key} · {issue.summary}
                </p>
              </div>
              <span className={`badge ${issue.tone}`}>{issue.priority}</span>
            </div>
            <dl className="kv-grid">
              {FIELD_ORDER.map(([label, field]) => (
                <div key={field}>
                  <dt>{label}</dt>
                  <dd>{displayValue(field, issue)}</dd>
                </div>
              ))}
            </dl>
            {issue.resolved ? (
              <p className="panel-sub" style={{ marginTop: 14 }}>Already resolved.</p>
            ) : (
              <button className="app-btn" style={{ marginTop: 14 }} onClick={resolve} disabled={resolving}>
                {resolving ? "Marking resolved…" : "Mark resolved"}
              </button>
            )}
          </div>
        </div>
      )}
    </>
  );
}
