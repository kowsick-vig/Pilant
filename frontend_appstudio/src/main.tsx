import DesignerChat, {type DesignTurn} from "./DesignerChat";
import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Archive,
  ArrowDown,
  ArrowRight,
  Check,
  ChevronDown,
  ChevronRight,
  Columns3,
  ExternalLink,
  Github,
  Inbox,
  LayoutList,
  LogOut,
  Mail,
  Menu,
  MessageSquare,
  MoreHorizontal,
  PanelsTopLeft,
  Plus,
  Search,
  Settings2,
  SlidersHorizontal,
  Sparkles,
  Star,
  Table2,
  Ticket,
  Trash2,
  X,
  RefreshCw,
  Plug,
  Send,
  Bookmark,
  CircleHelp,
  Paperclip,
  FileText,
  Activity,
} from "lucide-react";
import "./style.css";
import "./dedicated.css";

type Source = "gmail" | "github" | "slack" | "jira" | "helpdesk" | "splunk";
type Layout = "inbox" | "board" | "feed" | "table" | "focus";
type User = {
  id: string;
  name: string;
  role: string;
  preferences: { layout?: Layout; density?: string };
};
type Connection = {
  id: Source;
  label: string;
  kind: string;
  layout: Layout;
  description: string;
  configured: boolean;
  account: string;
  oauth_ready: boolean;
};
type Row = {
  id: string;
  source: Source;
  title: string;
  status: string;
  priority?: string;
  person?: string;
  date?: string;
  body?: string;
  url?: string;
  starred?: boolean;
  detail?: Record<string, unknown>;
  labels?: string[];
  attachments?: {
    filename: string;
    mime_type: string;
    size: number;
    attachment_id: string;
  }[];
};
const names: Record<Source, string> = {
  gmail: "Gmail",
  github: "GitHub",
  slack: "Slack",
  jira: "Jira",
  helpdesk: "Helpdesk",
  splunk: "Splunk",
};
const sourceIcons = {
  gmail: Mail,
  github: Github,
  slack: MessageSquare,
  jira: Columns3,
  helpdesk: Ticket,
  splunk: Activity,
};
const layouts: { id: Layout; name: string; icon: typeof Inbox }[] = [
  { id: "inbox", name: "Inbox", icon: Inbox },
  { id: "board", name: "Board", icon: Columns3 },
  { id: "feed", name: "Feed", icon: LayoutList },
  { id: "table", name: "Table", icon: Table2 },
  { id: "focus", name: "Focus", icon: PanelsTopLeft },
];
let csrf = "";
async function api(path: string, options: RequestInit = {}) {
  const response = await fetch("/api" + path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": csrf,
      ...options.headers,
    },
  });
  let data;
  try {
    data = await response.json();
  } catch {
    throw new Error("The server did not respond. Please try again.");
  }
  if (!response.ok)
    throw new Error(data.error || "Something went wrong. Please try again.");
  if (data.csrf) csrf = data.csrf;
  return data;
}
const json = (value: unknown) => JSON.stringify(value);
function SourceIcon({
  source,
  small = false,
}: {
  source: Source;
  small?: boolean;
}) {
  const Icon = sourceIcons[source];
  return (
    <span className={`source-icon ${source} ${small ? "small" : ""}`}>
      <Icon size={small ? 13 : 17} />
    </span>
  );
}
function safeUrl(url?: string) {
  if (!url) return undefined;
  try {
    const u = new URL(url);
    return u.protocol === "https:" ? u.href : undefined;
  } catch {
    return undefined;
  }
}
function dateLabel(value?: string) {
  if (!value) return "";
  const d = new Date(value);
  return Number.isNaN(d.getTime())
    ? value
    : d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
function formatBytes(bytes: number) {
  if (!bytes) return "";
  const units = ["B", "KB", "MB", "GB"];
  let n = bytes,
    i = 0;
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024;
    i++;
  }
  return `${i === 0 ? n : n.toFixed(1)} ${units[i]}`;
}
function gmailAttachmentUrl(messageId: string, attachmentId: string) {
  return `/api/data/gmail/${encodeURIComponent(messageId)}/attachments/${encodeURIComponent(attachmentId)}`;
}
function statusClass(value?: string) {
  return (value || "").toLowerCase().replaceAll(" ", "-").replaceAll("_", "-");
}
function Badge({ text }: { text: string }) {
  return (
    <span className={`badge ${statusClass(text)}`}>
      <span />
      {text.replaceAll("_", " ")}
    </span>
  );
}
function App() {
  const [user, setUser] = useState<User | null>(null),
    [booting, setBooting] = useState(true),
    [bootError, setBootError] = useState("");
  useEffect(() => {
    api("/session")
      .then((d) => setUser(d.user))
      .catch((e) => setBootError(e.message))
      .finally(() => setBooting(false));
  }, []);
  if (booting)
    return (
      <div className="loading-page">
        <div className="brand-mark">p</div>
        <p>Opening your workspace…</p>
      </div>
    );
  if (bootError)
    return (
      <div className="loading-page">
        <h2>Couldn’t open your workspace</h2>
        <p>{bootError}</p>
        <button onClick={() => location.reload()}>Try again</button>
      </div>
    );
  return user ? (
    <Workspace
      user={user}
      onLogout={async () => {
        await api("/auth/logout", { method: "POST" });
        setUser(null);
      }}
    />
  ) : (
    <Auth onLogin={setUser} />
  );
}
function Auth({ onLogin }: { onLogin: (u: User) => void }) {
  const [signup, setSignup] = useState(false),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError("");
    setBusy(true);
    const data = Object.fromEntries(new FormData(e.currentTarget));
    try {
      const result = await api("/auth/" + (signup ? "signup" : "login"), {
        method: "POST",
        body: json(data),
      });
      onLogin(result.user);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="auth-page">
      <section className="auth-story">
        <a className="brand" href="/">
          <span className="brand-mark">p</span>pilant
          <span className="brand-dot">●</span>
        </a>
        <div>
          <span className="eyebrow">
            A LITTLE LESS NOISE. A LOT MORE FOCUS.
          </span>
          <h1>
            Your tools.
            <br />
            Your flow.
            <br />
            <em>Your own apps.</em>
          </h1>
          <p>
            Bring your work together in an interface that fits the way you
            think.
          </p>
          <div className="auth-sources">
            {(Object.keys(names) as Source[]).map((s) => (
              <SourceIcon key={s} source={s} />
            ))}
          </div>
        </div>
        <small>Your existing software. An interface of your own.</small>
      </section>
      <section className="auth-form">
        <form onSubmit={submit}>
          <span className="eyebrow">WELCOME TO PILANT</span>
          <h2>
            {signup ? "Make your software your own." : "Good to have you back."}
          </h2>
          <p>
            {signup
              ? "Create your account to build dedicated apps from your existing software."
              : "Sign in to pick up where you left off."}
          </p>
          {signup && (
            <label>
              Your name
              <input name="name" autoComplete="name" required maxLength={80} />
            </label>
          )}
          <label>
            Username or email
            <input
              name="username"
              autoComplete="username"
              required
              minLength={3}
              maxLength={100}
            />
          </label>
          <label>
            Password
            <input
              name="password"
              type="password"
              autoComplete={signup ? "new-password" : "current-password"}
              required
              minLength={signup ? 10 : 1}
              maxLength={200}
            />
          </label>
          {signup && (
            <label>
              How do you work?
              <select name="role">
                <option>Product & engineering</option>
                <option>Customer support</option>
                <option>Operations</option>
                <option>Leadership</option>
              </select>
            </label>
          )}
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          <button className="primary wide" disabled={busy}>
            {busy ? "Please wait…" : signup ? "Create account" : "Sign in"}
            <ArrowRight size={17} />
          </button>
          <p className="auth-switch">
            {signup ? "Already have an account?" : "New here?"}{" "}
            <button
              type="button"
              onClick={() => {
                setSignup(!signup);
                setError("");
              }}
            >
              {signup ? "Sign in" : "Create your account"}
            </button>
          </p>
          <small className="muted">
            This workspace uses separate accounts from the original Studio.
          </small>
        </form>
      </section>
    </main>
  );
}
type AppScreen = {
  id: string;
  title: string;
  description: string;
  layout: Layout;
  query: string;
  status: string;
  folder: string;
  columns: string[];
};
type BuiltApp = {
  id: string;
  title: string;
  description: string;
  purpose: string;
  source: Source;
  pages: AppScreen[];
  accent: string;
  density: string;
  mode: "ai" | "starter";
};
type AppDraft = {
  turns?: DesignTurn[];
  suggestions?: string[];
  designMode?: string;
  source: Source | null;
  name: string;
  prompt: string;
  step: number;
};
function routeAppId() {
  return location.hash.startsWith("#/app/")
    ? location.hash.split("/")[2] || ""
    : "";
}
function Workspace({
  user,
  onLogout,
}: {
  user: User;
  onLogout: () => Promise<void>;
}) {
  const [apps, setApps] = useState<BuiltApp[]>([]),
    [connections, setConnections] = useState<Connection[]>([]),
    [loading, setLoading] = useState(true);
  const [appId, setAppId] = useState(routeAppId),
    [notice, setNotice] = useState(""),
    [building, setBuilding] = useState(false);
  const [draft, setDraft] = useState<AppDraft | null>(() => {
    try {
      const raw = sessionStorage.getItem("pilant-app-draft:" + user.id);
      return raw ? JSON.parse(raw) : null;
    } catch {
      return null;
    }
  });
  async function refresh() {
    const [a, c] = await Promise.all([api("/apps"), api("/connections")]);
    setApps(a.apps);
    setConnections(c.connections);
  }
  useEffect(() => {
    refresh()
      .catch((e) => setNotice(e.message))
      .finally(() => setLoading(false));
    const onHash = () => setAppId(routeAppId());
    window.addEventListener("hashchange", onHash);
    const p = new URLSearchParams(location.search);
    if (p.get("connection_error")) setNotice(p.get("connection_error")!);
    if (p.get("connected"))
      setNotice(
        "Your software is connected. Describe the app you want to build.",
      );
    if (p.toString())
      history.replaceState(null, "", location.pathname + location.hash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  useEffect(() => {
    if (draft)
      sessionStorage.setItem("pilant-app-draft:" + user.id, json(draft));
    else sessionStorage.removeItem("pilant-app-draft:" + user.id);
  }, [draft, user.id]);
  function openApp(id: string) {
    location.hash = "/app/" + id;
    setAppId(id);
  }
  function home() {
    location.hash = "";
    setAppId("");
  }
  async function build() {
    if (!draft?.source) return;
    setBuilding(true);
    setNotice("");
    try {
      const d = await api("/apps/build", {
        method: "POST",
        body: json({
          source: draft.source,
          prompt: draft.prompt,
          name: draft.name,
        }),
      });
      await refresh();
      setDraft(null);
      openApp(d.app.id);
    } catch (e) {
      setNotice((e as Error).message);
    } finally {
      setBuilding(false);
    }
  }
  const chosen = connections.find((c) => c.id === draft?.source);
  if (appId)
    return (
      <GeneratedApp
        key={appId}
        id={appId}
        user={user}
        onHome={home}
        connections={connections}
        refreshConnections={refresh}
      />
    );
  return (
    <main className="app-studio">
      <header className="studio-header">
        <a className="brand" href="/">
          <span className="brand-mark">p</span>pilant
          <span className="brand-dot">●</span>
        </a>
        <span className="studio-label">APP STUDIO</span>
        <div className="studio-account">
          <span className="avatar mini">{user.name[0].toUpperCase()}</span>
          <span>{user.name}</span>
          <button
            className="icon-button"
            aria-label="Sign out"
            onClick={() => {
              home();
              onLogout().catch((e) => setNotice(e.message));
            }}
          >
            <LogOut size={16} />
          </button>
        </div>
      </header>
      {notice && (
        <div className="notice" role="status">
          <span>{notice}</span>
          <button
            className="icon-button"
            aria-label="Dismiss message"
            onClick={() => setNotice("")}
          >
            <X size={15} />
          </button>
        </div>
      )}
      {draft ? (
        <section className="app-builder">
          <button
            className="text-button"
            onClick={() => {
              if (!building) setDraft(null);
            }}
            disabled={building}
          >
            ← Your apps
          </button>
          <div className="builder-progress">
            <span className={draft.step === 1 ? "current" : "complete"}>
              01 <b>Your software</b>
            </span>
            <i />
            <span className={draft.step === 2 ? "current" : ""}>
              02 <b>Your way of working</b>
            </span>
            <i />
            <span>
              03 <b>Your app</b>
            </span>
          </div>
          {draft.step === 1 ? (
            <>
              <div className="builder-heading">
                <span className="eyebrow">START WITH WHAT YOU ALREADY USE</span>
                <h1>Which software should your app work with?</h1>
                <p>
                  Connect its existing data. Pilant will build a separate
                  interface around the work you want to do.
                </p>
              </div>
              <div className="software-choices">
                {connections.map((c) => (
                  <button
                    key={c.id}
                    className={`software-choice ${draft.source === c.id ? "selected" : ""}`}
                    onClick={() => setDraft(draft.source === c.id ? draft : { source:c.id, name:"", prompt:"", step:1 })}
                  >
                    <SourceIcon source={c.id} />
                    <strong>{c.label}</strong>
                    <small>
                      {c.kind === "sample"
                        ? "Try with sample data"
                        : c.configured
                          ? "Your account is connected"
                          : "Connect your existing account"}
                    </small>
                    {draft.source === c.id && <Check size={17} />}
                  </button>
                ))}
              </div>
              {chosen && !chosen.configured && (
                <div className="builder-connection">
                  <Connections
                    connections={[chosen]}
                    onChange={refresh}
                    onNotice={setNotice}
                  />
                </div>
              )}
              {chosen?.kind === "sample" && (
                <p className="builder-note">
                  This demonstration uses sample {chosen.label} data. Live
                  Gmail, GitHub, and Slack apps use your connected account’s
                  existing records.
                </p>
              )}
              <button
                className="primary builder-next"
                disabled={!chosen?.configured}
                onClick={() => setDraft({ ...draft, step: 2 })}
              >
                Continue
                <ArrowRight size={17} />
              </button>
            </>
          ) : (
            <DesignerChat
              key={draft.source}
              source={draft.source!} label={chosen?.label || draft.source!}
              sample={chosen?.kind === "sample"}
              name={draft.name} brief={draft.prompt} turns={draft.turns || []}
              suggestions={draft.suggestions || []} mode={draft.designMode} building={building}
              request={(messages) => api("/apps/design", {method:"POST", body:json({source:draft.source,messages})})}
              onUpdate={(value) => setDraft({...draft,...value})}
              onName={(name) => setDraft({...draft,name})}
              onBuild={build}
              onBack={() => setDraft({...draft,step:1})}
            />
          )}
        </section>
      ) : (
        <section className="apps-home">
          <div className="home-intro">
            <span className="eyebrow">YOUR SOFTWARE, ON YOUR TERMS</span>
            <h1>
              Software that works
              <br />
              <em>the way you do.</em>
            </h1>
            <p>
              Connect the tools you already use. Turn their existing data into
              dedicated apps designed around your work.
            </p>
            <button
              className="primary"
              onClick={() =>
                setDraft({ source: null, name: "", prompt: "", step: 1 })
              }
            >
              <Plus size={17} />
              Build an app
            </button>
          </div>
          <div className="apps-section-title">
            <h2>Your apps</h2>
            <span>
              {apps.length} {apps.length === 1 ? "app" : "apps"}
            </span>
          </div>
          {loading ? (
            <p className="muted">Loading your apps…</p>
          ) : apps.length ? (
            <div className="built-apps">
              {apps.map((app) => (
                <article
                  className={`built-app-card accent-${app.accent}`}
                  key={app.id}
                >
                  <div className="app-card-top">
                    <span className="app-monogram">
                      {app.title[0].toUpperCase()}
                    </span>
                    <span className="backed-by">
                      <SourceIcon source={app.source} small />
                      {names[app.source]}
                    </span>
                  </div>
                  <h3>{app.title}</h3>
                  <p>{app.description}</p>
                  <div className="app-page-preview">
                    {app.pages.slice(0, 3).map((p) => (
                      <span key={p.id}>{p.title}</span>
                    ))}
                  </div>
                  <div className="app-card-bottom">
                    <button
                      className="text-button"
                      onClick={() => openApp(app.id)}
                    >
                      Open app
                      <ArrowRight size={15} />
                    </button>
                    <button
                      className="icon-button"
                      aria-label={`Delete ${app.title}`}
                      onClick={async () => {
                        try {
                          await api("/apps/" + app.id, { method: "DELETE" });
                          await refresh();
                        } catch (e) {
                          setNotice((e as Error).message);
                        }
                      }}
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                </article>
              ))}
            </div>
          ) : (
            <div className="no-apps">
              <div className="app-outline">
                <PanelsTopLeft size={27} />
                <Sparkles size={13} />
              </div>
              <div>
                <h3>Your first app starts with a job to do.</h3>
                <p>
                  An email follow-up desk. A delivery board. A focused support
                  queue.
                  <br />
                  Each one is its own interface, connected to the software
                  behind it.
                </p>
              </div>
            </div>
          )}
          <div className="how-it-works">
            <span>
              <b>01</b>Connect your software
            </span>
            <ArrowRight size={15} />
            <span>
              <b>02</b>Describe how you work
            </span>
            <ArrowRight size={15} />
            <span>
              <b>03</b>Use your generated app
            </span>
          </div>
        </section>
      )}
    </main>
  );
}
function GeneratedApp({
  id,
  user,
  onHome,
  connections,
  refreshConnections,
}: {
  id: string;
  user: User;
  onHome: () => void;
  connections: Connection[];
  refreshConnections: () => Promise<void>;
}) {
  const [copilotOpen, setCopilotOpen] = useState(
    () => window.innerWidth >= 1100,
  );
  const [copilotResult, setCopilotResult] = useState<CopilotResult | null>(
    null,
  );
  const [app, setApp] = useState<BuiltApp | null>(null),
    [pageId, setPageId] = useState(""),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const [rows, setRows] = useState<Row[]>([]),
    [selected, setSelected] = useState<Row | null>(null),
    [query, setQuery] = useState(""),
    [loading, setLoading] = useState(false),
    [refresh, setRefresh] = useState(0),
    [scope, setScope] = useState("");
  const [refineError, setRefineError] = useState("");
  const [refining, setRefining] = useState(false),
    [refineOpen, setRefineOpen] = useState(false),
    [connectionOpen, setConnectionOpen] = useState(false),
    [menuOpen, setMenuOpen] = useState(false);
  const active = app?.pages.find((p) => p.id === pageId) || app?.pages[0];
  const version = useRef(0);
  useEffect(() => {
    api("/apps/" + id)
      .then((d) => {
        setApp(d.app);
        setPageId(d.app.pages[0].id);
      })
      .catch((e) => setError(e.message));
  }, [id]);
  useEffect(() => {
    if (!app || !active) return;
    const n = ++version.current;
    setLoading(true);
    setRows([]);
    setSelected(null);
    setError("");
    api(`/apps/${id}/data/${active.id}?` + new URLSearchParams({ q: query }))
      .then((d) => {
        if (n === version.current) {
          setRows(d.records);
          setScope(d.scope);
        }
      })
      .catch((e) => {
        if (n === version.current) setError(e.message);
      })
      .finally(() => {
        if (n === version.current) setLoading(false);
      });
  }, [id, active?.id, app, query, refresh]);
  async function changeStatus(row: Row, value: string) {
    try {
      await api(`/data/${row.source}/${encodeURIComponent(row.id)}/actions`, {
        method: "POST",
        body: json({ action: "status", value }),
      });
      setNotice("Status saved. Ask the copilot again for updated results.");
      setCopilotResult(null);
      setRefresh((n) => n + 1);
    } catch (e) {
      setNotice((e as Error).message);
    }
  }
  if (!app)
    return (
      <div className="loading-page">
        <button className="text-button" onClick={onHome}>
          ← Your apps
        </button>
        <p role="status">{error || "Opening your app…"}</p>
      </div>
    );
  const visibleRows = copilotResult?.records || rows;
  const visibleLayout = copilotResult?.plan.layout || active!.layout;
  const visibleColumns = copilotResult?.plan.columns || active!.columns;
  const visibleLoading = !copilotResult && loading;
  const visibleError = copilotResult ? "" : error;
  const connection = connections.find((c) => c.id === app.source);
  const LayoutIcon =
    layouts.find((l) => l.id === active?.layout)?.icon || PanelsTopLeft;
  return (
    <main
      className={`generated-app accent-${app.accent} density-${app.density} ${copilotOpen ? "has-copilot" : ""}`}
    >
      <header className="generated-header">
        <button
          className="icon-button"
          onClick={onHome}
          aria-label="Back to your apps"
        >
          <ArrowRight size={17} style={{ transform: "rotate(180deg)" }} />
        </button>
        <span className="app-monogram">{app.title[0].toUpperCase()}</span>
        <div className="generated-brand">
          <strong>{app.title}</strong>
          <small>Built for {user.name.split(" ")[0]}</small>
        </div>
        <div className="generated-header-right">
          {(
            <button
              className={`secondary copilot-toggle ${copilotOpen ? "active" : ""}`}
              onClick={() => setCopilotOpen(!copilotOpen)}
              aria-expanded={copilotOpen}
              aria-label="Pilant Copilot"
            >
              <MessageSquare size={15} />
              <span>Pilant Copilot</span>
            </button>
          )}
          <button
            className="backed-by"
            onClick={() => setConnectionOpen(true)}
            aria-label="App data connection"
          >
            <SourceIcon source={app.source} small />
            <span>Powered by {names[app.source]}</span>
            {["jira", "helpdesk", "splunk"].includes(app.source) && <small>Sample</small>}
          </button>
          <button
            className="secondary"
            onClick={() => {
              setRefineError("");
              setRefineOpen(true);
            }}
          >
            <Sparkles size={14} />
            <span>Adapt this app</span>
          </button>
          <span className="avatar mini">{user.name[0].toUpperCase()}</span>
        </div>
      </header>
      {app.mode === "starter" && (
        <div className="starter-note">
          <Sparkles size={13} />
          <span>
            Starter interface · AI design is unavailable. Your data and actions
            still work.
          </span>
        </div>
      )}
      {notice && (
        <div className="notice" role="status">
          {notice}
          <button
            className="icon-button"
            aria-label="Dismiss message"
            onClick={() => setNotice("")}
          >
            <X size={15} />
          </button>
        </div>
      )}
      <div className="generated-body">
        <aside className={`app-navigation ${menuOpen ? "open" : ""}`}>
          <div className="nav-caption">{app.description}</div>
          <span className="nav-label">IN THIS APP</span>
          {app.pages.map((p) => {
            const Icon =
              layouts.find((l) => l.id === p.layout)?.icon || LayoutList;
            return (
              <button
                className={`app-nav-item ${active?.id === p.id ? "active" : ""}`}
                key={p.id}
                onClick={() => {
                  setCopilotResult(null);
                  setPageId(p.id);
                  setQuery("");
                  setMenuOpen(false);
                }}
              >
                <Icon size={17} />
                <span>{p.title}</span>
                {active?.id === p.id && <ChevronRight size={13} />}
              </button>
            );
          })}
          <div className="generated-nav-footer">
            <span className="connected-dot" />
            <span>
              Existing {names[app.source]} data
              <br />
              <small>
                {connection?.account ||
                  (["jira", "helpdesk", "splunk"].includes(app.source)
                    ? "Private sample changes"
                    : "Your connected account")}
              </small>
            </span>
          </div>
        </aside>
        <section className="generated-screen">
          <div className="screen-heading">
            <div>
              <span className="eyebrow">{app.title}</span>
              <h1>{copilotResult ? "Copilot results" : active?.title}</h1>
              <p>
                {copilotResult
                  ? "The records that match your request. Open a record to see its details."
                  : active?.description || app.description}
              </p>
            </div>
            <button
              className="icon-button app-mobile-menu"
              aria-label="App navigation"
              onClick={() => setMenuOpen(!menuOpen)}
            >
              <Menu size={20} />
            </button>
          </div>
          <div className="screen-toolbar">
            <span className="screen-record-count">
              <LayoutIcon size={15} />
              {visibleLoading
                ? "Loading your data…"
                : `${visibleRows.length} records`}
            </span>
            {copilotResult ? (
              <button
                className="secondary return-to-screen"
                onClick={() => {
                  setCopilotResult(null);
                  setSelected(null);
                }}
              >
                Back to {active?.title}
                <ArrowRight size={14} />
              </button>
            ) : (
              <form
                className="search-box"
                key={pageId}
                onSubmit={(e) => {
                  e.preventDefault();
                  setQuery(
                    String(new FormData(e.currentTarget).get("search") || ""),
                  );
                }}
              >
                <Search size={15} />
                <input
                  name="search"
                  aria-label="Search this screen"
                  placeholder="Search this screen…"
                  defaultValue={query}
                />
                <button className="icon-button" aria-label="Run search">
                  <ArrowRight size={14} />
                </button>
              </form>
            )}
            <button
              className="icon-button"
              aria-label="Refresh records"
              onClick={() => {
                setCopilotResult(null);
                setRefresh((n) => n + 1);
              }}
              disabled={visibleLoading}
            >
              <RefreshCw size={16} className={loading ? "spin" : ""} />
            </button>
          </div>
          {copilotResult && <ResultBreakdown result={copilotResult} />}
          {visibleError ? (
            <div className="source-error" role="alert">
              <SourceIcon source={app.source} />
              <span>
                <strong>We couldn’t load this screen.</strong>
                <small>{visibleError}</small>
              </span>
              <button
                className="secondary"
                onClick={() => setConnectionOpen(true)}
              >
                Check connection
              </button>
            </div>
          ) : visibleLoading ? (
            <div className="skeleton-area" aria-label="Loading records">
              {[1, 2, 3].map((n) => (
                <div className="skeleton" key={n} />
              ))}
            </div>
          ) : !visibleRows.length ? (
            <div className="empty">
              <Inbox size={34} />
              <h2>Nothing here right now.</h2>
              <p>
                No existing records match this screen’s filters
                {query ? " and your search" : ""}.
              </p>
              {query && (
                <button className="secondary" onClick={() => setQuery("")}>
                  Clear search
                </button>
              )}
            </div>
          ) : (
            <RecordView
              layout={visibleLayout}
              rows={visibleRows}
              selected={selected}
              onSelect={setSelected}
              onStatus={changeStatus}
              onAction={() => setRefresh((n) => n + 1)}
              onNotice={setNotice}
              columns={visibleColumns}
            />
          )}
          <footer className="results-footer">
            <span>
              {scope}
              {active?.status && ` · ${active.status}`}
              {query && ` · Search: ${query}`}
            </span>
            <span>Connected to {names[app.source]} · Made with Pilant</span>
          </footer>
        </section>
        {copilotOpen && (
          <AppCopilot
            appId={id}
            source={app.source}
            onClose={() => setCopilotOpen(false)}
            onClear={() => setCopilotResult(null)}
            onResult={(r) => {
              setCopilotResult(r);
              setSelected(null);
            }}
          />
        )}
      </div>
      {connectionOpen && (
        <Modal
          title={`${names[app.source]} connection`}
          onClose={() => setConnectionOpen(false)}
        >
          <div className="builder-connection">
            {connection ? (
              <Connections
                connections={[connection]}
                onChange={async () => {
                  await refreshConnections();
                  setRefresh((n) => n + 1);
                }}
                onNotice={setNotice}
              />
            ) : (
              <p className="muted">Loading your connection…</p>
            )}
          </div>
        </Modal>
      )}
      {refineOpen && (
        <Modal
          title="How should this app change?"
          onClose={() => {
            if (!refining) setRefineOpen(false);
          }}
        >
          <p className="muted">
            Describe the screens, focus, or layout you need. This app keeps
            using the same {names[app.source]} data.
          </p>
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              setRefining(true);
              const prompt = String(
                new FormData(e.currentTarget).get("prompt"),
              );
              try {
                const d = await api(`/apps/${id}/refine`, {
                  method: "POST",
                  body: json({ prompt }),
                });
                setApp(d.app);
                setPageId(d.app.pages[0].id);
                setQuery("");
                setRefineOpen(false);
                setNotice("Your app has been updated.");
              } catch (e) {
                setRefineError((e as Error).message);
              } finally {
                setRefining(false);
              }
            }}
          >
            <label>
              What would work better?
              <textarea
                name="prompt"
                minLength={10}
                maxLength={2000}
                rows={5}
                required
                placeholder="Add a focused screen for blocked work, and show owners and priorities in a compact table."
                disabled={refining}
              />
            </label>
            {refineError && (
              <p className="error" role="alert">
                {refineError}
              </p>
            )}
            <button className="primary wide" disabled={refining}>
              {refining ? "Adapting your app…" : "Update this app"}
              <Sparkles size={16} />
            </button>
          </form>
        </Modal>
      )}
    </main>
  );
}

type CopilotTurn = {
  question: string;
  summary: string;
  mode: string;
  at: string;
  plan: Record<string, unknown>;
};
type CopilotResult = {
  sample: boolean;
  scope: string;
  summary: string;
  total: number;
  records: Row[];
  groups: { label: string; count: number }[];
  story_points: number;
  mode: string;
  plan: {
    layout: Layout;
    columns: string[];
    group_by: string;
    filters: { field: string; operator: string; value: unknown }[];
  };
};
function AppCopilot({
  appId,
  source,
  onResult,
  onClose,
  onClear,
}: {
  appId: string;
  source: Source;
  onResult: (r: CopilotResult) => void;
  onClose: () => void;
  onClear: () => void;
}) {
  const [turns, setTurns] = useState<CopilotTurn[]>([]),
    [message, setMessage] = useState(""),
    [busy, setBusy] = useState(false),
    [ready, setReady] = useState(false),
    [error, setError] = useState("");
  const bottom = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let active = true;
    api(`/apps/${appId}/copilot`)
      .then((d) => {
        if (active) setTurns(d.turns);
      })
      .catch((e) => {
        if (active) setError(e.message);
      })
      .finally(() => {
        if (active) setReady(true);
      });
    return () => {
      active = false;
    };
  }, [appId]);
  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [turns, busy, error]);
  async function ask(text: string) {
    if (busy || !ready || text.trim().length < 3) return;
    setBusy(true);
    setError("");
    setMessage(text);
    try {
      const d = await api(`/apps/${appId}/copilot`, {
        method: "POST",
        body: json({ message: text }),
      });
      setTurns((old) => [...old, d.turn].slice(-12));
      setMessage("");
      onResult(d.result);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const examples = source === "jira" ? [
    "Show blocked issues assigned to Priya",
    "Which unfinished issues are overdue?",
    "Break down open work by assignee",
    "Show PIL-104 and why it is blocked",
  ] : ["Show all records", "Break down by status", "Break down by person", 'Find records containing "launch"'];
  return (
    <aside className="jira-copilot" aria-label="Pilant Copilot">
      <header className="copilot-header">
        <span className="copilot-mark">
          <Sparkles size={18} />
        </span>
        <div>
          <strong>Pilant Copilot</strong>
          <small>Your {names[source] || source}, on demand</small>
        </div>
        <button
          className="icon-button"
          aria-label="Clear copilot conversation"
          disabled={busy || !turns.length}
          onClick={async () => {
            try {
              await api(`/apps/${appId}/copilot`, { method: "DELETE" });
              setTurns([]);
              setError("");
              onClear();
            } catch (e) {
              setError((e as Error).message);
            }
          }}
        >
          <Trash2 size={14} />
        </button>
        <button
          className="icon-button"
          aria-label="Close copilot"
          onClick={onClose}
        >
          <X size={17} />
        </button>
      </header>
      <div className="copilot-source">
        <SourceIcon source={source} small />
        <span>{names[source] || source} data for this app</span>
        <small>{["jira", "helpdesk", "splunk"].includes(source) ? "Sample data" : "Connected data"}</small>
      </div>
      <div className="copilot-messages" aria-live="polite">
        {!turns.length && (
          <div className="copilot-welcome">
            <div className="copilot-sparkles">
              <Sparkles size={24} />
            </div>
            <h2>
              What do you need
              <br />
              from {names[source] || source}?
            </h2>
            <p>
              Ask about your records, people, statuses, or topics. I’ll find
              the records and put them in your app.
            </p>
            <div className="copilot-examples">
              {examples.map((text) => (
                <button
                  key={text}
                  disabled={busy || !ready}
                  onClick={() => ask(text)}
                >
                  {text}
                  <ArrowRight size={13} />
                </button>
              ))}
            </div>
            {source === "jira" && <small className="copilot-demo-note">
              Try the fictional PIL-101–PIL-112 launch issues. They include
              overdue, blocked, unassigned, and completed work.
            </small>}
          </div>
        )}
        {turns.map((turn, i) => (
          <div className="copilot-exchange" key={turn.at + i}>
            <div className="copilot-question">{turn.question}</div>
            <div className="copilot-answer">
              <Sparkles size={14} />
              <div>
                <p>{turn.summary}</p>
                <small>
                  {turn.mode === "ai" ? "AI search" : "Basic filters"} ·{" "}
                  {new Date(turn.at).toLocaleTimeString([], {
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                </small>
              </div>
            </div>
          </div>
        ))}
        {busy && (
          <div className="copilot-thinking">
            <RefreshCw size={14} className="spin" />
            Looking through your {names[source] || source} records…
          </div>
        )}
        {error && (
          <div className="error" role="alert">
            {error}
          </div>
        )}
        <div ref={bottom} />
      </div>
      <form
        className="copilot-input"
        onSubmit={(e) => {
          e.preventDefault();
          ask(message);
        }}
      >
        <label className="sr-only" htmlFor="copilot-question">
          Ask Pilant Copilot
        </label>
        <textarea
          id="copilot-question"
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          placeholder="Ask for the data you need…"
          rows={3}
          maxLength={1500}
          disabled={busy || !ready}
          onKeyDown={(e) => {
            if (
              e.key === "Enter" &&
              !e.shiftKey &&
              !e.nativeEvent.isComposing
            ) {
              e.preventDefault();
              ask(message);
            }
          }}
        />
        <div>
          <span>Enter to send · Shift + Enter for a new line</span>
          <button
            className="copilot-send"
            aria-label="Send copilot question"
            disabled={busy || !ready || message.trim().length < 3}
          >
            <ArrowRight size={17} />
          </button>
        </div>
      </form>
      <footer className="copilot-footnote">
        Looks up data. Record changes use the record’s controls.
      </footer>
    </aside>
  );
}
function ResultBreakdown({ result }: { result: CopilotResult }) {
  return (
    <section
      className="copilot-result-summary"
      aria-label="Copilot result summary"
    >
      <div>
        <span className="copilot-count">{result.total}</span>
        <span>
          <strong>{result.summary}</strong>
          <small>
            {result.mode === "ai"
              ? "AI-interpreted request"
              : "Basic filter matching"}{" "}
            · {result.sample ? "Sample data" : "Connected data"} · {result.scope}
          </small>
        </span>
      </div>
      {result.groups.length > 0 && (
        <div className="copilot-breakdown">
          {result.groups.map((g) => (
            <div key={g.label}>
              <span>{g.label}</span>
              <i>
                <b
                  style={{
                    width: result.total
                      ? `${(g.count / result.total) * 100}%`
                      : "0%",
                  }}
                />
              </i>
              <strong>{g.count}</strong>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function RecordView({
  layout,
  rows,
  selected,
  onSelect,
  onStatus,
  onAction,
  onNotice,
  columns: tableColumns = ["title", "status", "person", "date"],
}: {
  columns?: string[];
  layout: Layout;
  rows: Row[];
  selected: Row | null;
  onSelect: (r: Row | null) => void;
  onStatus: (r: Row, s: string) => Promise<void>;
  onAction: () => void;
  onNotice: (s: string) => void;
}) {
  const [dragged, setDragged] = useState<Row | null>(null);
  const states = [...new Set(rows.map((r) => r.status))];
  const base = rows.every((r) => r.source === "jira")
    ? ["To Do", "In Progress", "In Review", "Blocked", "Done"]
    : rows.every((r) => r.source === "helpdesk")
      ? ["open", "in_progress", "escalated", "resolved"]
      : rows.every((r) => r.source === "splunk")
        ? ["new", "investigating", "escalated", "resolved"]
        : [];
  const columns = [...new Set([...base, ...states])];
  const details = (
    <Detail
      row={selected}
      onClose={() => onSelect(null)}
      onStatus={onStatus}
      onAction={onAction}
      onNotice={onNotice}
    />
  );
  if (layout === "board")
    return (
      <>
        <div
          className={`board-scroll ${columns.length > 3 ? "many-columns" : ""}`}
        >
          <div className="board">
            {columns.map((s, i) => (
            <section
              className="board-column"
              key={s}
              onDragOver={(e) => {
                if (dragged && ["jira", "helpdesk", "splunk"].includes(dragged.source))
                  e.preventDefault();
              }}
              onDrop={(e) => {
                e.preventDefault();
                if (dragged) onStatus(dragged, s);
                setDragged(null);
              }}
            >
              <div className="column-heading">
                <span className={`column-dot color-${i % 5}`} />
                <h2>{s.replaceAll("_", " ")}</h2>
                <span className="column-count">
                  {rows.filter((r) => r.status === s).length}
                </span>
              </div>
              <div className="column-content">
                {rows
                  .filter((r) => r.status === s)
                  .map((row) => (
                    <button
                      className="issue-card"
                      key={row.source + row.id}
                      draggable={["jira", "helpdesk", "splunk"].includes(row.source)}
                      onDragStart={() => setDragged(row)}
                      onDragEnd={() => setDragged(null)}
                      onClick={() => onSelect(row)}
                    >
                      <div className="issue-meta">
                        <SourceIcon source={row.source} small />
                        <span>
                          {row.source === "github" ? "#" : ""}
                          {row.id}
                        </span>
                        <MoreHorizontal size={15} />
                      </div>
                      <h3>{row.title}</h3>
                      {row.priority && (
                        <span
                          className={`priority ${statusClass(row.priority)}`}
                        >
                          <span>⚑</span>
                          {row.priority}
                        </span>
                      )}
                      <div className="issue-footer">
                        <span>
                          <span className="tiny-avatar">
                            {row.person?.[0] || "?"}
                          </span>
                          {row.person?.split(" ")[0]}
                        </span>
                        <time>{dateLabel(row.date)}</time>
                      </div>
                    </button>
                  ))}
                {!rows.some((r) => r.status === s) && (
                  <div className="drop-empty">Drop a task here</div>
                )}
              </div>
            </section>
          ))}
          </div>
        </div>
        {selected && (
          <div className="detail-overlay">
            <div className="detail-backdrop" onClick={() => onSelect(null)} />
            {details}
          </div>
        )}
      </>
    );
  if (layout === "inbox")
    return (
      <div className={`inbox-layout ${selected ? "has-selection" : ""}`}>
        <div className="inbox-list">
          {rows.map((row) => (
            <button
              key={row.source + row.id}
              className={`message-row ${selected?.id === row.id && selected.source === row.source ? "selected" : ""} ${row.status === "unread" ? "unread" : ""}`}
              onClick={() => onSelect(row)}
            >
              <div className="message-top">
                <SourceIcon source={row.source} small />
                <strong>{row.person || names[row.source]}</strong>
                <time>{dateLabel(row.date)}</time>
              </div>
              <h3>{row.title}</h3>
              <p>
                {row.body ||
                  [row.id, row.priority, row.status]
                    .filter(Boolean)
                    .join(" · ")}
              </p>
              <Badge text={row.status} />
            </button>
          ))}
        </div>
        {details}
      </div>
    );
  if (layout === "table")
    return (
      <>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                {tableColumns.map((c) => (
                  <th key={c}>
                    {c === "title"
                      ? "Record"
                      : c === "person"
                        ? "Person"
                        : c === "date"
                          ? "Updated"
                          : c}
                  </th>
                ))}
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.source + r.id}>
                  {tableColumns.map((c) => (
                    <td key={c}>
                      {c === "title" ? (
                        <button
                          className="table-title"
                          onClick={() => onSelect(r)}
                        >
                          <small>{r.id}</small>
                          {r.title}
                        </button>
                      ) : c === "status" ? (
                        <Badge text={r.status} />
                      ) : c === "date" ? (
                        dateLabel(r.date)
                      ) : (
                        String(r[c as keyof Row] ?? r.detail?.[c] ?? "—")
                      )}
                    </td>
                  ))}
                  <td>
                    <button
                      className="icon-button"
                      aria-label={`Open ${r.title}`}
                      onClick={() => onSelect(r)}
                    >
                      <ChevronRight size={17} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {selected && (
          <div className="detail-overlay">
            <div className="detail-backdrop" onClick={() => onSelect(null)} />
            {details}
          </div>
        )}
      </>
    );
  if (layout === "focus")
    return (
      <div className="focus-layout">
        <div className="focus-index">
          <span className="eyebrow">ONE THING AT A TIME</span>
          {rows.map((r, i) => (
            <button
              key={r.source + r.id}
              className={
                selected?.id === r.id && selected.source === r.source
                  ? "active"
                  : ""
              }
              onClick={() => onSelect(r)}
            >
              <span>{String(i + 1).padStart(2, "0")}</span>
              {r.title}
              <ChevronRight size={14} />
            </button>
          ))}
        </div>
        {details}
      </div>
    );
  return (
    <div className="feed-layout">
      <div className="feed">
        <div className="feed-line" />
        {rows.map((r) => (
          <article className="feed-item" key={r.source + r.id}>
            <SourceIcon source={r.source} />
            <div className="feed-card">
              <div className="feed-meta">
                <strong>{r.person || names[r.source]}</strong>
                <span>{names[r.source]}</span>
                <time>{dateLabel(r.date)}</time>
              </div>
              <button className="feed-title" onClick={() => onSelect(r)}>
                {r.title}
              </button>
              {r.body && <p>{r.body.slice(0, 600)}</p>}
              <div className="feed-footer">
                <Badge text={r.status} />
                <button className="text-button" onClick={() => onSelect(r)}>
                  Open details
                  <ArrowRight size={13} />
                </button>
              </div>
            </div>
          </article>
        ))}
      </div>
      {selected && (
        <div className="detail-overlay">
          <div className="detail-backdrop" onClick={() => onSelect(null)} />
          {details}
        </div>
      )}
    </div>
  );
}
function Detail({
  row,
  onClose,
  onStatus,
  onAction,
  onNotice,
}: {
  row: Row | null;
  onClose: () => void;
  onStatus: (r: Row, s: string) => Promise<void>;
  onAction: () => void;
  onNotice: (s: string) => void;
}) {
  const [full, setFull] = useState<Row | null>(null),
    [loading, setLoading] = useState(false),
    [error, setError] = useState(""),
    [reply, setReply] = useState(""),
    [busy, setBusy] = useState(false),
    [confirm, setConfirm] = useState(false);
  useEffect(() => {
    let active = true;
    setFull(row);
    setReply("");
    setConfirm(false);
    setError("");
    setLoading(false);
    if (row?.source === "gmail") {
      setLoading(true);
      api(`/data/gmail/${encodeURIComponent(row.id)}`)
        .then((d) => {
          if (active) setFull(d.record);
        })
        .catch((e) => {
          if (active) setError(e.message);
        })
        .finally(() => {
          if (active) setLoading(false);
        });
    }
    return () => {
      active = false;
    };
  }, [row?.source, row?.id]);
  useEffect(() => {
    if (row && full && row.source === full.source && row.id === full.id)
      setFull((f) => (f ? { ...f, status: row.status } : f));
  }, [row?.status]);
  if (!row)
    return (
      <section className="detail-panel empty-detail">
        <div className="detail-illustration">
          <LayoutList size={31} />
          <span>
            <Check size={13} />
          </span>
        </div>
        <h2>A little space to focus.</h2>
        <p>
          Select a record to read the details
          <br />
          and take the next step.
        </p>
      </section>
    );
  const r = full || row;
  async function action(name: string, value = "") {
    setBusy(true);
    setError("");
    try {
      await api(`/data/${r.source}/${encodeURIComponent(r.id)}/actions`, {
        method: "POST",
        body: json({ action: name, value }),
      });
      setReply("");
      setConfirm(false);
      onNotice(name === "reply" ? "Reply sent." : "Email updated.");
      onAction();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const statusOptions =
    r.source === "jira"
      ? ["To Do", "In Progress", "In Review", "Blocked", "Done"]
      : r.source === "splunk"
        ? ["new", "investigating", "escalated", "resolved"]
        : ["open", "in_progress", "escalated", "resolved"];
  return (
    <section className="detail-panel" aria-label="Record details">
      <div className="detail-toolbar">
        <span className="inline-source">
          <SourceIcon source={r.source} small />
          {names[r.source]}
          <span className="muted"> / {r.id}</span>
        </span>
        <button
          className="icon-button"
          aria-label="Close details"
          onClick={onClose}
        >
          <X size={18} />
        </button>
      </div>
      <div className="detail-body">
        <Badge text={r.status} />
        <h2>{r.title}</h2>
        <div className="detail-byline">
          <span className="avatar">{r.person?.[0]?.toUpperCase() || "?"}</span>
          <span>
            <strong>{r.person || names[r.source]}</strong>
            <small>{dateLabel(r.date)}</small>
          </span>
        </div>
        {loading && <p className="muted">Loading message…</p>}
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        {r.body && <div className="message-body">{r.body}</div>}
        {r.source === "gmail" && !!r.attachments?.length && (
          <div className="attachments">
            {r.attachments.map((a) => {
              const url = gmailAttachmentUrl(r.id, a.attachment_id);
              const isImage = a.mime_type.startsWith("image/");
              return isImage ? (
                <a
                  key={a.attachment_id}
                  className="attachment-card attachment-image"
                  href={url}
                  target="_blank"
                  rel="noreferrer"
                >
                  <img src={url} alt={a.filename} loading="lazy" />
                  <span>{a.filename}</span>
                </a>
              ) : (
                <a
                  key={a.attachment_id}
                  className="attachment-card attachment-file"
                  href={url}
                  target="_blank"
                  rel="noreferrer"
                >
                  {a.mime_type === "application/pdf" ? (
                    <FileText size={16} />
                  ) : (
                    <Paperclip size={16} />
                  )}
                  <span>{a.filename}</span>
                  {a.size ? <small>{formatBytes(a.size)}</small> : null}
                </a>
              );
            })}
          </div>
        )}
        {r.detail && r.source !== "gmail" && (
          <dl className="record-fields">
            {Object.entries(r.detail)
              .filter(
                ([k, v]) =>
                  !["summary", "subject", "status"].includes(k) &&
                  v !== null &&
                  v !== "",
              )
              .map(([k, v]) => (
                <div key={k}>
                  <dt>{k.replaceAll("_", " ")}</dt>
                  <dd>{Array.isArray(v) ? v.join(", ") : String(v)}</dd>
                </div>
              ))}
          </dl>
        )}
        {r.labels?.length ? (
          <div className="labels">
            {r.labels.map((l) => (
              <span key={l}>{l}</span>
            ))}
          </div>
        ) : null}
        {safeUrl(r.url) && (
          <a
            className="secondary external"
            href={safeUrl(r.url)}
            target="_blank"
            rel="noreferrer"
          >
            Open in {names[r.source]}
            <ExternalLink size={14} />
          </a>
        )}
        {["jira", "helpdesk", "splunk"].includes(r.source) && (
          <div className="detail-actions">
            <label>
              Move to
              <select
                aria-label="Change record status"
                value={row.status}
                onChange={async (e) => {
                  setBusy(true);
                  await onStatus(row, e.target.value);
                  setBusy(false);
                }}
                disabled={busy}
              >
                {statusOptions.map((s) => (
                  <option key={s}>{s}</option>
                ))}
              </select>
            </label>
            <small className="muted">
              Changes apply to your sample workspace only.
            </small>
          </div>
        )}
        {r.source === "gmail" && !loading && !error && (
          <>
            <div className="mail-actions">
              <button
                className="secondary"
                disabled={busy}
                onClick={() => action(r.starred ? "unstar" : "star")}
              >
                <Star size={15} />
                {r.starred ? "Unstar" : "Star"}
              </button>
              <button
                className="secondary"
                disabled={busy}
                onClick={() => action("read")}
              >
                <Check size={15} />
                Mark read
              </button>
              <button
                className="secondary"
                disabled={busy}
                onClick={() => action("archive")}
              >
                <Archive size={15} />
                Archive
              </button>
            </div>
            <form
              className="reply-box"
              onSubmit={(e) => {
                e.preventDefault();
                setConfirm(true);
              }}
            >
              <label>
                Reply to {r.person}
                <textarea
                  aria-label="Reply message"
                  rows={5}
                  value={reply}
                  onChange={(e) => setReply(e.target.value)}
                  placeholder="Write your reply…"
                  required
                  maxLength={20000}
                />
              </label>
              <button className="primary" disabled={busy || !reply.trim()}>
                <Send size={14} />
                Review & send
              </button>
            </form>
          </>
        )}
      </div>
      {confirm && (
        <Modal title="Send this reply?" onClose={() => setConfirm(false)}>
          <p>To: {r.person}</p>
          <div className="reply-preview">{reply}</div>
          <button
            className="primary wide"
            disabled={busy}
            onClick={() => action("reply", reply)}
          >
            {busy ? "Sending…" : "Send reply"}
            <Send size={15} />
          </button>
        </Modal>
      )}
    </section>
  );
}
function Connections({
  connections,
  onChange,
  onNotice,
}: {
  connections: Connection[];
  onChange: () => Promise<void>;
  onNotice: (s: string) => void;
}) {
  const [editing, setEditing] = useState<Connection | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  async function connect(c: Connection) {
    setError("");
    if (c.id === "gmail") {
      try {
        const d = await api("/oauth/gmail", { method: "POST" });
        location.assign(d.url);
      } catch (e) {
        onNotice((e as Error).message);
      }
    } else setEditing(c);
  }
  return (
    <section className="connections-page">
      <span className="eyebrow">BETTER TOGETHER</span>
      <h1>Bring your tools along.</h1>
      <p>
        Your connections belong to your account. Choose what comes into your
        workspace.
      </p>
      <div className="connections-list">
        {connections.map((c) => (
          <article className="connection-card" key={c.id}>
            <SourceIcon source={c.id} />
            <div className="connection-info">
              <h2>
                {c.label}
                {c.kind === "sample" && (
                  <span className="sample-label">Sample data</span>
                )}
              </h2>
              <p>{c.description}</p>
              {c.account && <small>{c.account}</small>}
            </div>
            <div className="connection-actions">
              {c.kind === "sample" ? (
                <span className="available">
                  <Check size={14} />
                  Ready to explore
                </span>
              ) : (
                <>
                  {c.configured && (
                    <span className="available">
                      <span className="connected-dot" />
                      Configured
                    </span>
                  )}
                  <button className="secondary" onClick={() => connect(c)}>
                    {c.configured ? "Reconnect" : "Connect"}
                    <ArrowRight size={14} />
                  </button>
                  {c.configured && (
                    <button
                      className="icon-button"
                      aria-label={`Disconnect ${c.label}`}
                      onClick={async () => {
                        try {
                          await api("/connections/" + c.id, {
                            method: "DELETE",
                          });
                          await onChange();
                          onNotice(
                            `${c.label} disconnected from your account.`,
                          );
                        } catch (e) {
                          onNotice((e as Error).message);
                        }
                      }}
                    >
                      <X size={16} />
                    </button>
                  )}
                </>
              )}
            </div>
          </article>
        ))}
      </div>
      <div className="connection-footnote">
        <CircleHelp size={18} />
        <p>
          GitHub and Slack use the permissions of the token you connect. Gmail
          asks you to authorize your own Google account. Sample data is separate
          for every user.
        </p>
      </div>
      {editing && (
        <Modal
          title={`Connect ${editing.label}`}
          onClose={() => setEditing(null)}
        >
          <p className="muted">
            These settings are saved privately to your account.
          </p>
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              setBusy(true);
              setError("");
              const data = Object.fromEntries(new FormData(e.currentTarget));
              try {
                await api("/connections/" + editing.id, {
                  method: "PUT",
                  body: json(data),
                });
                await onChange();
                onNotice(`${editing.label} connected.`);
                setEditing(null);
              } catch (e) {
                setError((e as Error).message);
              } finally {
                setBusy(false);
              }
            }}
          >
            {editing.id === "github" ? (
              <>
                <label>
                  Repository
                  <input
                    name="repo"
                    placeholder="owner/repository"
                    defaultValue={editing.account}
                    required
                    autoFocus
                  />
                </label>
                <label>
                  Personal access token
                  <input
                    name="token"
                    type="password"
                    autoComplete="off"
                    placeholder="Optional for a public repository"
                  />
                </label>
                <small className="muted">
                  Private repositories require a token with permission to read
                  their issues.
                </small>
              </>
            ) : (
              <>
                <label>
                  Channel ID
                  <input
                    name="channel"
                    placeholder="C0123456789"
                    defaultValue={editing.account}
                    required
                    autoFocus
                  />
                </label>
                <label>
                  Bot token
                  <input
                    name="token"
                    type="password"
                    autoComplete="off"
                    required
                    placeholder="xoxb-…"
                  />
                </label>
                <small className="muted">
                  Invite the bot to this channel and grant channel history
                  permissions.
                </small>
              </>
            )}
            {error && (
              <p className="error" role="alert">
                {error}
              </p>
            )}
            <button className="primary wide" disabled={busy}>
              {busy ? "Checking connection…" : "Connect securely"}
              <Plug size={15} />
            </button>
          </form>
        </Modal>
      )}
    </section>
  );
}
function Modal({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const el = ref.current!;
    el.showModal();
    const cancel = (e: Event) => {
      e.preventDefault();
      closeRef.current();
    };
    el.addEventListener("cancel", cancel);
    return () => {
      el.removeEventListener("cancel", cancel);
      el.close();
    };
  }, []);
  return (
    <dialog
      ref={ref}
      className="modal"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      aria-label={title}
    >
      <div className="modal-inner">
        <header>
          <h2>{title}</h2>
          <button
            className="icon-button"
            aria-label="Close dialog"
            onClick={onClose}
          >
            <X size={18} />
          </button>
        </header>
        {children}
      </div>
    </dialog>
  );
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
