"""
workspace_primitives.py — added 2026-08-29 for the Studio workspace
rewrite. This is the TYPED, SCHEMA-DRIVEN component vocabulary the brief
asks for: a fixed, registered set of Primitive types, each with its own
data shape and its own renderer function. workspace_engine.py may only
ever construct instances of the types declared in PRIMITIVE_TYPES below,
with the fields each renderer actually reads — there is no code path from
a generated workspace to arbitrary HTML/JS, unlike a raw template-string
free-for-all.

A "primitive instance" (what workspace_engine.py builds and this module
renders) is a plain dict:

    {
        "id": str,                 # stable within one generation, used by the
                                    # inspector and by action/approval routing
        "type": one of PRIMITIVE_TYPES,
        "title": str,
        "subtitle": str | None,
        "data_sources": [{"name": "Gmail"|"Salesforce"|"Jira"|..., "mock": bool}],
        "visible_roles": [role keys] | None,   # informational; ENFORCEMENT is
                                                # roles.filter_primitives_for_role,
                                                # already applied before this
                                                # module ever sees an instance
        "allowed_actions": [{"key", "label", "approval_required": bool}],
        "approval_required": bool,             # true if the primitive itself
                                                # (not just one action) needs
                                                # sign-off, e.g. ApprovalPanel
        "tone": "critical"|"warning"|"good"|"accent"|"neutral",
        "position": {"span": "full"|"half"|"third"},
        "data": {...},             # type-specific payload, see each _render_*
    }

Every renderer HTML-escapes every string it emits (via _esc) and only
ever reads named fields out of `data` — there is no eval, no format-string
injection of untrusted content into tag/attribute position, and no way
for a workspace's generated content to smuggle in a script tag.
"""

import html as _html
import json

import audit_log

PRIMITIVE_TYPES = [
    "Metric", "PriorityList", "DataTable", "CustomerCard", "MessageCard",
    "Timeline", "TaskQueue", "Alert", "Chart", "Form", "ApprovalPanel",
    "ActionPanel", "ActivityLog",
]

PALETTE = {
    "Metric": {"icon": "▣", "description": "A single headline number with a label — counts, totals, sums."},
    "PriorityList": {"icon": "☰", "description": "Ranked items needing attention, each with reasons, sources, and actions."},
    "DataTable": {"icon": "▦", "description": "Rows/columns of structured records from one or more sources."},
    "CustomerCard": {"icon": "◈", "description": "One account's summary: stage, risk, key facts, sources."},
    "MessageCard": {"icon": "✉", "description": "Recent message previews from a connected inbox."},
    "Timeline": {"icon": "⟿", "description": "Chronological events across sources for one account or topic."},
    "TaskQueue": {"icon": "☑", "description": "Outstanding tasks with owners and due dates."},
    "Alert": {"icon": "▲", "description": "A single flagged condition needing awareness."},
    "Chart": {"icon": "▤", "description": "A small aggregate chart — stage/priority/status breakdowns."},
    "Form": {"icon": "✎", "description": "A short input to capture a note or follow-up."},
    "ApprovalPanel": {"icon": "✓", "description": "A pending decision requiring human approval."},
    "ActionPanel": {"icon": "▷", "description": "A set of workspace-level quick actions."},
    "ActivityLog": {"icon": "≣", "description": "The audit trail of decisions and actions taken here."},
}


def _esc(s):
    return _html.escape(str(s)) if s is not None else ""


def _badge_html(source):
    name = source.get("name") if isinstance(source, dict) else source
    mock = source.get("mock") if isinstance(source, dict) else False
    cls = "src-badge src-badge-mock" if mock else "src-badge"
    label = _esc(name) + (" · mock" if mock else "")
    title = f'title="Simulated {_esc(name)} data — connector not yet live"' if mock else f'title="Live {_esc(name)} data"'
    return f'<span class="{cls}" {title}>{label}</span>'

def _sources_html(sources):
    if not sources:
        return ""
    return '<span class="src-badges">' + "".join(_badge_html(s) for s in sources) + "</span>"


def _action_button(workspace_id, prim_id, action, item_id=None, link=None):
    """A single action as either a real link (when workspace_engine.py
    supplied one — e.g. a genuine /thread/<id> deep link into the real
    Gmail inbox) or a POST form hitting the audited action-dispatch
    route. Approval-required actions carry a visible 'Human approval
    required' marker per the brief."""
    label = _esc(action.get("label"))
    approval = bool(action.get("approval_required"))
    marker = '<span class="approval-marker">Human approval required</span>' if approval else ""
    if link:
        return f'<a class="prim-action" href="{_esc(link)}">{label}</a>{marker}'
    hidden_item = f'<input type="hidden" name="item_id" value="{_esc(item_id)}">' if item_id else ""
    cls = "prim-action prim-action-approval" if approval else "prim-action"
    return (
        f'<form method="post" action="/workspace/{_esc(workspace_id)}/action" class="prim-action-form">'
        f'<input type="hidden" name="primitive_id" value="{_esc(prim_id)}">'
        f'{hidden_item}'
        f'<input type="hidden" name="action_key" value="{_esc(action.get("key"))}">'
        f'<button type="submit" class="{cls}">{label}</button>'
        f'</form>{marker}'
    )


def _actions_html(workspace_id, prim_id, actions, item_id=None, links=None):
    if not actions:
        return ""
    links = links or {}
    buttons = "".join(
        _action_button(workspace_id, prim_id, a, item_id=item_id, link=links.get(a.get("key")))
        for a in actions
    )
    return f'<div class="prim-actions">{buttons}</div>'


def _wrap(instance, workspace_id, inner_html):
    tone = instance.get("tone") or "neutral"
    span = (instance.get("position") or {}).get("span", "full")
    config_json = _esc(json.dumps({
        "id": instance["id"], "type": instance["type"], "title": instance.get("title"),
        "data_sources": instance.get("data_sources") or [],
        "visible_roles": instance.get("visible_roles") or [],
        "allowed_actions": instance.get("allowed_actions") or [],
        "approval_required": bool(instance.get("approval_required")),
    }))
    subtitle_html = f'<p class="prim-subtitle">{_esc(instance.get("subtitle"))}</p>' if instance.get("subtitle") else ""
    return (
        f'<article class="prim prim-{_esc(instance["type"])} tone-{_esc(tone)} span-{_esc(span)}" '
        f'data-prim-id="{_esc(instance["id"])}" data-prim-type="{_esc(instance["type"])}" '
        f'data-config="{config_json}" tabindex="0" role="button" '
        f'aria-label="{_esc(instance.get("title"))} — select to inspect">'
        f'<header class="prim-head"><h3 class="prim-title">{_esc(instance.get("title"))}</h3>'
        f'{_sources_html(instance.get("data_sources"))}</header>'
        f'{subtitle_html}'
        f'<div class="prim-body">{inner_html}</div>'
        f'</article>'
    )


# --- Metric ------------------------------------------------------------
def _render_metric(instance, workspace_id):
    d = instance.get("data") or {}
    trend = f'<span class="metric-trend">{_esc(d.get("trend"))}</span>' if d.get("trend") else ""
    return _wrap(instance, workspace_id, (
        f'<div class="metric-value">{_esc(d.get("value"))}</div>'
        f'<div class="metric-label">{_esc(d.get("label"))}</div>{trend}'
    ))


# --- PriorityList --------------------------------------------------------
def _priority_item_html(workspace_id, prim_id, item):
    why = "".join(
        f'<li><span class="why-text">{_esc(w.get("text"))}</span> '
        f'<span class="why-source">— {_esc(w.get("source"))}</span></li>'
        for w in (item.get("why") or [])
    )
    why_html = f'<ul class="priority-why">{why}</ul>' if why else ""
    info_html = f'<p class="priority-info">{_esc(item.get("info"))}</p>' if item.get("info") else ""
    rec_html = (
        f'<p class="priority-recommend"><span class="recommend-label">Recommended:</span> '
        f'{_esc(item.get("recommended_action"))}</p>'
    ) if item.get("recommended_action") else ""
    meta = []
    if item.get("customer"):
        meta.append(f'<span class="priority-customer">{_esc(item["customer"])}</span>')
    if item.get("deadline"):
        meta.append(f'<span class="priority-deadline">{_esc(item["deadline"])}</span>')
    meta_html = f'<div class="priority-meta">{"".join(meta)}</div>' if meta else ""
    priority = _esc(item.get("priority") or "normal")
    actions_html = _actions_html(
        workspace_id, prim_id, item.get("actions"), item_id=item.get("id"), links=item.get("links"),
    )
    return (
        f'<li class="priority-item priority-{priority}">'
        f'<div class="priority-item-head">'
        f'<span class="priority-flag priority-flag-{priority}">{priority}</span>'
        f'<span class="priority-item-title">{_esc(item.get("title"))}</span>'
        f'{_sources_html(item.get("sources"))}'
        f'</div>'
        f'{meta_html}{why_html}{info_html}{rec_html}{actions_html}'
        f'</li>'
    )


def _render_priority_list(instance, workspace_id):
    d = instance.get("data") or {}
    items = d.get("items") or []
    if not items:
        return _wrap(instance, workspace_id, '<p class="prim-empty">Nothing needs attention here right now.</p>')
    rows = "".join(_priority_item_html(workspace_id, instance["id"], it) for it in items)
    return _wrap(instance, workspace_id, f'<ol class="priority-list">{rows}</ol>')


# --- DataTable -----------------------------------------------------------
def _render_data_table(instance, workspace_id):
    d = instance.get("data") or {}
    columns = d.get("columns") or []
    rows = d.get("rows") or []
    if not columns or not rows:
        return _wrap(instance, workspace_id, '<p class="prim-empty">No records.</p>')
    head = "".join(f'<th>{_esc(c["label"])}</th>' for c in columns)
    body = "".join(
        "<tr>" + "".join(f'<td>{_esc(r.get(c["key"]))}</td>' for c in columns) + "</tr>"
        for r in rows
    )
    return _wrap(instance, workspace_id, (
        f'<div class="table-scroll"><table class="data-table"><thead><tr>{head}</tr></thead>'
        f'<tbody>{body}</tbody></table></div>'
    ))


# --- CustomerCard ----------------------------------------------------------
def _render_customer_card(instance, workspace_id):
    d = instance.get("data") or {}
    facts = "".join(
        f'<div class="fact"><span class="fact-label">{_esc(f["label"])}</span>'
        f'<span class="fact-value">{_esc(f["value"])}</span></div>'
        for f in (d.get("facts") or [])
    )
    summary_html = f'<p class="customer-summary">{_esc(d.get("summary"))}</p>' if d.get("summary") else ""
    actions_html = _actions_html(workspace_id, instance["id"], instance.get("allowed_actions"), links=d.get("links"))
    return _wrap(instance, workspace_id, f'{summary_html}<div class="fact-grid">{facts}</div>{actions_html}')


# --- MessageCard -----------------------------------------------------------
def _render_message_card(instance, workspace_id):
    d = instance.get("data") or {}
    messages = d.get("messages") or []
    if not messages:
        return _wrap(instance, workspace_id, '<p class="prim-empty">No messages.</p>')
    rows = []
    for m in messages:
        link = f'<a class="message-open" href="{_esc(m["link"])}">Open</a>' if m.get("link") else ""
        rows.append(
            '<li class="message-row">'
            f'<div class="message-row-head"><span class="message-from">{_esc(m.get("from"))}</span>'
            f'<span class="message-date">{_esc(m.get("date"))}</span></div>'
            f'<div class="message-subject">{_esc(m.get("subject"))}</div>'
            f'<div class="message-snippet">{_esc(m.get("snippet"))}</div>{link}'
            '</li>'
        )
    return _wrap(instance, workspace_id, f'<ul class="message-list">{"".join(rows)}</ul>')


# --- Timeline --------------------------------------------------------------
def _render_timeline(instance, workspace_id):
    d = instance.get("data") or {}
    events = d.get("events") or []
    if not events:
        return _wrap(instance, workspace_id, '<p class="prim-empty">No history yet.</p>')
    rows = "".join(
        '<li class="timeline-event">'
        f'<span class="timeline-date">{_esc(e.get("date"))}</span>'
        f'<span class="timeline-label">{_esc(e.get("label"))}</span>'
        f'{_sources_html([e["source"]] if e.get("source") else [])}'
        '</li>'
        for e in events
    )
    return _wrap(instance, workspace_id, f'<ol class="timeline">{rows}</ol>')


# --- TaskQueue ---------------------------------------------------------
def _render_task_queue(instance, workspace_id):
    d = instance.get("data") or {}
    tasks = d.get("tasks") or []
    if not tasks:
        return _wrap(instance, workspace_id, '<p class="prim-empty">No open tasks.</p>')
    rows = []
    for t in tasks:
        overdue_cls = " task-overdue" if t.get("overdue") else ""
        rows.append(
            f'<li class="task-row{overdue_cls}">'
            f'<span class="task-title">{_esc(t.get("title"))}</span>'
            f'<span class="task-owner">{_esc(t.get("owner"))}</span>'
            f'<span class="task-due">{_esc(t.get("due"))}</span>'
            f'{_sources_html([t["source"]] if t.get("source") else [])}'
            '</li>'
        )
    return _wrap(instance, workspace_id, f'<ul class="task-list">{"".join(rows)}</ul>')


# --- Alert -----------------------------------------------------------------
def _render_alert(instance, workspace_id):
    d = instance.get("data") or {}
    if d.get("items"):
        rows = "".join(f'<li>{_esc(item)}</li>' for item in d["items"])
        return _wrap(instance, workspace_id, f'<ul class="alert-list">{rows}</ul>')
    return _wrap(instance, workspace_id, f'<p class="alert-message">{_esc(d.get("message"))}</p>')


# --- Chart (CSS-only horizontal bars, no external chart library) -----------
def _render_chart(instance, workspace_id):
    d = instance.get("data") or {}
    series = d.get("series") or []
    if not series:
        return _wrap(instance, workspace_id, '<p class="prim-empty">No data to chart.</p>')
    max_val = max((s["value"] for s in series), default=1) or 1
    rows = []
    for s in series:
        pct = round(100 * s["value"] / max_val)
        rows.append(
            '<div class="chart-row">'
            f'<span class="chart-row-label">{_esc(s["label"])}</span>'
            f'<div class="chart-bar-track"><div class="chart-bar" style="width:{pct}%"></div></div>'
            f'<span class="chart-row-value">{_esc(s.get("display_value", s["value"]))}</span>'
            '</div>'
        )
    return _wrap(instance, workspace_id, f'<div class="chart">{"".join(rows)}</div>')


# --- Form (a real, minimal POST — see workspace_site.py's /form route) -----
def _render_form(instance, workspace_id):
    d = instance.get("data") or {}
    label = _esc(d.get("label") or "Note")
    placeholder = _esc(d.get("placeholder") or "")
    return _wrap(instance, workspace_id, (
        f'<form method="post" action="/workspace/{_esc(workspace_id)}/form/{_esc(instance["id"])}" class="prim-form">'
        f'<label class="form-field-label" for="note-{_esc(instance["id"])}">{label}</label>'
        f'<textarea id="note-{_esc(instance["id"])}" name="note" placeholder="{placeholder}" rows="3"></textarea>'
        f'<button type="submit" class="prim-action">{_esc(d.get("submit_label") or "Save")}</button>'
        f'</form>'
    ))


# --- ApprovalPanel -----------------------------------------------------
def _render_approval_panel(instance, workspace_id, role_key, can_approve):
    d = instance.get("data") or {}
    status = d.get("status", "pending")
    status_html = f'<span class="approval-status approval-status-{_esc(status)}">{_esc(status)}</span>'
    desc_html = f'<p class="approval-desc">{_esc(d.get("description"))}</p>' if d.get("description") else ""
    requested = f'<p class="approval-meta">Requested by {_esc(d.get("requested_by"))}</p>' if d.get("requested_by") else ""
    if status == "pending" and can_approve:
        controls = (
            f'<form method="post" action="/workspace/{_esc(workspace_id)}/approve/{_esc(d.get("approval_id"))}" class="inline-form">'
            f'<button type="submit" class="prim-action approve-btn">Approve</button></form>'
            f'<form method="post" action="/workspace/{_esc(workspace_id)}/reject/{_esc(d.get("approval_id"))}" class="inline-form">'
            f'<button type="submit" class="prim-action reject-btn">Reject</button></form>'
        )
    elif status == "pending":
        controls = '<p class="approval-meta">Awaiting a manager or above to decide.</p>'
    else:
        decided_by = f' by {_esc(d.get("decided_by"))}' if d.get("decided_by") else ""
        controls = f'<p class="approval-meta">Decision: {_esc(status)}{decided_by}</p>'
    return _wrap(instance, workspace_id, (
        f'<div class="approval-head">{status_html}</div>{desc_html}{requested}'
        f'<div class="prim-actions">{controls}</div>'
    ))


# --- ActionPanel -------------------------------------------------------
def _render_action_panel(instance, workspace_id):
    d = instance.get("data") or {}
    context_html = f'<p class="prim-subtitle-inline">{_esc(d.get("context"))}</p>' if d.get("context") else ""
    actions_html = _actions_html(workspace_id, instance["id"], instance.get("allowed_actions"), links=d.get("links"))
    return _wrap(instance, workspace_id, f'{context_html}{actions_html}')


# --- ActivityLog (live — reads audit_log at render time) -------------------
def _render_activity_log(instance, workspace_id):
    entries = audit_log.list_for_workspace(workspace_id, limit=20)
    if not entries:
        return _wrap(instance, workspace_id, '<p class="prim-empty">No activity recorded for this workspace yet.</p>')
    rows = "".join(
        '<li class="activity-row">'
        f'<span class="activity-at">{_esc(e.get("at"))}</span>'
        f'<span class="activity-event">{_esc(e.get("event_type"))}</span>'
        f'<span class="activity-actor">{_esc(e.get("actor"))}</span>'
        '</li>'
        for e in entries
    )
    return _wrap(instance, workspace_id, f'<ul class="activity-log">{rows}</ul>')


_RENDERERS = {
    "Metric": lambda i, w, r, ca: _render_metric(i, w),
    "PriorityList": lambda i, w, r, ca: _render_priority_list(i, w),
    "DataTable": lambda i, w, r, ca: _render_data_table(i, w),
    "CustomerCard": lambda i, w, r, ca: _render_customer_card(i, w),
    "MessageCard": lambda i, w, r, ca: _render_message_card(i, w),
    "Timeline": lambda i, w, r, ca: _render_timeline(i, w),
    "TaskQueue": lambda i, w, r, ca: _render_task_queue(i, w),
    "Alert": lambda i, w, r, ca: _render_alert(i, w),
    "Chart": lambda i, w, r, ca: _render_chart(i, w),
    "Form": lambda i, w, r, ca: _render_form(i, w),
    "ApprovalPanel": lambda i, w, r, ca: _render_approval_panel(i, w, r, ca),
    "ActionPanel": lambda i, w, r, ca: _render_action_panel(i, w),
    "ActivityLog": lambda i, w, r, ca: _render_activity_log(i, w),
}


def render_primitive(instance, workspace_id, role_key="sales_rep", can_approve=False):
    """The single entrypoint workspace_site.py's canvas renderer calls.
    Raises KeyError for an unregistered type — a programming error (every
    instance workspace_engine.py builds must use a PRIMITIVE_TYPES value),
    not a user-facing case."""
    renderer = _RENDERERS[instance["type"]]
    return renderer(instance, workspace_id, role_key, can_approve)
