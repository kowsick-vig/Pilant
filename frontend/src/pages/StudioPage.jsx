import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../context/AuthContext";
import Sidebar from "../components/Sidebar";
import ChatPane from "../components/ChatPane";
import RenderView from "../components/RenderView";
import JiraDetail from "../components/JiraDetail";

// React port of studio.py's render_studio / _page_shell / _preview_html.
// The Gmail live panel is embedded via <iframe src="/studio/gmail_panel_frame">
// rather than ported component-by-component — see studio.py's own comment
// on that route: gmail_site.py's real read+write inbox (star/delete/reply/
// compose, real message IDs) already works end to end and every one of its
// actions honors an arbitrary same-site next_url, so reusing it unchanged
// behind an iframe was far lower-risk than a second full rebuild of that
// subsystem in React.
export default function StudioPage() {
  const { user, logout } = useAuth();
  const { id } = useParams();
  const navigate = useNavigate();

  const [workflows, setWorkflows] = useState([]);
  const [wf, setWf] = useState(null);
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [jiraKey, setJiraKey] = useState(null);
  const [error, setError] = useState(null);

  const refreshList = useCallback(async () => {
    const data = await api.workflows();
    setWorkflows(data.workflows);
    return data;
  }, []);

  // No :id in the URL — resolve to the session's current workflow (or the
  // most recent one, or create a fresh one if there are none yet) and
  // redirect to /studio/<id>, mirroring the old GET /studio route's
  // "always operates on `_current_workflow()`" behavior.
  useEffect(() => {
    if (id) return;
    let cancelled = false;
    (async () => {
      const data = await refreshList();
      if (cancelled) return;
      const targetId = data.current_workflow_id || (data.workflows[0] && data.workflows[0].id);
      if (targetId) {
        navigate(`/studio/${targetId}`, { replace: true });
      } else {
        const created = await api.createWorkflow(null);
        if (!cancelled) navigate(`/studio/${created.workflow.id}`, { replace: true });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [id, navigate, refreshList]);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    setLoading(true);
    setJiraKey(null);
    setError(null);
    (async () => {
      try {
        const [opened] = await Promise.all([api.openWorkflow(id), refreshList()]);
        if (!cancelled) setWf(opened.workflow);
      } catch (e) {
        if (!cancelled) setError("That workflow doesn't exist anymore.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [id, refreshList]);

  const handleNewWorkflow = async () => {
    const created = await api.createWorkflow(null);
    await refreshList();
    navigate(`/studio/${created.workflow.id}`);
  };

  const handleSend = async (text) => {
    setSending(true);
    try {
      const data = await api.sendMessage(id, text);
      setWf(data.workflow);
      await refreshList();
    } finally {
      setSending(false);
    }
  };

  const renderPreview = () => {
    if (!wf) return null;
    if (jiraKey) {
      return <JiraDetail issueKey={jiraKey} onBack={() => setJiraKey(null)} />;
    }
    if (wf.connector === "gmail") {
      return (
        <>
          <p className="preview-label">Live preview</p>
          {wf.last_request_text ? <p className="preview-request">"{wf.last_request_text}"</p> : null}
          <iframe
            className="gmail-panel-frame"
            title="Gmail live preview"
            src={`/studio/gmail_panel_frame?workflow_id=${encodeURIComponent(wf.id)}`}
          />
        </>
      );
    }
    if (!wf.last_render) {
      return (
        <div className="preview-empty">
          <p>Live preview</p>
          <p className="preview-hint">Nothing generated yet — describe what you want in the chat on the left.</p>
        </div>
      );
    }
    return (
      <>
        <p className="preview-label">Live preview</p>
        {wf.last_request_text ? <p className="preview-request">"{wf.last_request_text}"</p> : null}
        <RenderView view={wf.last_render} jiraOnly={wf.connector === "jira"} onOpenJira={setJiraKey} />
      </>
    );
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
        <Sidebar workflows={workflows} activeWorkflowId={id} onNewWorkflow={handleNewWorkflow} />
        {wf ? (
          <>
            <ChatPane wf={wf} onSend={handleSend} sending={sending} />
            <div className="previewpane">{renderPreview()}</div>
          </>
        ) : (
          <div className="previewpane">
            <div className="preview-empty">
              <p>{error || (loading ? "Loading…" : "")}</p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
