# Pilant Studio (`studio.py`) — Route & Data-Model Map

Source of truth for converting `studio.py` from server-rendered HTML into a JSON API for a
React frontend. Based on a full read of `studio.py` (2834 lines), `users.py`, `saved_views.py`,
relevant slices of `renderer.py`, `gmail_site.py`, `agent_gmail.py`, `claude_engine.py`, and the
full `UI_SCHEMA` in `schema.py`.

---

## 0. Top-level app wiring

- `app = Flask(__name__)`, secret key from `FLASK_SECRET_KEY` env var or a random per-process value.
- `app.register_blueprint(gmail_bp)` — the real interactive Gmail inbox (`gmail_site.py`) is
  mounted with **no url_prefix**, so its routes (`/inbox`, `/compose`, `/message/<id>`, etc.) live
  at the top level alongside everything in `studio.py`. Any API redesign must account for these
  routes too if "the app" means the whole product, not just `studio.py`'s own routes.
- Fixed constant: `PORT = 5008`, `GMAIL_OAUTH_REDIRECT_URI = f"http://127.0.0.1:{PORT}/oauth/gmail/callback"`.
  Google's OAuth client has this exact URI registered; a JSON-API redesign that changes ports/hosts
  must update both the Google Cloud Console registration and this constant together.

---

## 1. Every Flask route in `studio.py`

Format: `METHOD path` — auth — reads — returns.

### Auth
| Route | Methods | Auth | Reads | Returns |
|---|---|---|---|---|
| `/login` | GET, POST | none | POST: `request.form["username"]`, `request.form["password"]` | GET: `render_login()` HTML page. POST success: `session.clear()`, sets `session["username"]`, `redirect(/studio)`. POST failure: `render_login(error=...)` HTML with generic "Incorrect username or password." |
| `/logout` | GET | none | — | `session.clear()`, `redirect(/login)` |
| `/` | GET | `@login_required` | — | `redirect(/studio)` |

### Studio shell (chat + live preview)
| Route | Methods | Auth | Reads | Returns |
|---|---|---|---|---|
| `/studio` | GET | `@login_required` | `?panel_folder=`, `?panel_chip=`, `?panel_message=`, `?jira_detail=` (all query params); mutates `wf["gmail_panel_folder"/"gmail_panel_query"/"gmail_panel_search_text"]` as a side effect of `?panel_folder=`/`?panel_chip=` | Full HTML page: `render_studio(user, wf, panel_folder, panel_message, jira_detail)` — sidebar + chat history + live preview panel |
| `/studio/panel_search` | POST | `@login_required` | form: `panel_folder`, `panel_q` | Sets `wf["gmail_panel_folder"]`, `wf["gmail_panel_search_text"]`, `wf["gmail_panel_query"]` (via `resolve_panel_query`); `redirect(/studio)`. No-op redirect if `wf["connector"] != "gmail"`. |
| `/studio/jira_resolve/<key>` | POST | `@login_required` | URL param `key` | Calls `connectors_jira.set_issue_status(key, "Done")` (real in-memory mutation); `redirect(f"/studio?jira_detail={key}")` |
| `/studio/message` | POST | `@login_required` | form: `text` | **The core chat endpoint** — see §4 below. Always ends with `redirect(/studio)`; all state changes are server-side, then the whole page re-renders. |
| `/studio/new` | POST | `@login_required` | — | `_create_workflow()` with `connector=None`; sets `session["current_workflow"]`; `redirect(/studio)` |
| `/studio/new/<connector_key>` | POST | `@login_required` | URL param `connector_key` | `_create_workflow(connector=connector_key if valid else None)`; sets `session["current_workflow"]`; `redirect(/studio)` |
| `/studio/open/<workflow_id>` | GET | `@login_required` | URL param `workflow_id` | If id exists in `WORKFLOWS`, sets `session["current_workflow"] = workflow_id`; always `redirect(/studio)` (silently no-ops for a bad id, still redirecting) |

### Integrations
| Route | Methods | Auth | Reads | Returns |
|---|---|---|---|---|
| `/integrations` | GET | `@login_required` | `?category=`, `?installed=1`; pops `session["integrations_notice"]` / `session["integrations_notice_kind"]` (one-shot flash) | Full HTML page: `render_integrations(...)` — category sidebar + card grid |
| `/oauth/gmail/connect` | GET | `@login_required` | — | Generates CSRF `state` token into `session["gmail_oauth_state"]`, `redirect()`s to Google's real OAuth consent URL (`connectors_gmail.get_authorization_url`). On `RuntimeError` (misconfigured), sets flash notice and `redirect(/integrations)` instead. |
| `/oauth/gmail/callback` | GET | `@login_required` | `?error=`, `?state=`, `?code=` | Validates `state` against session, exchanges `code` for tokens (`connectors_gmail.exchange_code_for_tokens`), sets one-shot flash notice, `redirect(/integrations)` always |
| `/oauth/gmail/disconnect` | POST | `@login_required` | — | `connectors_gmail.disconnect()`; flash notice; `redirect(/integrations)` |
| `/rag/sync` | POST | `@login_required` | — | Runs `rag_ingest.ingest_all()`, builds a per-connector summary flash notice; `redirect(/integrations)` |

### Composer (cross-connector canvas)
| Route | Methods | Auth | Reads | Returns |
|---|---|---|---|---|
| `/composer` | GET, POST | `@login_required` | POST form: `primitive_ids` (multi, `getlist`), `instruction` | GET: `render_composer(user)` empty canvas. POST: runs `agent_composer.run_agent(...)`, returns `render_composer(...)` with `render=`, `clarify=`, or `agent_error=` depending on result key (see §4's result-shape contract, same as workflow chat) |
| `/composer/style` | POST | `@login_required` | form: `style` | `user_style.set_style(username, style)`; returns `render_composer(user, style_saved=True)` |
| `/composer/save` | POST | `@login_required` | form: `name`, `instruction`, `primitive_ids` (multi) | `save_view(username, name, instruction, primitive_ids=...)`; `redirect(/composer/open/<new id>)` |
| `/composer/open/<view_id>` | GET | `@login_required` | URL param `view_id` | `get_view(username, view_id)`; if found, **re-runs** `agent_composer.run_agent` live (never a stored snapshot); renders composer page with `opened=saved`. If not found (or belongs to another user — indistinguishable), `redirect(/composer)` |
| `/composer/<view_id>/delete` | POST | `@login_required` | URL param `view_id` | `delete_view(username, view_id)`; `redirect(/composer)` |

### Customers (Customer-360)
| Route | Methods | Auth | Reads | Returns |
|---|---|---|---|---|
| `/customers` | GET | `@login_required` | `?q=` | Full HTML page: customer directory list (derived live from `connectors_retail.get_orders()`, deduped by email/customer_id), filtered by `q` against name/key |
| `/customers/open` | GET | `@login_required` | `?key=` | If no key, `redirect(/customers)`. Else builds a fixed `request_text` and calls `agent_composer.run_agent(request_text, user=user, primitive_ids=["retail_orders"], first_turn=False)` synchronously, renders result or error inline |

### Misc
| Route | Methods | Auth | Reads | Returns |
|---|---|---|---|---|
| (inherited from `gmail_bp`) `/inbox`, `/compose`, `/message/<id>`, etc. | various | — | — | Out of `studio.py`'s scope but reachable at top level; a full JSON API needs to cover these too if "merge into Studio, one app" still applies. |

**Every route in this file returns full-page HTML or a redirect. There is no JSON endpoint anywhere in `studio.py` today** — the entire "API surface" a React frontend needs will have to be newly designed. There is no CSRF protection beyond the OAuth `state` param; POST routes are plain forms.

---

## 2. Session / auth model

- `login_required` (custom decorator, `studio.py:276`): checks `session.get("username")`; if falsy, `redirect(url_for("login"))`. No token/JWT — purely Flask's signed cookie session.
- **`session` keys used anywhere in `studio.py`:**
  - `session["username"]` — set on login, cleared on logout. The only thing that identifies who's logged in.
  - `session["current_workflow"]` — the currently-open workflow id (`wf_N`). Set by `/studio/new`, `/studio/new/<key>`, `/studio/open/<id>`, and lazily by `_current_workflow()` if unset/stale (creates a fresh connector-less workflow).
  - `session["gmail_oauth_state"]` — CSRF nonce for the OAuth flow, set by `/oauth/gmail/connect`, popped/checked by `/oauth/gmail/callback`.
  - `session["integrations_notice"]` / `session["integrations_notice_kind"]` — one-shot flash message (`"ok"` or `"error"`), written by the OAuth routes and `/rag/sync`, popped (read-once) by `GET /integrations`.
  - `session["composer_first_turn_done"]` — boolean flag so the Composer's bundled purpose/style question (`first_turn=True`) fires only once per login session, not once ever or every request.
- `users.py`:
  - `USERS` dict seeded with 3 demo accounts: `kowsick`/owner (label_scope `None` = unrestricted), `analyst`/analyst (`label_scope=["bug","critical","security"]`), `viewer`/viewer (`label_scope=["enhancement","documentation"]`). Passwords hashed at import time with werkzeug.
  - `get_user(username)` → `{"username", "name", "role", "label_scope", "password_hash"}` dict; raises `RuntimeError` for unknown usernames (never called with untrusted input directly — always guarded by a prior `session["username"]` check).
  - `verify_login(username, password)` → same shape as `get_user` on success, `None` on ANY failure (unknown user or bad password look identical — no username enumeration).
  - `scope_issues(issues, user)` — enforces `user["label_scope"]` on a list of already-fetched GitHub-shaped issues (keeps only issues with a label in `label_scope`, or all issues if scope is `None`). This is the enforcement point for GitHub connector identity scoping (see `CONNECTORS["github"]["needs_user"]` below).
- **No per-user workflow scoping.** `WORKFLOWS`/`WORKFLOW_ORDER` are process-global — whoever is logged in shares the one set of workflows and the one Gmail inbox connection. A JSON API redesign that wants real multi-user isolation is a bigger change than swapping the transport; this is explicitly called out in the module docstring as a known limitation, not fixed here.

---

## 3. WORKFLOWS / WORKFLOW_ORDER data model

```python
WORKFLOWS = {}          # wf_id -> workflow dict
WORKFLOW_ORDER = []     # [wf_id, ...] most-recently-created first
_next_id_counter = [0]  # wf_1, wf_2, ... monotonically increasing, never reused
```

### Exact workflow object shape (`_create_workflow`, `studio.py:485-533`)

```python
{
    "id": "wf_3",
    "title": "Untitled workflow",       # becomes the first request's text (truncated to 42 chars + "…") once one succeeds
    "connector": None,                   # None until chosen; else one of CONNECTORS' keys
    "messages": [],                      # [{"role": "user"|"assistant", "text": str, "render": <optional, see below>}]
    "agent_messages": None,              # raw OpenAI/Anthropic-format conversation array, ONLY set mid-clarify (ask_user in progress); None otherwise
    "fetched_data": False,               # bool — whether this in-progress agent call has already fetched real data this turn
    "last_render": None,                 # last successful render_view JSON (UI_SCHEMA shape), or None — THE single source of truth for what the live preview shows
    "last_request_text": "",             # the text that produced last_render, shown as a quoted caption above the preview
    "gmail_panel_folder": "inbox",       # which real Gmail folder the embedded panel is scoped to (gmail workflows only)
    "gmail_panel_query": None,           # real resolved Gmail search syntax currently filtering the panel (gmail workflows only)
    "gmail_panel_search_text": None,     # raw text last typed into the PANEL's own search box (kept separately from the resolved query, gmail workflows only)
    "memory": None,                      # None, or a bounded list of {"role": "system"|"user"|"assistant", "content": str} — cross-request conversational memory (see below)
}
```

- **Create**: `_create_workflow(connector=None)` → new id, inserted at index 0 of `WORKFLOW_ORDER` (newest-first sidebar order), stored in `WORKFLOWS`.
- **Read (current)**: `_current_workflow()` = `WORKFLOWS.get(session["current_workflow"])`; if missing (never set, or stale/deleted id), silently creates a brand-new connector-less workflow and rebinds the session to it. **There is no "workflow not found" error state exposed to the user** — it's papered over by always creating a new one.
- **Read (list)**: iterate `WORKFLOW_ORDER`, look up in `WORKFLOWS` — this is what the sidebar renders (`_sidebar_html`).
- **Update**: workflow dicts are mutated in place throughout `_run_agent_for_workflow`, `studio_message`, `/studio`, `/studio/panel_search`, `/studio/jira_resolve/<key>` (indirectly, via `connectors_jira`, not `wf` itself). No explicit "update" API exists — every route just reaches into the dict.
- **Delete**: **there is no delete-workflow route anywhere in `studio.py`.** Workflows only disappear when the process restarts (in-memory only, explicitly documented as a known/accepted limitation). A JSON API should probably add one, but note its absence isn't an oversight in scope here — it's undesigned territory.

### `wf["messages"]` entry shape
```python
{"role": "user", "text": "show my unread emails"}
{"role": "assistant", "text": "Built it — see the live preview on the right.", "render": {...UI_SCHEMA shape...}}   # "render" key present only on a successful build turn
{"role": "assistant", "text": "Couldn't build that (...). Try rephrasing — the last working preview is still there on the right."}   # error turn, no "render" key
```
Note: `_chat_html` currently **never displays** the per-message `render` snapshot (removed 2026-08-31, "unified" screen's inline card read as clutter) — but the data is still stored on every message and is exactly what `renderer.render_chat_inline_result(view, max_rows=3)` was built to consume, in case a JSON API wants per-turn result cards back.

### `wf["memory"]` — cross-request conversation memory
- Written **only** by `_append_memory_exchange(wf, user_text, assistant_summary)`, called after a successful `"render"` result and after a plain conversational `"text"` reply — **never** after an `"error"` result (a failed attempt must not corrupt existing good memory).
- First write seeds `memory[0] = {"role": "system", "content": CONNECTORS[wf["connector"]]["system"]}` (each connector's own `SYSTEM` prompt constant) if the connector has one.
- Then appends `{"role": "user", "content": user_text}`, `{"role": "assistant", "content": assistant_summary}` — `assistant_summary` is **not** the raw model output, it's a synthesized one-line description from `_render_memory_line(render)` (e.g. `Built "Unread emails" — a list of 8 item(s).`) — see that function (`studio.py:2425-2494`) for the exact per-component-type phrasing table (covers all 17 `UI_SCHEMA` component types).
- Trimmed to `MEMORY_MAX_EXCHANGES = 6` most-recent exchanges (12 messages) + the system message, oldest dropped first.
- Cleared to `None` whenever the workflow switches connectors (`studio_message`'s switch branch) — memory is connector-domain-specific.

### CONNECTORS registry → workflow mapping

```python
CONNECTORS = {
    "gmail":    {"label": "Gmail", "icon": "✉️", "run_agent": gmail_run_agent, "oauth": True,
                 "system": agent_gmail.SYSTEM, "first_turn": True, "category": "email",
                 "description": "..."},
    "slack":    {"label": "Slack", ..., "system": agent_slack.SYSTEM, "first_turn": True, "category": "messaging"},
    "github":   {"label": "GitHub", ..., "needs_user": True, "system": agent_github.SYSTEM, "first_turn": True, "category": "developer"},
    "helpdesk": {"label": "Helpdesk", ..., "system": agent_helpdesk.SYSTEM, "first_turn": True, "category": "support"},
    "jira":     {"label": "Jira", ..., "system": agent_jira.SYSTEM, "first_turn": True, "category": "developer"},
    "unified":  {"label": "All apps", ..., "run_agent": unified_run_agent, "system": agent_unified.system_snapshot(),
                 "first_turn": True, "category": "overview"},
    "custom":   {"label": "Custom interface", "icon": "🎨", "run_agent": custom_run_agent, "freeform": True,
                 "system": agent_custom.SYSTEM},   # NOTE: no "first_turn" key, no "category" key
}
DEFAULT_CONNECTOR = "gmail"   # unused as far as workflow creation goes — new workflows start with connector=None
```

Key flags a JSON API needs to replicate:
- `oauth: True` — only `gmail` has this; drives the real Google OAuth flow vs. static `.env` credentials for everyone else.
- `needs_user: True` — only `github`; its `run_agent` is called with a `user=` kwarg for `scope_issues()` label-based identity filtering. No other connector's `run_agent` accepts that kwarg (calling with it would `TypeError`).
- `first_turn: True` — every real connector except `custom`. Passed as `first_turn=True` **only** on a workflow's very first top-level request (no prior clarify in progress, no memory yet) — see `_run_agent_for_workflow`'s three-branch dispatch below.
- `freeform: True` — only `custom`; excluded from `_connector_list_text()`, `_integrations_page_html()`'s cards, and `_CONNECTOR_LINKS_BY_LABEL`/`_CONNECTOR_COLORS_BY_LABEL`. It's the fallback, never a user-visible pick-by-name option.
- `system` — each connector's module-level `SYSTEM` prompt constant (for `unified`, `agent_unified.system_snapshot()` — a function call, not a constant, since it aggregates the others' state).
- `category` — used only by the Integrations page's category filter (`CATEGORY_META`); `custom` deliberately has none since it isn't a category-filterable card.

`_CONNECTOR_LINKS_BY_LABEL` = `{lowercased label: "/studio/new/<key>"}` for every non-freeform connector — passed to `renderer.render_fragment`'s `connector_links` param so a merged "unified" screen's per-app list titles become clickable links that create+open a brand-new single-app workflow.

`_CONNECTOR_ACCENT` / `_connector_accent(key)` — per-connector `{bg, fg}` hex color pair for avatar chips (sidebar cards + Integrations cards), plus a synthetic `"knowledge_base"` entry and a `_DEFAULT_CONNECTOR_ACCENT` fallback.

---

## 4. Chat / compose flow end to end (`POST /studio/message` → `studio_message()`, `studio.py:2681-2808`)

### Step 1 — always
```python
text = (request.form.get("text") or "").strip()
wf = _current_workflow()
if not text:
    return redirect(url_for("studio"))    # blank submission is silently ignored
wf["messages"].append({"role": "user", "text": text})
```

### Step 2 — branch on `wf["connector"]`

**Branch A: `wf["connector"] is None`** (brand-new workflow, first message is read as "which app?")
1. `_match_all_connectors(text)` — if **2+** real connectors are named (e.g. "connect gmail and jira"), lock `wf["connector"] = "unified"`, post a confirmation bubble, then immediately call `_run_agent_for_workflow(wf, f"Show me what's relevant across {joined}.")`.
2. Else if exactly one connector is named (`_match_all_connectors(text)[0]`), lock that connector, post "Connected to X." bubble, then `_run_agent_for_workflow(wf, f"I want to build something with my {label}.")` — a **synthesized** request text, not the user's literal message, deliberately made explicit so a `force_tool_choice=False` connector doesn't mistake a one-word "gmail" for small talk and skip its first-turn question.
3. Else `_infer_connector_from_intent(text)` (domain keywords like "inbox"/"ticket"/"sprint"/"urgent" even without naming the app by name) — if it matches, lock that connector and forward `text` itself (the real request) via `_run_agent_for_workflow(wf, text)`.
4. Else fall through to `wf["connector"] = "custom"` and `_run_agent_for_workflow(wf, text)` — the free-form fabricated-data connector, last resort.

All four sub-paths end with `redirect(url_for("studio"))`.

**Branch B: `wf["connector"]` already set** — check for a mid-conversation connector switch:
1. `_detect_connector_switch(text, wf["connector"])` — requires BOTH a connect-ish verb (`connect/switch/integrate/hook up/link/add/change`) AND a named connector different from the current one.
2. Else `_is_bare_connector_name(text)` — text that, stripped of punctuation/whitespace, is EXACTLY a connector's key or label (e.g. just "gmail") — catches the narrower case verb-gating misses.
3. If either matches: reset `wf["agent_messages"]=None`, `wf["fetched_data"]=False`, `wf["memory"]=None` (memory doesn't carry across connectors), set `wf["connector"]`, post a "Switched this workflow to X." bubble with example prompts. **Does not build anything this turn** — the switch itself is the whole response. `redirect(/studio)`.
4. Otherwise, it's a real request for the current connector: `_run_agent_for_workflow(wf, text)`, then `redirect(/studio)`.

### `_run_agent_for_workflow(wf, text)` (`studio.py:2528-2678`) — the actual model call

```python
connector = CONNECTORS[wf["connector"]]
user = get_user(session["username"])
call_kwargs = {}
if connector.get("needs_user"):
    call_kwargs["user"] = user                      # only GitHub
hint = layout_usage.summarize_usage(user.get("role"), wf["connector"])
if hint:
    call_kwargs["extra_system"] = hint               # role-level layout usage hint, once enough history exists

if wf["agent_messages"]:                             # resuming an in-progress clarify (ask_user round)
    agent_messages = wf["agent_messages"] + [{"role": "user", "content": text}]
    result = connector["run_agent"](messages=agent_messages, fetched_data=wf["fetched_data"], **call_kwargs)
elif wf["memory"]:                                    # brand-new top-level request, but prior successful exchange(s) exist
    agent_messages = wf["memory"] + [{"role": "user", "content": text}]
    result = connector["run_agent"](messages=agent_messages, fetched_data=False, **call_kwargs)   # fetched_data reset — force a fresh fetch
else:                                                 # genuinely the very first request of a brand-new workflow
    if connector.get("first_turn"):
        call_kwargs["first_turn"] = True
    result = connector["run_agent"](text, **call_kwargs)
```

Wrapped in `try/except Exception` — belt-and-braces against a bug in the engine itself (run_agent is documented to catch its own network/model errors and return `{"error": ...}` rather than raise); on an uncaught exception, appends a generic "Something went wrong building that (ExceptionType)." message, resets `agent_messages`/`fetched_data`, and **returns without touching `last_render`**.

### `result` dict contract (from `claude_engine.run_claude_agent` via each connector's `run_agent`)

Exactly one of these shapes comes back (see `agent_gmail.py`/`claude_engine.py` for the canonical version every connector follows):

| Key present | Meaning | Other keys |
|---|---|---|
| `"clarify"` | Model called `ask_user` — needs more info before it can act | `"messages"`: the raw conversation array to resume with next turn (NOT serializable-safe long-term, but JSON-safe dict-shaped blocks); `"fetched_data"`: bool, whether a fetch already happened this turn (carries forward on resume) |
| `"error"` | Model failed (hit max_steps, guardrail rejection, tool failure) or `{"_network_error": True, "error": "..."}" for a network/API failure reaching Anthropic | — |
| `"render"` | A `render_view` call passed schema validation + fabrication guardrails | `"fetch_args"`: (Gmail only) the real `{folder, query, unread_only}` the fetch used — can be `None` even on success (e.g. resuming a clarify that had already fetched earlier) |
| `"text"` | Plain conversational reply — no tool call, or a no-tool-call turn accepted because nothing was fetched this turn | — |

`_run_agent_for_workflow` handles each:
- **`"clarify"`**: append assistant bubble with the question text; `wf["agent_messages"] = result["messages"]`; `wf["fetched_data"] = result.get("fetched_data", False)`. Memory is **not** touched (exchange isn't finished yet).
- **`"error"`**: append assistant bubble `f"Couldn't build that ({result['error']}). Try rephrasing — the last working preview is still there on the right."`; reset `agent_messages`/`fetched_data` to None/False. **`wf["last_render"]` is never touched** — this is the "fail safely, keep last good preview" guarantee stated in the module docstring: a failed/ambiguous turn only ever appends a chat message, it can never blank or corrupt the live preview pane.
- **`"render"`**: `wf["last_render"] = result["render"]`; `wf["last_request_text"] = text`; sets `wf["title"]` from `text` (truncated to 42 chars) if this is the workflow's first successful build; appends an assistant bubble `"Built it — see the live preview on the right."` with the render JSON attached as `message["render"]` (currently unused by `_chat_html` but present in data); resets `agent_messages`/`fetched_data`; calls `layout_usage.record_render(role, connector, text, [component types])`; if connector is `gmail` and `fetch_args` is present, re-scopes `wf["gmail_panel_*"]` to match exactly what was fetched; finally calls `_append_memory_exchange(wf, text, _render_memory_line(result["render"]))`.
- **anything else / `"text"`**: append assistant bubble with `result.get("text")` or a generic fallback; reset `agent_messages`/`fetched_data`; `_append_memory_exchange(wf, text, reply_text)`.

### What the live preview actually renders — `_preview_html(wf, ...)` (`studio.py:1440-1610`)

This is the piece a JSON API most needs to replicate as data, since today it's baked into HTML generation:
1. If `?jira_detail=<key>` is set (transient, never stored on `wf`): fetch `connectors_jira.get_issue(key)` live and render `_jira_detail_panel_html` — a **full current** issue detail (re-fetched, not the model's row summary) with a real "Mark resolved" action (`POST /studio/jira_resolve/<key>`). Falls through to normal preview if the key is stale.
2. Else if `wf["connector"] == "gmail"`: the panel is **not** derived from `wf["last_render"]` at all — it's a live, deterministic re-render of the real inbox (`gmail_site.render_inbox_panel`/`render_inbox_panel_tabs`/`render_inbox_panel_chips`/`render_inbox_panel_message`), scoped by `wf["gmail_panel_folder"]`/`wf["gmail_panel_query"]`, with real working star/delete/mark-read actions. This is deliberately bypassing the LLM's render_view entirely for Gmail specifically, because `render_view`'s schema has no real message-ID field and the model is never trusted to pick which real email a write action targets.
3. Else (every other connector): if `wf["last_render"]` is `None`, show an empty-state placeholder ("Nothing generated yet…"). Otherwise `renderer.render_fragment(wf["last_render"], connector_links=_CONNECTOR_LINKS_BY_LABEL, connector_colors=_CONNECTOR_COLORS_BY_LABEL, jira_detail_base="/studio?jira_detail=", jira_only=(wf["connector"]=="jira"))` — i.e. **`wf["last_render"]` (raw UI_SCHEMA JSON) is exactly what a JSON API should hand the React frontend as-is** for every non-Gmail connector; the frontend would implement its own version of `render_fragment`'s component-type switch instead of calling the Python renderer.

**"Fail safely, keep last good preview" — concretely**: the ONLY place `wf["last_render"]` is ever assigned is inside the `"render"` branch above. No error/clarify/text path touches it. A JSON API's equivalent contract: a failed/ambiguous chat turn returns/updates only the message list and (for clarify) a pending-state flag — it must never null out or replace whatever the last successful `render_view` JSON was.

---

## 5. Saved views & layout (`saved_views.py`) and studio.py's use of them

`saved_views.py` persists to a single JSON file (`saved_views.json`), keyed by username, guarded by a process-wide `threading.Lock()`.

- `list_views(username)` → this user's list of view dicts, oldest first. Never another user's.
- `get_view(username, view_id)` → one view dict if it exists **and** belongs to `username`, else `None` — a foreign or nonexistent id is indistinguishable (no info leak).
- `save_view(username, name, query, primitive_ids=None)` → creates `{"id": uuid4[:12], "name": name.strip() or query or "Untitled", "query": query}`, plus `"primitive_ids": list(...)` **only if given** (so old 3-arg callers get views with no such key at all — additive, not breaking). Appends to that user's list, writes the whole file.
- `delete_view(username, view_id)` → removes only if owned by `username`; silent no-op otherwise.
- `save_layout(username, view_id, order, total)` → stores `view["layout"] = {"order": [ints...], "total": total}` on the matching view (cleans `order` to non-negative ints only); returns `True`/`False` for found/not-found, silent no-op if not owned.
- `apply_layout(components, layout)` → pure function, reorders/filters a **freshly-regenerated** components list by **position** (no stable identity across regenerations):
  - If `layout["total"] == len(components)` (same count as when saved): saved order is authoritative, **including removal** — any index not listed is hidden.
  - If the count differs: anything not in the saved order is appended rather than hidden (never silently hides new real content).

### studio.py routes that call into `saved_views.py`
- `POST /composer/save` → `save_view(username, name, instruction, primitive_ids=selected_ids)`, then redirects into `composer_open`.
- `GET /composer/open/<view_id>` → `get_view(username, view_id)`, then **re-runs** `agent_composer.run_agent` live with the saved `primitive_ids`/`query` — this is the "not a snapshot" philosophy: what's stored is *what to re-run*, not the rendered result.
- `POST /composer/<view_id>/delete` → `delete_view(username, view_id)`.
- `_composer_saved_list_html(username)` → `list_views(username)` filtered to only those with a `"primitive_ids"` key (a plain query-only saved view from the old `server.py` app wouldn't make sense reopened through the Composer's multi-primitive engine).

**Important gap**: `save_layout`/`apply_layout` exist fully in `saved_views.py` and `renderer.render_editable_html`/`render_editable_fragment` exist fully in `renderer.py` (drag-to-reorder UI, POST target implied to be `/views/<id>/layout`) — but **`studio.py` has no route that calls `save_layout` or `apply_layout`, and no route serves `render_editable_html`.** This whole "reshape a saved view's layout" feature is wired in `renderer.py`/`saved_views.py` but **not connected to any live `studio.py` route** in this file as it stands today (it may be a remnant of the older, separate `server.py` app referenced in `saved_views.py`'s docstring). A JSON API redesign should treat this as "exists as a library capability, currently unreachable from Studio," not as an active feature to replicate faithfully — worth flagging back to whoever designs the new API rather than silently dropping or silently wiring it up.

---

## 6. Integrations page & OAuth flow

### `GET /integrations` (`studio.py:2142-2160` → `render_integrations` → `_integrations_page_html`)
- Query params: `?category=<key>` (validated against `CATEGORY_META` keys: `overview`, `email`, `messaging`, `developer`, `support`, `search` — invalid values silently reset to "all"), `?installed=1` (boolean toggle).
- Pops one-shot `session["integrations_notice"]`/`session["integrations_notice_kind"]`.
- Builds a card per `CONNECTORS` entry (excluding `freeform` ones — i.e. excludes `custom`) via `_connector_card_data(key, cfg)`, plus one synthetic card from `_knowledge_base_card_data()` (RAG sync status — not a `CONNECTORS` entry at all).
- Category counts (`Counter`) drive the left sidebar; filtering is done server-side via query params, not client-side JS.

### `_connector_card_data(key, cfg)` — per-connector card contents (`studio.py:878-934`)
- **Gmail (`cfg["oauth"]` is True)**: real dynamic state from `connectors_gmail.get_connected_account()`.
  - Connected: status = `"Connected as {email}"`; actions = Reconnect link (`/oauth/gmail/connect`), Disconnect form (`POST /oauth/gmail/disconnect`), "Open full inbox" link (`/inbox?folder=inbox`), "Start a workflow" form (`POST /studio/new/gmail`). `installed=True`.
  - Not connected: status = "Not connected yet — falls back to the shared inbox configured in .env, if any."; actions = Connect link (`/oauth/gmail/connect`) + full-inbox link + start-workflow form. `installed=False`.
- **Every other connector (Slack/GitHub/Helpdesk/Jira/unified)**: static — status = "Connected via this Studio's server credentials."; actions = only the "Start a workflow" form (`POST /studio/new/<key>`); `installed=True` **always** (static `.env` credentials are "already active" by definition — there's nothing to actually connect/disconnect for these). **This is the exact mechanic for connectors without real OAuth**: no Connect/Disconnect button rendered at all, just an honest "already wired to server creds" caption plus a shortcut into a workflow.

### `GET /oauth/gmail/connect` (`studio.py:2313-2334`)
1. Logs the fixed redirect URI to stderr (operator troubleshooting aid).
2. `state = secrets.token_urlsafe(24)`, stashed in `session["gmail_oauth_state"]`.
3. `connectors_gmail.get_authorization_url(GMAIL_OAUTH_REDIRECT_URI, state=state)` — real Google URL.
4. On `RuntimeError` (e.g. missing client id/secret config): flash error notice, `redirect(/integrations)` instead of crashing.
5. Otherwise `redirect(auth_url)` — browser goes to Google's real sign-in page. **This app never sees the person's real Gmail password.**

### `GET /oauth/gmail/callback` (`studio.py:2337-2371`)
1. Reads `?error=` — if Google reports cancellation/denial, flash error, redirect to `/integrations`.
2. Pops `session["gmail_oauth_state"]`; compares against `?state=`. Mismatch/missing → flash "stale or tampered" error, redirect.
3. Reads `?code=` — missing → flash error, redirect.
4. `connectors_gmail.exchange_code_for_tokens(code, GMAIL_OAUTH_REDIRECT_URI)` → real `email_address` on success, or `RuntimeError` → flash error, redirect.
5. Success: flash `f"Connected Gmail as {email_address}."`, `notice_kind="ok"`, redirect to `/integrations`.

### `POST /oauth/gmail/disconnect` (`studio.py:2374-2380`)
- `connectors_gmail.disconnect()` — clears the connector's in-memory refresh token (process-wide, not per-user/per-session — same "one shared connection" limitation as workflows).
- Flash "Disconnected. New requests will fall back to the shared inbox configured in .env, if any.", redirect.

### Connection-state tracking summary
- **Gmail**: real, dynamic, in-memory state inside `connectors_gmail` module (`get_connected_account()`/`disconnect()`/token exchange) — genuinely reflects whether an OAuth token is currently held, process-wide (not session-scoped, not per-user).
- **Slack/GitHub/Helpdesk/Jira/unified**: no connection state at all to track — they're always "connected" (`installed=True` unconditionally) because they run on static `.env` credentials configured once at process start. There is no per-connector "disconnect" or "reconnect" concept for these; a JSON API's `/integrations` equivalent should model this as a **binary flag per connector: `has_oauth: bool`** with Gmail the only `true` today, rather than modeling every connector as if it had a real connect/disconnect lifecycle.
- **Knowledge base (RAG)**: separate synthetic card, not a `CONNECTORS` entry — `installed` reflects `rag_index.is_configured()` (an env var, `AWS_BEARER_TOKEN_BEDROCK`) plus whether anything has been indexed yet (`rag_index.stats()`); its one action is `POST /rag/sync`.

---

## 7. Other stateful mechanics

### "+ New workflow" / connector-selection flow (the first-chat-message-picks-the-connector pattern)
Fully covered in §4 Branch A. Key nuances a JSON API must preserve:
- **`_match_all_connectors`** vs **`_match_connector`**: the former returns every named connector (fixes a real bug where "connect gmail and jira" used to silently drop "jira"); ≥2 matches routes to the `unified` connector automatically, not to the first-named one.
- **`_infer_connector_from_intent`**: a message that never names an app by name (e.g. "show my inbox", "what's high priority") still routes to a real connector via domain keyword tables (`_CONNECTOR_INTENT_KEYWORDS`), checked in dict order (`gmail→slack→github→helpdesk→jira→unified`, `unified` deliberately last as a catch-all). Only falls through to `custom` (fabricated data) if truly nothing matches.
- **`_is_bare_connector_name`**: an exact, whole-message match against a connector's key/label (post-punctuation-stripping) is treated as unambiguous even *without* a connect-ish verb and *even mid-clarify* — this overrides whatever the current connector's `ask_user` was waiting for. This is checked in Branch B (existing connector) as a supplement to `_detect_connector_switch`, which requires an explicit verb.
- **Mid-conversation switch** (`_detect_connector_switch`) resets `agent_messages`, `fetched_data`, and `memory` to a blank slate but explicitly leaves `wf["last_render"]`/`wf["last_request_text"]` untouched — the old preview stays on screen (stale, from the old connector) until the new connector's chat produces something, and the chat bubble says so explicitly.

### `layout_usage.py` integration
- `layout_usage.summarize_usage(role, connector)` → `None` until enough history exists (module-level `MIN_EVENTS_FOR_HINT` threshold, not read in full here) else a one-sentence hint folded into the model's system prompt via `extra_system` kwarg — a role-level ("what layouts has this role's usage of this connector tended to favor") learning signal, applied to *every* call, not just after clarify.
- `layout_usage.record_render(role, connector, text, [component types])` — called only after a successful `"render"` outcome; logs **shape only** (component `type`s + request text), never underlying data — a privacy-conscious design choice worth preserving if a JSON API adds analytics.

### `user_style.py` integration (Composer only)
- `user_style.get_style(username)` / `user_style.set_style(username, style)` — a free-text per-user style preference ("I prefer compact lists over stat grids…"), persisted per-user, applied to every future Composer compose/reopen. Separate lifetime from a single `instruction` (which is per-request). Not wired into the four single-connector workflow chats — Composer-only.

### Jira detail panel + real write action
- `_jira_detail_panel_html(issue, back_url="/studio")` renders a **live re-fetch** of one issue's full field set (`connectors_jira.get_issue(key)`), never the model's trimmed row — reachable both from a `jira`-connector workflow's own preview and from a `unified` merged screen's Jira section (via `renderer.py`'s `jira_detail_base`/`jira_only` params rewriting a list row's link to `/studio?jira_detail=<key>`).
- `POST /studio/jira_resolve/<key>` is the one genuine write action in the whole generated-screen system — mutates `connectors_jira`'s in-memory `ISSUES` store via `set_issue_status(key, "Done")`. Not scoped to any workflow (any workflow showing that issue would see the updated status on its next fetch).
- This is the **only** write-back-into-a-connector action driven from a generated screen anywhere in `studio.py` (Gmail's star/delete/mark-read live in the separate deterministic `gmail_site.py` panel, not through `render_view`).

### Customer-360 (`/customers`, `/customers/open`)
- Not a `CONNECTORS` entry, not a chat — `_customer_directory()` derives a live list of distinct customers from `connectors_retail.get_orders()` (deduped by email, falling back to `customer_id`), no separate customer database exists anywhere.
- `customer_open` builds a **fixed, fully-specified** `request_text` (never free-form) and calls `agent_composer.run_agent(..., primitive_ids=["retail_orders"], first_turn=False)` synchronously — skips the whole first-turn-clarify machinery on purpose, since "open this customer" isn't an ambiguous request the way a free-form chat message can be.

### Gmail panel scoping (transient vs. persisted state)
- `panel_folder`/`panel_message`/`jira_detail` in `GET /studio` are **query params only** — never written to `wf` except `panel_folder` (which mirrors into `wf["gmail_panel_folder"]` as a side effect of an explicit tab click). `panel_message` and `jira_detail` are deliberately never stored on `wf`, so browser-back / a "back to results" link always returns to exactly the folder/query/chip scoping that was active before opening one item — a JSON API needs an equivalent "transient detail view, doesn't mutate underlying list state" concept for both of these.
- The panel's own inline search box (`POST /studio/panel_search`) is a **separate, non-LLM** search path from the chat — it calls `gmail_site.resolve_panel_query` (real-syntax-or-plain-English translation) directly, bypassing `run_agent` entirely.

### `first_turn` mechanics (DynamisOS-style bundled clarifying question)
- Fires **only** on `_run_agent_for_workflow`'s third branch (genuinely first request, no clarify in progress, no memory) for every connector with `first_turn: True` in its `CONNECTORS` entry (all real connectors except `custom`, which has its own separate 4-item clarifying checklist built into `agent_custom.py`'s own SYSTEM prompt logic, unrelated to this flag).
- The Composer has its own separate `first_turn` gate, tracked via `session["composer_first_turn_done"]` (once per login session, since the Composer has no per-workflow conversational memory of its own — every submission is independent).

---

## 8. `renderer.py` render_* function contracts (signatures only, for the JSON→UI mapping)

```python
render_fragment(view, connector_links=None, connector_colors=None, jira_detail_base=None, jira_only=False)
```
- `view`: the raw `UI_SCHEMA`-shaped dict (§9 below) — the ONLY required input.
- `connector_links`: optional `{lowercased connector label: url}` — makes a titled `list`/`timeline`/`task_queue` component's title a clickable link (used for `unified` merged screens).
- `connector_colors`: optional `{lowercased connector label: {"bg","fg"}}` — source-color dot + filter-chip bar when 2+ titled lists exist.
- `jira_detail_base`: optional URL prefix (studio.py passes `"/studio?jira_detail="`) — makes Jira rows in a `list`/`timeline`/`task_queue` link to the detail panel.
- `jira_only`: bool — bypass title-based Jira-row heuristic entirely (used when the WHOLE workflow is the `jira` connector).
- Returns: inner HTML only (heading + meta + components), no `<html>` shell — meant to embed inside a host page.

```python
render_html(view, request_text="", nav_html="", connector_links=None, connector_colors=None)
```
Same rendering as `render_fragment` plus a full page shell (CSS, `<html>`) and a `request_text` caption. **Not used by `studio.py` directly** (it builds its own page shell via `_page_shell`); listed for completeness since a JSON API's frontend equivalent is really `render_fragment`'s component list, not this.

```python
render_editable_html(view, request_text, nav_html, save_action)
```
Wraps `render_editable_fragment` (drag-to-reorder + remove, POSTs to `save_action` which is meant to be `/views/<id>/layout` calling `saved_views.save_layout`) in a full page. **Currently unreachable from any `studio.py` route** — see §5's gap note.

```python
render_chat_inline_result(view, max_rows=3)
```
Compact per-turn result card (max `max_rows` rows per component) meant to sit under a chat bubble — built but currently unused by `_chat_html` (removed 2026-08-31 as visual clutter, `render_chat_inline_result` itself untouched in `renderer.py`).

```python
render_gmail_fragment(view, account_label=None)
```
A Gmail-skinned rendering of the SAME `UI_SCHEMA` shape — **not what `studio.py`'s live Gmail preview actually uses today** (that's `gmail_site.render_inbox_panel*`, a fully separate, deterministic, non-LLM code path with real message IDs and real actions). This function is effectively dead in the current `studio.py` Gmail flow but still exported/imported.

**Takeaway for the JSON API design**: every non-Gmail workflow's live preview is fully specified by `wf["last_render"]` (raw `UI_SCHEMA` JSON) — that's what the API should return as-is; the frontend reimplements `_component_html`'s per-type switch in React. The Gmail workflow's preview is fundamentally different — it needs its own real data endpoints (folder/message list with real IDs, star/delete/mark-read actions) mirroring `gmail_site.py`'s deterministic functions, NOT `render_view` JSON, because `render_view` has no real message-ID field by design.

---

## 9. `UI_SCHEMA` (from `schema.py`) — the render_view JSON contract to pass through untouched

Top-level shape (`required: ["heading", "components"]`):
```json
{
  "heading": "string — directly answers the request",
  "meta": "string — one short context line under the heading (optional)",
  "components": [ /* ordered array, each item required: ["type"] */ ]
}
```

Component `type` enum (17 values) and which fields each one uses:

| type | Uses | Notes |
|---|---|---|
| `stat_grid` | `title`, `stats[]` (`label`,`value`,`tone`) | grid of stat cards |
| `panel` | `title`, `subtitle`, `badge`, `fields[]` (`label`,`value`,`hint?`), `action` | compact detail box |
| `list` | `title`, `rows[]` (`name`,`note`,`action`,`url?`,`badge?`) | plain scannable list |
| `suggestions` | `suggestions[]` (array of strings) | model's own judgment calls, never new facts |
| `timeline` | same `rows[]` shape as `list` | chronological, oldest-first by convention |
| `metric` | `stats[]` with exactly 1 entry | single emphasized headline number |
| `data_table` | `columns[]` (strings), `table_rows[]` (`values[]` matching columns order, `badge?`) | real multi-column table |
| `chart` | `stats[]` (bar per entry; `value` must be a plain number string) | breakdown/comparison |
| `alert` | `title` (the claim), `subtitle`, `badge` (sets tone/urgency) | freeform prose, guardrail-checked (`find_ungrounded_alert_claims`) |
| `task_queue` | same `rows[]` shape as `list`, rendered as checklist | outstanding work/to-dos |
| `detail_view` | same fields as `panel` | richer single-record treatment |
| `status_badge` | `title` (status label), `badge` (status text+tone) | quiet single-line status |
| `empty_state` | `title` (the "nothing matched" statement), `subtitle` | explicit zero-result screen |
| `error_state` | `title` (which source failed), `subtitle`, `badge`, `action` (recovery step) | structured failure card |
| `connection_state` | `stats[]` (one per app; value = "Connected"/"Not connected"/etc, tone matches) | integration-status specific |
| `pagination` | `page`, `total_pages`, `total_count` (all integers) | no connector currently supplies this data |
| `popover` | `title` (trigger label), `fields[]` (revealed content) | rendered as native `<details>/<summary>`, no JS |

Shared sub-shapes:
- `badge`: `{text, tone: default|critical|warning|good, hint?}`
- `fields[]` item: `{label, value, hint?}` (required: `label`,`value`)
- `stats[]` item: `{label, value, tone?}` (required: `label`,`value`)
- `rows[]` item: `{name, note?, action?, url?, badge?}` (required: `name`) — `url` must be a real verbatim link from fetched data, never invented; omit if none.
- `table_rows[]` item: `{values: [strings], badge?}` (required: `values`)
- `hint` (on any `fields`/`badge` object): optional hover-tooltip text, explanatory only, never a place for a new fact.

**For the JSON API**: this schema is exactly what `wf["last_render"]` holds and exactly what the new API should return verbatim to the React frontend for any non-Gmail connector's preview (and for Composer's `render` result, and for the chat message's stored `render` snapshot) — no server-side reshaping needed, just pass-through. The frontend's job is to implement the 17-type switch as React components instead of `renderer.py`'s HTML-string switch.
