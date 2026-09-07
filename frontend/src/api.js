// Thin fetch wrapper around studio.py's JSON API. All routes are same-origin
// (Vite's dev proxy forwards /api to Flask; in production Flask serves both
// the SPA and these routes directly — see vite.config.js / studio.py's
// FRONTEND_DIST section), and auth is the existing Flask session cookie, so
// every call just needs credentials: "include".

class ApiError extends Error {
  constructor(message, status, body) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

async function request(path, options = {}) {
  const res = await fetch(path, {
    credentials: "include",
    headers: options.body ? { "Content-Type": "application/json" } : undefined,
    ...options,
  });
  let data = null;
  try {
    data = await res.json();
  } catch {
    // Some routes (e.g. a network hiccup) may not return JSON at all.
  }
  if (!res.ok) {
    throw new ApiError((data && data.error) || res.statusText, res.status, data);
  }
  return data;
}

const post = (path, body) => request(path, { method: "POST", body: body ? JSON.stringify(body) : undefined });

export const api = {
  ApiError,
  login: (username, password) => post("/api/login", { username, password }),
  logout: () => post("/api/logout"),
  me: () => request("/api/me"),
  connectors: () => request("/api/connectors"),
  workflows: () => request("/api/workflows"),
  createWorkflow: (connector) => post("/api/workflows", { connector }),
  getWorkflow: (id) => request(`/api/workflows/${encodeURIComponent(id)}`),
  openWorkflow: (id) => post(`/api/workflows/${encodeURIComponent(id)}/open`),
  sendMessage: (id, text) => post(`/api/workflows/${encodeURIComponent(id)}/message`, { text }),
  jiraIssue: (key) => request(`/api/jira/${encodeURIComponent(key)}`),
  jiraResolve: (key) => post(`/api/jira/${encodeURIComponent(key)}/resolve`),
  integrations: () => request("/api/integrations"),
  disconnectGmail: () => post("/oauth/gmail/disconnect"),
  ragSync: () => post("/rag/sync"),
  listViews: () => request("/api/views"),
  saveView: (name, query) => post("/api/views", { name, query }),
};
