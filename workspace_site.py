"""
workspace_site.py — added 2026-08-29. The new PRIMARY Studio surface:
"describe a goal -> understand context -> check connected systems ->
select trusted Primitives -> generate a task-specific workspace",
replacing "choose Gmail -> chat with agent -> generate a Gmail-like
interface" as the thing a person sees first. Built as an independent
Flask Blueprint, same pattern gmail_site.py already established (own
login_required, own CSS, mounted with no url_prefix) — see that file's
module docstring for why (studio.py imports this one, so this one can't
import studio.py back without a cycle).

This module owns NO business logic — it only builds HTML and routes
requests to workspace_engine.py (goal -> workspace), roles.py (RBAC),
approvals.py/audit_log.py (the human-approval + audit trail), and
workspace_store.py (save/reopen/version history). Every generated
workspace is composed entirely from workspace_primitives.py's typed,
registered component set — there is no template string here that emits
primitive-specific markup itself.

The existing /studio (single-connector chat) and /composer
(multi-primitive canvas) journeys are left completely intact in
studio.py; this is an additive new default, not a replacement of that
code. See studio.py's registration of this blueprint + its updated
`index()`/`login()` redirects for how "new primary journey" is wired in
without deleting the old one.
"""

import html as _html
import re
import uuid
from functools import wraps

from flask import Blueprint, request, session, redirect, url_for

import approvals
import audit_log
import roles
import workspace_engine
import workspace_primitives
import workspace_store
from users import get_user

workspace_bp = Blueprint("workspace", __name__)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("username"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def _esc(s):
    return _html.escape(str(s)) if s is not None else ""


# --- draft (in-progress, not-yet-necessarily-saved) workspace state --------

def _new_workspace_id():
    return "ws_" + uuid.uuid4().hex[:10]


def _current_draft():
    draft = session.get("ws_draft")
    if not draft:
        draft = {
            "id": _new_workspace_id(), "goal": workspace_engine.DEFAULT_GOAL,
            "role": roles.DEFAULT_ROLE, "name": None, "saved": False, "published": False,
        }
        session["ws_draft"] = draft
    return draft


def _save_draft(draft):
    session["ws_draft"] = draft


# --- flash (same pattern as gmail_site.py's _flash_html) -------------------

def _flash_html():
    ok = session.pop("flash_ok", None)
    err = session.pop("flash_err", None)
    parts = []
    if ok:
        parts.append(f'<div class="flash flash-ok">{_esc(ok)}</div>')
    if err:
        parts.append(f'<div class="flash flash-err">{_esc(err)}</div>')
    return "".join(parts)


# --- Gmail connection status, derived from what the generated workspace
# actually used (no extra API call beyond what generation already made) --

def _connected_apps(workspace):
    seen = {}
    for p in workspace["primitives"]:
        for s in p.get("data_sources") or []:
            name, mock = s["name"], bool(s.get("mock"))
            if name not in seen or seen[name] is True:
                seen[name] = mock
    seen.setdefault("Gmail", None)
    return seen


def _connected_apps_html(workspace):
    apps = _connected_apps(workspace)
    order = ["Gmail", "Salesforce", "Jira", "Helpdesk"]
    rows = []
    for name in order:
        if name not in apps:
            continue
        mock = apps[name]
        if mock is None:
            status, cls = "Not used in this workspace", "app-status-idle"
        elif mock:
            status, cls = "Simulated (mock connector)", "app-status-mock"
        else:
            status, cls = "Connected — live data", "app-status-live"
        rows.append(f'<li class="app-row"><span class="app-name">{_esc(name)}</span><span class="app-status {cls}">{status}</span></li>')
    return f'<ul class="app-list">{"".join(rows)}</ul>'


# --- Left panel: Prompt / Primitives / Data / Rules / History --------------

def _prompt_tab_html(draft):
    suggestions = "".join(
        f'<button type="button" class="suggested-request" data-goal="{_esc(g)}">{_esc(g)}</button>'
        for g in workspace_engine.SUGGESTED_REQUESTS
    )
    return (
        '<form method="post" action="/workspace/generate" id="promptForm" class="prompt-form">'
        '<label class="prompt-label" for="goalInput">What should this workspace help you accomplish?</label>'
        f'<textarea id="goalInput" name="goal" rows="3">{_esc(draft["goal"])}</textarea>'
        f'<input type="hidden" name="role" value="{_esc(draft["role"])}">'
        '<button type="submit" class="btn-primary">Generate workspace</button>'
        '</form>'
        '<p class="tab-section-label">Suggested requests</p>'
        f'<div class="suggested-requests">{suggestions}</div>'
    )


def _primitives_tab_html(workspace):
    used_types = {p["type"] for p in workspace["primitives"]}
    rows = []
    for t in workspace_primitives.PRIMITIVE_TYPES:
        meta = workspace_primitives.PALETTE[t]
        used_cls = " primitive-used" if t in used_types else ""
        used_tag = '<span class="primitive-used-tag">in use</span>' if t in used_types else ""
        rows.append(
            f'<div class="primitive-entry{used_cls}"><span class="primitive-icon">{meta["icon"]}</span>'
            f'<div><p class="primitive-name">{_esc(t)}{used_tag}</p>'
            f'<p class="primitive-desc">{_esc(meta["description"])}</p></div></div>'
        )
    return (
        '<p class="tab-section-label">Registered Primitives</p>'
        '<p class="tab-help">Every generated workspace is assembled only from this trusted, typed set.</p>'
        f'<div class="primitive-list">{"".join(rows)}</div>'
    )


def _data_tab_html(workspace):
    return (
        '<p class="tab-section-label">Connected systems</p>'
        f'{_connected_apps_html(workspace)}'
        '<p class="tab-help">Salesforce and Jira are adapter-based mock connectors — clearly labeled wherever '
        'their data appears — standing in for real integrations that would plug into the same data layer.</p>'
        '<p class="tab-help"><a href="/integrations">Manage real connectors (Gmail, Slack, GitHub, Helpdesk) →</a></p>'
        '<p class="tab-help"><a href="/studio">Looking for the chat-based Studio or Composer? Open classic Studio →</a></p>'
    )


def _rules_tab_html(role_key):
    role = roles.get_role(role_key)
    types_html = "".join(f'<span class="rule-chip">{_esc(t)}</span>' for t in role["visible_types"])
    actions_html = "".join(f'<span class="rule-chip">{_esc(roles.ACTION_LABELS.get(a, a))}</span>' for a in role["allowed_actions"])
    approve_html = "Yes — this role can approve or reject pending requests." if role["can_approve"] else "No — this role can request approval but not decide it."
    return (
        f'<p class="tab-section-label">Permissions for {_esc(role["label"])}</p>'
        f'<p class="rule-row"><span class="rule-label">Data scope</span> {_esc(role["scope"])}</p>'
        f'<p class="rule-row"><span class="rule-label">Detail level</span> {_esc(role["detail_level"])}</p>'
        f'<p class="rule-row"><span class="rule-label">Visible primitives</span></p><div class="rule-chips">{types_html}</div>'
        f'<p class="rule-row"><span class="rule-label">Available actions</span></p><div class="rule-chips">{actions_html}</div>'
        f'<p class="rule-row"><span class="rule-label">Approval authority</span> {_esc(approve_html)}</p>'
    )


def _history_tab_html(username, draft):
    saved = workspace_store.list_workspaces(username)
    if not saved:
        rows_html = '<p class="tab-help">No saved workspaces yet — use Save in the top bar to keep this one.</p>'
    else:
        rows = []
        for w in saved:
            is_current = w["id"] == draft["id"]
            versions_html = ""
            if is_current:
                v_rows = []
                for v in w["versions"]:
                    is_cv = v["version"] == w["current_version"]
                    restore_btn = "" if is_cv else (
                        f'<form method="post" action="/workspace/{_esc(w["id"])}/restore/{v["version"]}" class="inline-form">'
                        f'<button type="submit" class="btn-tertiary">Restore</button></form>'
                    )
                    cv_tag = ' <span class="version-current-tag">current</span>' if is_cv else ""
                    v_rows.append(
                        f'<li class="version-row"><span>v{v["version"]} — {_esc(v["title"])}{cv_tag}</span>'
                        f'<span class="version-date">{_esc(v["created_at"])}</span>{restore_btn}</li>'
                    )
                versions_html = f'<ul class="version-list">{"".join(v_rows)}</ul>'
            open_link = "" if is_current else f'<a class="btn-tertiary" href="/workspace/open/{_esc(w["id"])}">Open</a>'
            rows.append(
                f'<div class="history-entry{" history-entry-current" if is_current else ""}">'
                f'<form method="post" action="/workspace/{_esc(w["id"])}/rename" class="rename-form">'
                f'<input type="text" name="name" value="{_esc(w["name"])}">'
                f'<button type="submit" class="btn-tertiary">Rename</button></form>'
                f'{open_link}{versions_html}'
                '</div>'
            )
        rows_html = "".join(rows)
    return f'<p class="tab-section-label">Saved workspaces</p>{rows_html}'


def _left_panel_html(username, workspace, draft, active_tab, collapsed=False):
    tabs = [("prompt", "Prompt"), ("primitives", "Primitives"), ("data", "Data"), ("rules", "Rules"), ("history", "History")]
    tab_buttons = "".join(
        f'<button type="button" class="ws-tab{" active" if key == active_tab else ""}" data-tab="{key}">{label}</button>'
        for key, label in tabs
    )
    panels = {
        "prompt": _prompt_tab_html(draft),
        "primitives": _primitives_tab_html(workspace),
        "data": _data_tab_html(workspace),
        "rules": _rules_tab_html(draft["role"]),
        "history": _history_tab_html(username, draft),
    }
    panels_html = "".join(
        f'<div class="ws-tab-panel{" active" if key == active_tab else ""}" id="tab-{key}">{html}</div>'
        for key, html in panels.items()
    )
    cls = "ws-left collapsed" if collapsed else "ws-left"
    return (
        f'<aside class="{cls}" id="wsLeft"><div class="ws-tabs">{tab_buttons}</div>'
        f'<div class="ws-tab-panels">{panels_html}</div></aside>'
    )


# --- Right inspector (populated client-side from data-config) --------------

_INSPECTOR_HTML = """
<aside class="ws-inspector" id="wsInspector">
  <div class="inspector-head">
    <p class="inspector-eyebrow">Inspector</p>
    <button type="button" id="wsInspectorClose" class="inspector-close" aria-label="Close inspector">&times;</button>
  </div>
  <h3 id="insTitle" class="inspector-title"></h3>
  <dl class="inspector-fields">
    <dt>Primitive type</dt><dd id="insType"></dd>
    <dt>Data sources</dt><dd id="insSources"></dd>
    <dt>Permitted roles</dt><dd id="insRoles"></dd>
    <dt>Available actions</dt><dd id="insActions"></dd>
    <dt>Approval requirement</dt><dd id="insApproval"></dd>
    <dt>Appearance</dt><dd id="insTone"></dd>
    <dt>Position / size</dt><dd id="insSpan"></dd>
  </dl>
</aside>
"""


# --- Top nav -----------------------------------------------------------

def _role_options(current_role):
    opts = "".join(
        f'<option value="{k}"{" selected" if k == current_role else ""}>{_esc(roles.get_role(k)["label"])}</option>'
        for k in roles.ROLE_ORDER
    )
    return opts


def _topbar_html(user, workspace, draft):
    name = draft.get("name") or workspace["title"]
    published_tag = ' <span class="published-tag">Published</span>' if draft.get("published") else ""
    return (
        '<header class="ws-topbar">'
        '<div class="ws-topbar-left">'
        '<span class="ws-logo">Pilant</span>'
        f'<span class="ws-workspace-name">{_esc(name)}{published_tag}</span>'
        '</div>'
        '<div class="ws-topbar-right">'
        '<button type="button" class="nav-link" data-open-tab="data">Connected apps</button>'
        '<button type="button" class="nav-link" data-open-tab="rules">Permissions</button>'
        '<form method="post" action="/workspace/role" class="role-switcher-form">'
        f'<select name="role" class="role-switcher" onchange="this.form.submit()">{_role_options(draft["role"])}</select>'
        '</form>'
        '<form method="post" action="/workspace/save" class="inline-form">'
        f'<input type="text" name="name" value="{_esc(name)}" class="save-name-input" placeholder="Workspace name">'
        '<button type="submit" class="btn-secondary">Save</button></form>'
        '<form method="post" action="/workspace/publish" class="inline-form">'
        '<button type="submit" class="btn-primary">Publish</button></form>'
        '</div></header>'
    )


# --- Canvas --------------------------------------------------------------

def _canvas_html(user, workspace, draft, role):
    prims_html = "".join(
        workspace_primitives.render_primitive(
            {**p, "visible_roles": roles.roles_that_can_view(p["type"])},
            workspace["id"], role_key=draft["role"], can_approve=role["can_approve"],
        )
        for p in workspace["primitives"]
    )
    stages_html = "".join(f'<div class="gen-stage" data-stage="{i}">{_esc(s)}</div>' for i, s in enumerate(workspace_engine.GENERATION_STAGES))
    return (
        '<main class="ws-canvas" id="wsCanvas">'
        f'<div class="gen-sequence" id="genSequence">{stages_html}</div>'
        '<div class="canvas-header">'
        f'<h1 class="canvas-title">{_esc(workspace["title"])}</h1>'
        f'<p class="canvas-subtitle">{_esc(workspace.get("subtitle") or "")}</p>'
        f'<p class="canvas-adapted">Adapted for {_esc(user["name"])} &middot; {_esc(role["label"])}</p>'
        '<p class="canvas-adapted-sub">Based on role, permissions, and current objective.</p>'
        '<a class="secondary-link" href="/inbox?folder=inbox">Open full Gmail inbox &rarr;</a>'
        '</div>'
        f'<div class="ws-grid">{prims_html}</div>'
        '</main>'
    )


PAGE_HEAD = """<title>Pilant Studio — Workspace</title>
<style>
:root {
  --ground:#0B1220; --surface:#141C30; --card:#182240; --card-hover:#1C2748; --border:#2A3552;
  --text:#EDEFF5; --text-muted:#8791A8; --accent:#6C7CFF; --accent-soft:rgba(108,124,255,.14);
  --critical:#E8615A; --critical-soft:rgba(232,97,90,.14); --warning:#F0B429; --warning-soft:rgba(240,180,41,.14);
  --success:#5FCE9A; --success-soft:rgba(95,206,154,.14);
}
* { box-sizing:border-box; }
html { background:var(--ground); }
body { margin:0; background:var(--ground); color:var(--text); font-family:'IBM Plex Sans',system-ui,sans-serif; }
a { color:inherit; }
button, input, select, textarea { font-family:inherit; }
:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
.ws-shell { display:flex; flex-direction:column; height:100vh; overflow:hidden; }

/* -- top bar -- */
.ws-topbar { display:flex; align-items:center; justify-content:space-between; padding:0 20px; height:56px; flex:none; border-bottom:1px solid var(--border); background:var(--surface); }
.ws-topbar-left { display:flex; align-items:center; gap:14px; min-width:0; }
.ws-logo { font-family:'Sora',sans-serif; font-weight:700; font-size:1.05rem; letter-spacing:.01em; }
.ws-workspace-name { font-size:.85rem; color:var(--text-muted); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width:32vw; }
.published-tag { background:var(--accent-soft); color:var(--accent); border-radius:4px; padding:2px 7px; font-size:.66rem; font-family:'IBM Plex Mono',monospace; text-transform:uppercase; letter-spacing:.04em; }
.ws-topbar-right { display:flex; align-items:center; gap:8px; }
.nav-link { background:none; border:none; color:var(--text-muted); font-size:.8rem; padding:7px 10px; border-radius:6px; cursor:pointer; }
.nav-link:hover { color:var(--text); background:rgba(255,255,255,.04); }
.role-switcher-form { margin:0; }
.role-switcher { background:var(--card); border:1px solid var(--border); color:var(--text); border-radius:7px; padding:7px 10px; font-size:.8rem; }
.save-name-input { background:var(--card); border:1px solid var(--border); color:var(--text); border-radius:7px 0 0 7px; padding:7px 10px; font-size:.8rem; width:150px; border-right:none; }
.ws-topbar-right form.inline-form { display:flex; margin:0; }
.ws-topbar-right form.inline-form .save-name-input + button { border-radius:0 7px 7px 0; }
.btn-primary, .btn-secondary, .btn-tertiary { border:none; border-radius:7px; padding:8px 14px; font-size:.8rem; font-weight:600; cursor:pointer; }
.btn-primary { background:var(--accent); color:#fff; }
.btn-primary:hover { opacity:.9; }
.btn-secondary { background:var(--card); color:var(--text); border:1px solid var(--border); border-radius:0 7px 7px 0; }
.btn-secondary:hover { border-color:var(--accent); }
.btn-tertiary { background:transparent; color:var(--accent); border:1px solid var(--border); padding:5px 10px; font-size:.74rem; }
.btn-tertiary:hover { border-color:var(--accent); }

/* -- body layout -- */
.ws-body { display:flex; flex:1; min-height:0; position:relative; }
.ws-left { width:340px; flex:none; border-right:1px solid var(--border); background:var(--surface); overflow-y:auto; transition:margin-left .18s ease, opacity .18s ease; }
.ws-left.collapsed { margin-left:-340px; opacity:0; pointer-events:none; }
.ws-tabs { display:flex; border-bottom:1px solid var(--border); position:sticky; top:0; background:var(--surface); z-index:2; }
.ws-tab { flex:1; background:none; border:none; color:var(--text-muted); font-size:.72rem; padding:12px 4px; cursor:pointer; border-bottom:2px solid transparent; font-family:'IBM Plex Mono',monospace; letter-spacing:.02em; text-transform:uppercase; }
.ws-tab:hover { color:var(--text); }
.ws-tab.active { color:var(--accent); border-bottom-color:var(--accent); }
.ws-tab-panel { display:none; padding:18px 18px 28px; }
.ws-tab-panel.active { display:block; }
.tab-section-label { font-family:'IBM Plex Mono',monospace; font-size:.66rem; letter-spacing:.06em; text-transform:uppercase; color:var(--text-muted); margin:0 0 10px; }
.tab-help { font-size:.76rem; color:var(--text-muted); line-height:1.5; margin:10px 0; }
.tab-help a { color:var(--accent); text-decoration:none; }
.tab-help a:hover { text-decoration:underline; }

.panel-toggle { position:absolute; left:340px; top:14px; transform:translateX(-50%); width:26px; height:26px; border-radius:50%; background:var(--card); border:1px solid var(--border); color:var(--text-muted); cursor:pointer; z-index:3; transition:left .18s ease; display:flex; align-items:center; justify-content:center; font-size:.8rem; }
.ws-left.collapsed ~ .panel-toggle, .panel-toggle.collapsed { left:0; }
.panel-toggle:hover { color:var(--accent); border-color:var(--accent); }

/* -- prompt tab -- */
.prompt-form { display:flex; flex-direction:column; gap:10px; margin-bottom:20px; }
.prompt-label { font-size:.82rem; font-weight:600; }
.prompt-form textarea { background:var(--card); border:1px solid var(--border); border-radius:8px; color:var(--text); padding:10px 12px; font-size:.85rem; resize:vertical; }
.prompt-form textarea:focus { outline:none; border-color:var(--accent); }
.suggested-requests { display:flex; flex-direction:column; gap:6px; }
.suggested-request { text-align:left; background:var(--card); border:1px solid var(--border); color:var(--text); border-radius:7px; padding:9px 12px; font-size:.78rem; cursor:pointer; }
.suggested-request:hover { border-color:var(--accent); background:var(--card-hover); }

/* -- primitives tab -- */
.primitive-list { display:flex; flex-direction:column; gap:10px; }
.primitive-entry { display:flex; gap:10px; padding:8px; border-radius:8px; }
.primitive-entry.primitive-used { background:var(--accent-soft); }
.primitive-icon { flex:none; width:26px; height:26px; display:flex; align-items:center; justify-content:center; background:var(--card); border-radius:6px; font-size:.85rem; }
.primitive-name { font-size:.8rem; font-weight:600; margin:0 0 2px; }
.primitive-used-tag { font-family:'IBM Plex Mono',monospace; font-size:.6rem; color:var(--accent); margin-left:6px; text-transform:uppercase; }
.primitive-desc { font-size:.72rem; color:var(--text-muted); margin:0; line-height:1.4; }

/* -- data tab -- */
.app-list { list-style:none; margin:0 0 14px; padding:0; display:flex; flex-direction:column; gap:6px; }
.app-row { display:flex; justify-content:space-between; align-items:center; background:var(--card); border-radius:7px; padding:9px 12px; font-size:.8rem; }
.app-status { font-size:.68rem; font-family:'IBM Plex Mono',monospace; padding:2px 7px; border-radius:4px; }
.app-status-live { color:var(--success); background:var(--success-soft); }
.app-status-mock { color:var(--warning); background:var(--warning-soft); }
.app-status-idle { color:var(--text-muted); background:rgba(255,255,255,.04); }

/* -- rules tab -- */
.rule-row { font-size:.8rem; margin:10px 0 4px; }
.rule-label { color:var(--text-muted); margin-right:6px; }
.rule-chips { display:flex; flex-wrap:wrap; gap:5px; margin-bottom:4px; }
.rule-chip { background:var(--card); border:1px solid var(--border); border-radius:5px; padding:3px 8px; font-size:.68rem; font-family:'IBM Plex Mono',monospace; }

/* -- history tab -- */
.history-entry { background:var(--card); border-radius:8px; padding:12px; margin-bottom:10px; border:1px solid var(--border); }
.history-entry-current { border-color:var(--accent); }
.rename-form { display:flex; gap:6px; margin:0 0 8px; }
.rename-form input { flex:1; background:var(--surface); border:1px solid var(--border); color:var(--text); border-radius:6px; padding:6px 8px; font-size:.78rem; }
.version-list { list-style:none; margin:8px 0 0; padding:0; display:flex; flex-direction:column; gap:5px; }
.version-row { display:flex; justify-content:space-between; align-items:center; font-size:.72rem; color:var(--text-muted); gap:6px; }
.version-current-tag { color:var(--accent); font-family:'IBM Plex Mono',monospace; font-size:.62rem; }
.version-date { font-family:'IBM Plex Mono',monospace; font-size:.62rem; }

/* -- canvas -- */
.ws-canvas { flex:1; min-width:0; overflow-y:auto; padding:24px 32px 60px; transition:opacity .25s ease; }
.ws-canvas.gen-hidden { opacity:0; pointer-events:none; }
.ws-canvas.gen-revealed { animation:canvasIn .4s ease; }
@keyframes canvasIn { from { opacity:0; transform:translateY(6px); } to { opacity:1; transform:translateY(0); } }
.canvas-header { margin-bottom:22px; }
.canvas-title { font-family:'Sora',sans-serif; font-weight:700; font-size:1.5rem; margin:0 0 4px; text-wrap:balance; }
.canvas-subtitle { color:var(--text-muted); font-size:.9rem; margin:0 0 10px; }
.canvas-adapted { font-size:.8rem; color:var(--accent); margin:0; font-weight:600; }
.canvas-adapted-sub { font-size:.72rem; color:var(--text-muted); margin:2px 0 10px; }
.secondary-link { font-size:.78rem; color:var(--text-muted); text-decoration:none; border-bottom:1px dotted var(--border); }
.secondary-link:hover { color:var(--accent); border-color:var(--accent); }

/* -- generation sequence -- */
.gen-sequence { display:none; flex-direction:column; gap:8px; max-width:420px; margin:40px auto; }
.gen-sequence.active { display:flex; }
.gen-stage { font-size:.85rem; color:var(--text-muted); padding:10px 14px; border-radius:8px; border:1px solid var(--border); opacity:.4; transition:opacity .25s ease, color .25s ease, border-color .25s ease; }
.gen-stage.current { opacity:1; color:var(--text); border-color:var(--accent); background:var(--accent-soft); }
.gen-stage.done { opacity:.75; color:var(--text); }
@media (prefers-reduced-motion: reduce) { .gen-stage, .ws-canvas, .ws-left { transition:none; } }

/* -- primitive grid + cards -- */
.ws-grid { display:grid; grid-template-columns:repeat(6, 1fr); gap:16px; }
.prim { grid-column:1 / -1; background:var(--card); border:1px solid var(--border); border-left:3px solid var(--border); border-radius:10px; padding:16px 18px; cursor:pointer; }
.prim:hover { border-color:var(--accent); }
.prim.selected { border-color:var(--accent); border-left-color:var(--accent); box-shadow:0 0 0 1px var(--accent); }
.prim.tone-critical { border-left-color:var(--critical); }
.prim.tone-warning { border-left-color:var(--warning); }
.prim.tone-good { border-left-color:var(--success); }
.prim.tone-accent { border-left-color:var(--accent); }
.span-half { grid-column:span 3; }
.span-third { grid-column:span 2; }
@media (max-width:900px) { .span-half, .span-third { grid-column:1 / -1; } }
.prim-head { display:flex; justify-content:space-between; align-items:flex-start; gap:10px; margin-bottom:6px; }
.prim-title { font-size:.92rem; font-weight:700; margin:0; }
.prim-subtitle { font-size:.76rem; color:var(--text-muted); margin:0 0 10px; }
.prim-empty { font-size:.8rem; color:var(--text-muted); margin:0; }
.src-badges { display:flex; gap:4px; flex-wrap:wrap; flex:none; }
.src-badge { font-family:'IBM Plex Mono',monospace; font-size:.62rem; text-transform:uppercase; letter-spacing:.03em; background:rgba(255,255,255,.05); border:1px solid var(--border); color:var(--text-muted); padding:2px 6px; border-radius:4px; }
.src-badge-mock { color:var(--warning); border-color:rgba(240,180,41,.35); }

/* metric */
.metric-value { font-family:'Sora',sans-serif; font-weight:700; font-size:2rem; line-height:1; margin-bottom:4px; font-variant-numeric:tabular-nums; }
.metric-label { font-size:.78rem; color:var(--text-muted); }
.metric-trend { display:block; font-size:.7rem; color:var(--text-muted); margin-top:4px; }

/* priority list */
.priority-list { list-style:none; margin:0; padding:0; display:flex; flex-direction:column; gap:14px; }
.priority-item { border-top:1px solid var(--border); padding-top:14px; }
.priority-item:first-child { border-top:none; padding-top:0; }
.priority-item-head { display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin-bottom:4px; }
.priority-flag { font-family:'IBM Plex Mono',monospace; font-size:.62rem; text-transform:uppercase; padding:2px 7px; border-radius:4px; }
.priority-flag-critical { background:var(--critical-soft); color:var(--critical); }
.priority-flag-high { background:var(--warning-soft); color:var(--warning); }
.priority-flag-medium { background:rgba(255,255,255,.06); color:var(--text-muted); }
.priority-item-title { font-weight:700; font-size:.92rem; }
.priority-meta { display:flex; gap:12px; font-size:.72rem; color:var(--text-muted); margin-bottom:6px; }
.priority-why { list-style:none; margin:0 0 8px; padding:0; display:flex; flex-direction:column; gap:3px; }
.priority-why li { font-size:.78rem; }
.why-source { color:var(--text-muted); font-family:'IBM Plex Mono',monospace; font-size:.7rem; }
.priority-info { font-size:.78rem; color:var(--text-muted); margin:0 0 6px; }
.priority-recommend { font-size:.78rem; margin:0 0 8px; }
.recommend-label { color:var(--accent); font-weight:600; }

/* actions */
.prim-actions { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin-top:6px; }
.prim-action-form { margin:0; }
.prim-action, a.prim-action { background:var(--surface); border:1px solid var(--border); color:var(--text); border-radius:6px; padding:6px 11px; font-size:.72rem; cursor:pointer; text-decoration:none; display:inline-block; }
.prim-action:hover { border-color:var(--accent); color:var(--accent); }
.prim-action-approval { border-color:rgba(240,180,41,.4); }
.approval-marker { font-family:'IBM Plex Mono',monospace; font-size:.62rem; color:var(--warning); text-transform:uppercase; letter-spacing:.03em; }
.approve-btn { border-color:rgba(95,206,154,.4); color:var(--success); }
.reject-btn { border-color:rgba(232,97,90,.4); color:var(--critical); }

/* data table */
.table-scroll { overflow-x:auto; }
.data-table { width:100%; border-collapse:collapse; font-size:.78rem; }
.data-table th { text-align:left; color:var(--text-muted); font-weight:600; font-size:.68rem; text-transform:uppercase; letter-spacing:.03em; padding:6px 10px; border-bottom:1px solid var(--border); white-space:nowrap; }
.data-table td { padding:8px 10px; border-bottom:1px solid var(--border); font-variant-numeric:tabular-nums; }
.data-table tr:last-child td { border-bottom:none; }

/* customer card */
.customer-summary { font-size:.82rem; margin:0 0 12px; }
.fact-grid { display:grid; grid-template-columns:1fr 1fr; gap:8px 16px; margin-bottom:10px; }
.fact { display:flex; flex-direction:column; }
.fact-label { font-size:.66rem; color:var(--text-muted); text-transform:uppercase; letter-spacing:.03em; }
.fact-value { font-size:.85rem; font-weight:600; }

/* message card */
.message-list { list-style:none; margin:0; padding:0; display:flex; flex-direction:column; gap:12px; max-height:340px; overflow-y:auto; }
.message-row { border-top:1px solid var(--border); padding-top:10px; }
.message-row:first-child { border-top:none; padding-top:0; }
.message-row-head { display:flex; justify-content:space-between; font-size:.72rem; color:var(--text-muted); margin-bottom:2px; }
.message-subject { font-weight:600; font-size:.82rem; }
.message-snippet { font-size:.76rem; color:var(--text-muted); margin:2px 0 4px; }
.message-open { font-size:.72rem; color:var(--accent); text-decoration:none; }
.message-open:hover { text-decoration:underline; }

/* timeline */
.timeline { list-style:none; margin:0; padding:0; display:flex; flex-direction:column; gap:10px; }
.timeline-event { display:flex; gap:10px; align-items:baseline; font-size:.78rem; }
.timeline-date { font-family:'IBM Plex Mono',monospace; font-size:.68rem; color:var(--text-muted); flex:none; width:70px; }

/* task queue */
.task-list { list-style:none; margin:0; padding:0; display:flex; flex-direction:column; gap:8px; }
.task-row { display:flex; justify-content:space-between; align-items:center; gap:8px; font-size:.8rem; padding:6px 0; border-top:1px solid var(--border); }
.task-row:first-child { border-top:none; }
.task-overdue .task-title { color:var(--critical); }
.task-owner, .task-due { font-size:.7rem; color:var(--text-muted); }

/* alert */
.alert-message, .alert-list { font-size:.82rem; margin:0; }
.alert-list { list-style:disc; padding-left:18px; display:flex; flex-direction:column; gap:6px; }

/* chart */
.chart { display:flex; flex-direction:column; gap:10px; }
.chart-row { display:grid; grid-template-columns:90px 1fr 70px; align-items:center; gap:8px; font-size:.76rem; }
.chart-bar-track { background:var(--surface); border-radius:5px; height:10px; overflow:hidden; }
.chart-bar { background:var(--accent); height:100%; border-radius:5px; }
.chart-row-value { text-align:right; font-variant-numeric:tabular-nums; color:var(--text-muted); }

/* form */
.prim-form { display:flex; flex-direction:column; gap:8px; }
.form-field-label { font-size:.78rem; font-weight:600; }
.prim-form textarea { background:var(--surface); border:1px solid var(--border); color:var(--text); border-radius:7px; padding:8px 10px; font-size:.8rem; }

/* approval panel */
.approval-status { font-family:'IBM Plex Mono',monospace; font-size:.68rem; text-transform:uppercase; padding:3px 9px; border-radius:5px; }
.approval-status-pending { background:var(--warning-soft); color:var(--warning); }
.approval-status-approved { background:var(--success-soft); color:var(--success); }
.approval-status-rejected { background:var(--critical-soft); color:var(--critical); }
.approval-desc { font-size:.8rem; margin:10px 0; }
.approval-meta { font-size:.74rem; color:var(--text-muted); margin:0; }

/* activity log */
.activity-log { list-style:none; margin:0; padding:0; display:flex; flex-direction:column; gap:6px; max-height:260px; overflow-y:auto; }
.activity-row { display:flex; gap:10px; font-size:.74rem; color:var(--text-muted); font-family:'IBM Plex Mono',monospace; }
.activity-event { color:var(--text); }

/* -- inspector -- */
.ws-inspector { width:0; flex:none; border-left:1px solid var(--border); background:var(--surface); overflow:hidden; opacity:0; transition:width .18s ease, opacity .18s ease; }
.ws-inspector.open { width:300px; opacity:1; overflow-y:auto; }
.inspector-head { display:flex; justify-content:space-between; align-items:center; padding:16px 18px 0; }
.inspector-eyebrow { font-family:'IBM Plex Mono',monospace; font-size:.66rem; text-transform:uppercase; letter-spacing:.06em; color:var(--text-muted); margin:0; }
.inspector-close { background:none; border:none; color:var(--text-muted); font-size:1.2rem; cursor:pointer; line-height:1; padding:4px; }
.inspector-close:hover { color:var(--text); }
.inspector-title { font-size:1rem; margin:8px 18px 12px; }
.inspector-fields { margin:0; padding:0 18px 24px; }
.inspector-fields dt { font-size:.66rem; text-transform:uppercase; letter-spacing:.03em; color:var(--text-muted); margin-top:14px; }
.inspector-fields dd { margin:4px 0 0; font-size:.8rem; }

/* flash */
.flash { font-family:'IBM Plex Mono',monospace; font-size:.78rem; padding:9px 14px; border-radius:8px; margin:0 32px 14px; }
.flash-ok { background:var(--success-soft); color:var(--success); }
.flash-err { background:var(--critical-soft); color:var(--critical); }

@media (max-width:760px) {
  .ws-left { position:absolute; z-index:5; height:100%; }
  .ws-inspector.open { position:absolute; right:0; z-index:5; height:100%; }
  .ws-topbar-right { flex-wrap:wrap; gap:6px; }
  .save-name-input { width:90px; }
}
</style>"""

PAGE_SCRIPT = """
<script>
(function(){
  var canvas = document.getElementById('wsCanvas');
  var inspector = document.getElementById('wsInspector');
  function closeInspector(){
    inspector.classList.remove('open');
    document.querySelectorAll('.prim.selected').forEach(function(el){ el.classList.remove('selected'); });
  }
  document.getElementById('wsInspectorClose').addEventListener('click', closeInspector);
  function openInspector(card){
    var cfg;
    try { cfg = JSON.parse(card.getAttribute('data-config')); } catch(e){ return; }
    document.querySelectorAll('.prim.selected').forEach(function(el){ el.classList.remove('selected'); });
    card.classList.add('selected');
    document.getElementById('insTitle').textContent = cfg.title || '';
    document.getElementById('insType').textContent = cfg.type || '';
    document.getElementById('insSources').textContent = (cfg.data_sources||[]).map(function(s){ return s.name + (s.mock ? ' (mock)' : ' (live)'); }).join(', ') || 'None';
    document.getElementById('insRoles').textContent = (cfg.visible_roles||[]).join(', ') || 'All roles';
    document.getElementById('insActions').textContent = (cfg.allowed_actions||[]).map(function(a){ return a.label + (a.approval_required ? ' (needs approval)' : ''); }).join(', ') || 'None';
    document.getElementById('insApproval').textContent = cfg.approval_required ? 'Required' : 'Not required';
    document.getElementById('insTone').textContent = cfg.tone || 'neutral';
    document.getElementById('insSpan').textContent = cfg.span || 'full';
    inspector.classList.add('open');
  }
  canvas.addEventListener('click', function(e){
    if (e.target.closest('form, a, button, textarea, input, label')) return;
    var card = e.target.closest('.prim');
    if (card) openInspector(card);
  });
  canvas.addEventListener('keydown', function(e){
    if ((e.key === 'Enter' || e.key === ' ') && e.target.classList.contains('prim')) {
      e.preventDefault(); openInspector(e.target);
    }
  });

  document.querySelectorAll('.ws-tab').forEach(function(btn){
    btn.addEventListener('click', function(){ activateTab(btn.dataset.tab); });
  });
  document.querySelectorAll('[data-open-tab]').forEach(function(btn){
    btn.addEventListener('click', function(){
      var left = document.getElementById('wsLeft');
      if (left.classList.contains('collapsed')) { document.getElementById('wsPanelToggleForm').submit(); return; }
      activateTab(btn.getAttribute('data-open-tab'));
    });
  });
  function activateTab(tab){
    document.querySelectorAll('.ws-tab').forEach(function(b){ b.classList.toggle('active', b.dataset.tab === tab); });
    document.querySelectorAll('.ws-tab-panel').forEach(function(p){ p.classList.toggle('active', p.id === 'tab-' + tab); });
  }

  document.querySelectorAll('.suggested-request').forEach(function(btn){
    btn.addEventListener('click', function(){
      document.getElementById('goalInput').value = btn.getAttribute('data-goal');
      document.getElementById('promptForm').submit();
    });
  });

  var params = new URLSearchParams(window.location.search);
  if (params.get('generated') === '1') {
    var seq = document.getElementById('genSequence');
    var stages = Array.prototype.slice.call(seq.querySelectorAll('.gen-stage'));
    canvas.classList.add('gen-hidden');
    seq.classList.add('active');
    var i = 0;
    function next(){
      if (i > 0) stages[i-1].classList.remove('current');
      if (i >= stages.length) {
        seq.classList.remove('active');
        canvas.classList.remove('gen-hidden');
        canvas.classList.add('gen-revealed');
        return;
      }
      stages[i].classList.add('current');
      stages[i].classList.add('done');
      i++;
      setTimeout(next, 360);
    }
    next();
    if (window.history.replaceState) window.history.replaceState({}, '', window.location.pathname);
  }
})();
</script>"""


def _render_page(user, workspace, draft, active_tab="prompt"):
    role = roles.get_role(draft["role"])
    collapsed = bool(session.get("ws_left_collapsed"))
    toggle_cls = "panel-toggle collapsed" if collapsed else "panel-toggle"
    body = (
        f'<div class="ws-shell">{_topbar_html(user, workspace, draft)}'
        f'{_flash_html()}'
        '<div class="ws-body">'
        f'{_left_panel_html(user["username"], workspace, draft, active_tab, collapsed=collapsed)}'
        '<form method="post" action="/workspace/panel/toggle" id="wsPanelToggleForm" style="display:none"></form>'
        f'<button type="button" class="{toggle_cls}" onclick="document.getElementById(\'wsPanelToggleForm\').submit()" aria-label="Toggle build panel">&#8942;</button>'
        f'{_canvas_html(user, workspace, draft, role)}'
        f'{_INSPECTOR_HTML}'
        '</div></div>'
    )
    return f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">{PAGE_HEAD}</head><body class="studio-body">{body}{PAGE_SCRIPT}</body></html>'


# --- routes ---------------------------------------------------------------

@workspace_bp.route("/workspace")
@login_required
def workspace_home():
    user = get_user(session["username"])
    draft = _current_draft()
    workspace = workspace_engine.generate_workspace(draft["id"], draft["goal"], draft["role"], user)
    active_tab = request.args.get("tab") if request.args.get("tab") in ("prompt", "primitives", "data", "rules", "history") else "prompt"
    return _render_page(user, workspace, draft, active_tab=active_tab)


@workspace_bp.route("/workspace/generate", methods=["POST"])
@login_required
def workspace_generate():
    goal = (request.form.get("goal") or "").strip() or workspace_engine.DEFAULT_GOAL
    role = request.form.get("role") or roles.DEFAULT_ROLE
    draft = _current_draft()
    draft["id"] = _new_workspace_id()
    draft["goal"] = goal
    draft["role"] = role if roles.is_valid_role(role) else roles.DEFAULT_ROLE
    draft["saved"] = False
    draft["published"] = False
    draft["name"] = None
    _save_draft(draft)
    session["ws_left_collapsed"] = True
    return redirect(url_for("workspace.workspace_home") + "?generated=1")


@workspace_bp.route("/workspace/role", methods=["POST"])
@login_required
def workspace_role():
    role = request.form.get("role") or roles.DEFAULT_ROLE
    draft = _current_draft()
    draft["role"] = role if roles.is_valid_role(role) else roles.DEFAULT_ROLE
    _save_draft(draft)
    return redirect(url_for("workspace.workspace_home"))


@workspace_bp.route("/workspace/panel/toggle", methods=["POST"])
@login_required
def workspace_panel_toggle():
    session["ws_left_collapsed"] = not session.get("ws_left_collapsed")
    return redirect(url_for("workspace.workspace_home"))


@workspace_bp.route("/workspace/save", methods=["POST"])
@login_required
def workspace_save():
    user = get_user(session["username"])
    draft = _current_draft()
    name = (request.form.get("name") or "").strip()
    workspace = workspace_engine.generate_workspace(draft["id"], draft["goal"], draft["role"], user)
    workspace_store.save_workspace(user["username"], draft["id"], name, draft["goal"], draft["role"], workspace["title"])
    draft["saved"] = True
    draft["name"] = name or workspace["title"]
    _save_draft(draft)
    audit_log.record("workspace_saved", user["username"], workspace_id=draft["id"], details={"name": draft["name"]})
    session["flash_ok"] = "Workspace saved."
    return redirect(url_for("workspace.workspace_home"))


@workspace_bp.route("/workspace/publish", methods=["POST"])
@login_required
def workspace_publish():
    draft = _current_draft()
    if not draft.get("saved"):
        session["flash_err"] = "Save the workspace before publishing it."
        return redirect(url_for("workspace.workspace_home"))
    draft["published"] = True
    _save_draft(draft)
    audit_log.record("workspace_published", session["username"], workspace_id=draft["id"])
    session["flash_ok"] = "Workspace published."
    return redirect(url_for("workspace.workspace_home"))


@workspace_bp.route("/workspace/regenerate", methods=["POST"])
@login_required
def workspace_regenerate():
    user = get_user(session["username"])
    draft = _current_draft()
    if draft.get("saved"):
        workspace = workspace_engine.generate_workspace(draft["id"], draft["goal"], draft["role"], user)
        workspace_store.save_workspace(user["username"], draft["id"], draft.get("name"), draft["goal"], draft["role"], workspace["title"])
        audit_log.record("workspace_regenerated", user["username"], workspace_id=draft["id"])
    return redirect(url_for("workspace.workspace_home") + "?generated=1")


@workspace_bp.route("/workspace/open/<workspace_id>")
@login_required
def workspace_open(workspace_id):
    username = session["username"]
    stored = workspace_store.get_workspace(username, workspace_id)
    if not stored:
        session["flash_err"] = "That saved workspace couldn't be found."
        return redirect(url_for("workspace.workspace_home"))
    version = workspace_store.current_version_of(username, workspace_id)
    draft = {
        "id": workspace_id, "goal": version["goal"], "role": version["role"],
        "name": stored["name"], "saved": True, "published": False,
    }
    _save_draft(draft)
    return redirect(url_for("workspace.workspace_home"))


@workspace_bp.route("/workspace/<workspace_id>/restore/<int:version>", methods=["POST"])
@login_required
def workspace_restore(workspace_id, version):
    username = session["username"]
    v = workspace_store.restore_version(username, workspace_id, version)
    if not v:
        session["flash_err"] = "That version couldn't be found."
        return redirect(url_for("workspace.workspace_home"))
    draft = _current_draft()
    if draft["id"] == workspace_id:
        draft["goal"], draft["role"] = v["goal"], v["role"]
        _save_draft(draft)
    audit_log.record("workspace_restored", username, workspace_id=workspace_id, details={"version": version})
    session["flash_ok"] = f"Restored version {version}."
    return redirect(url_for("workspace.workspace_home", tab="history"))


@workspace_bp.route("/workspace/<workspace_id>/rename", methods=["POST"])
@login_required
def workspace_rename(workspace_id):
    username = session["username"]
    name = request.form.get("name") or ""
    if workspace_store.rename_workspace(username, workspace_id, name):
        draft = _current_draft()
        if draft["id"] == workspace_id:
            draft["name"] = name.strip()
            _save_draft(draft)
        session["flash_ok"] = "Workspace renamed."
    return redirect(url_for("workspace.workspace_home", tab="history"))


_LOGGED_ACTION_MESSAGES = {
    "view_account": "Opened account detail.",
    "draft_response": "Draft response started.",
    "assign_follow_up": "Follow-up assigned.",
    "resolve": "Marked resolved.",
}


@workspace_bp.route("/workspace/<workspace_id>/action", methods=["POST"])
@login_required
def workspace_action(workspace_id):
    username = session["username"]
    action_key = request.form.get("action_key") or ""
    primitive_id = request.form.get("primitive_id") or ""
    item_id = request.form.get("item_id") or None
    if action_key == "escalate":
        session["flash_ok"] = "This item requires human approval — see the approval panel below."
    else:
        audit_log.record(
            "action_performed", username, workspace_id=workspace_id,
            details={"action_key": action_key, "primitive_id": primitive_id, "item_id": item_id},
        )
        session["flash_ok"] = _LOGGED_ACTION_MESSAGES.get(action_key, "Action recorded.")
    return redirect(url_for("workspace.workspace_home"))


@workspace_bp.route("/workspace/<workspace_id>/approve/<approval_id>", methods=["POST"])
@login_required
def workspace_approve(workspace_id, approval_id):
    return _decide_approval(workspace_id, approval_id, "approved")


@workspace_bp.route("/workspace/<workspace_id>/reject/<approval_id>", methods=["POST"])
@login_required
def workspace_reject(workspace_id, approval_id):
    return _decide_approval(workspace_id, approval_id, "rejected")


def _decide_approval(workspace_id, approval_id, decision):
    username = session["username"]
    draft = _current_draft()
    role = roles.get_role(draft["role"])
    if not role["can_approve"]:
        session["flash_err"] = f"The {role['label']} role can't decide approvals — switch to a manager-level role."
        return redirect(url_for("workspace.workspace_home"))
    result = approvals.decide(approval_id, decision, username)
    if result is None:
        session["flash_err"] = "That approval was already decided, or doesn't exist."
    else:
        session["flash_ok"] = f"Approval {decision}."
    return redirect(url_for("workspace.workspace_home"))


@workspace_bp.route("/workspace/<workspace_id>/form/<prim_id>", methods=["POST"])
@login_required
def workspace_form(workspace_id, prim_id):
    username = session["username"]
    note = (request.form.get("note") or "").strip()
    if note:
        audit_log.record("workspace_note_added", username, workspace_id=workspace_id, details={"primitive_id": prim_id, "note": note[:500]})
        session["flash_ok"] = "Note saved."
    return redirect(url_for("workspace.workspace_home"))
