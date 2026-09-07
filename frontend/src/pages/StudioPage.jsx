import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../context/AuthContext";
import Sidebar from "../components/Sidebar";
import ChatPane from "../components/ChatPane";
import WorkspacePanel from "../components/WorkspacePanel";
import JiraDetail from "../components/JiraDetail";

// React port of studio.py's render_studio / _page_shell / _preview_html.
// The right-hand pane is WorkspacePanel: a workspace-style display (view
// tabs, search/status/refresh, Gmail folder tabs above the real live-inbox
// iframe) of whatever the chat on the left has built — see that
// component's own header comment for why this replaced a separate
// /workspace page.
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
    return <WorkspacePanel wf={wf} onWfUpdate={setWf} onOpenJira={setJiraKey} />;
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
