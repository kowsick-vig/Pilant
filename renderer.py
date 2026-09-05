"""
Turns a render_view JSON object (validated against schema.py) into a real,
self-contained HTML page — the piece that turns "the agent produced correct
JSON" into "here's an actual screen," using the same visual language as the
interactive demo artifact. Pure string generation, no client-side JS needed.
"""

import html as _html
import re

# Matches a real Jira-style issue key at the START of a row's 'name' (e.g.
# "ENG-482 — Checkout fails..." -> "ENG-482") — every Jira row's name is set
# this way by both agent_jira.py's and agent_unified.py's SYSTEM prompts.
# Used only when jira_detail_base is provided (see _component_html's list
# branch) to turn a Jira row into a real link to studio.py's detail panel,
# without needing the model to set the row's 'url' field itself.
_ISSUE_KEY_RE = re.compile(r"^([A-Z][A-Z0-9]*-\d+)")


def _esc(s):
    return _html.escape(str(s)) if s is not None else ""


def _badge(b):
    if not b:
        return ""
    tone = _esc(b.get("tone", "default"))
    return f'<span class="badge {tone}">{_esc(b.get("text", ""))}</span>'


def _list_row_html(r):
    # url is OPTIONAL and additive (schema.py, added 2026-08-26 for
    # search_knowledge_base results rendered via render_view) — most
    # connectors never set it, so most rows still render exactly as before.
    # When it's there, wrap the name in a real anchor instead of inventing
    # a second "open" affordance the shared row schema has no field for.
    name_text = _esc(r.get("name"))
    url = r.get("url")
    name_html = (
        f'<div class="list-name"><a href="{_esc(url)}" target="_blank" rel="noopener">{name_text}</a></div>'
        if url else f'<div class="list-name">{name_text}</div>'
    )

    note = r.get("note")
    note_html = f'<div class="list-note">{_esc(note)}</div>' if note else ""

    badge = r.get("badge")
    action = r.get("action")
    if badge:
        right_html = _badge(badge)
    elif action:
        right_html = f'<div class="list-action">{_esc(action)}</div>'
    else:
        right_html = ""

    return f'<div class="list-row"><div>{name_html}{note_html}</div>{right_html}</div>'


_EMAIL_SENDER_SUBJECT_RE = re.compile(
    r"^(?P<sender>.*?<[^<>@]+@[^<>]+>)\s*:\s*(?P<subject>.+)$", re.DOTALL
)
_BARE_LABEL_SPLIT_RE = re.compile(r"^(?P<label>[^:]{1,80}):\s*(?P<rest>.+)$", re.DOTALL)


def _parse_email_row(name, note):
    """Best-effort split of a generic list row's 'name'/'note' strings into
    (sender, subject, snippet) for the Gmail-styled renderer below. There's
    no dedicated sender/subject field in the shared list-row schema (it's
    the same 'name'/'note'/'badge' shape every connector's render_view
    uses, by design — see schema.py) — agent_gmail.py's own system prompt
    tells the model 'row name = the subject or a short from+subject combo,
    row note = the snippet or sender', which is genuinely ambiguous on
    purpose (the model picks whichever reads best for a given email), so
    this never assumes one fixed shape. Tries, in order: a real 'Name
    <email@x>: Subject' pattern (what the model has actually written live
    in every real log this project has seen so far); then a shorter bare
    'Label: rest' split IF the label side is short enough to plausibly be a
    name rather than a sentence fragment; otherwise gives up gracefully and
    treats the whole 'name' as the subject with no separate sender — never
    fabricates a sender that isn't actually there."""
    name = name or ""
    note = note or ""
    m = _EMAIL_SENDER_SUBJECT_RE.match(name)
    if m:
        return {"sender": m.group("sender").strip(), "subject": m.group("subject").strip(), "snippet": note}
    m2 = _BARE_LABEL_SPLIT_RE.match(name)
    if m2 and len(m2.group("label").split()) <= 8:
        return {"sender": m2.group("label").strip(), "subject": m2.group("rest").strip(), "snippet": note}
    return {"sender": "", "subject": name, "snippet": note}


def _gmail_row_html(r):
    """One inbox row in the Gmail-styled renderer (render_gmail_fragment
    below) — checkbox, star, sender, subject + snippet, and a status pill
    where Gmail would normally show a date (there's no per-row date in the
    shared row schema to draw a real one from — see _parse_email_row's
    docstring — so this never invents one; the badge, e.g. 'Unread', fills
    that visual slot honestly instead)."""
    parsed = _parse_email_row(r.get("name"), r.get("note"))
    badge = r.get("badge") or {}
    badge_text = (badge.get("text") or "").strip()
    is_unread = badge.get("tone") == "warning" or "unread" in badge_text.lower()
    row_cls = "gmail-row gmail-row-unread" if is_unread else "gmail-row"
    sender_html = _esc(parsed["sender"]) if parsed["sender"] else "&nbsp;"
    subject_html = _esc(parsed["subject"])
    snippet_html = f' <span class="gmail-snippet">&mdash; {_esc(parsed["snippet"])}</span>' if parsed["snippet"] else ""
    tone = _esc(badge.get("tone", "default"))
    status_html = f'<span class="gmail-row-status gmail-row-status-{tone}">{_esc(badge_text)}</span>' if badge_text else ""
    return (
        f'<div class="{row_cls}">'
        '<span class="gmail-row-check" aria-hidden="true"></span>'
        '<span class="gmail-row-star" aria-hidden="true">&#9734;</span>'
        f'<span class="gmail-row-sender">{sender_html}</span>'
        f'<span class="gmail-row-subject">{subject_html}{snippet_html}</span>'
        f'{status_html}'
        '</div>'
    )


def _gmail_component_html(c):
    """Like _component_html, but a 'list' component renders as real Gmail
    inbox rows (_gmail_row_html) instead of the generic card-list rows —
    every other component type (stat_grid, panel — e.g. if a request comes
    back as a single-message detail view rather than a list) falls back to
    the same generic rendering _component_html already provides, since
    those don't have a natural 'looks like Gmail' equivalent and forcing
    one would mean inventing UI the request never asked for."""
    if c.get("type") == "list":
        rows = c.get("rows", [])
        if not rows:
            return '<div class="gmail-empty">Nothing matched — try a broader request (e.g. a longer time range).</div>'
        return "".join(_gmail_row_html(r) for r in rows)
    return _component_html(c)


def render_gmail_fragment(view, account_label=None):
    """A Gmail-connector-aware rendering of the same render_view JSON every
    other connector uses — same data, same schema, no special-casing in
    the agent or the schema itself, just a different skin at the
    presentation layer (studio.py picks this over render_fragment() only
    when the workflow's connector is Gmail — see _preview_html there).

    Originally (2026-08-25) this skin mimicked real Gmail's own light-mode
    chrome — white background, the actual Gmail wordmark, Gmail's exact
    blue/gold colors. Replaced 2026-08-26 at the user's explicit request
    ("don't want to like gmail. want to look like full view interface"):
    this now uses the SAME dark palette, sidebar shape, and row layout as
    gmail_site.py's real interactive inbox (--surface/--card/--border/
    --accent/--star, the pill Compose button, the folder-list sidebar) —
    var(--ground)/var(--surface)/etc. are safe to reference here because
    studio.py's own SHARED_CSS defines them in :root before this stylesheet
    is concatenated after it (see studio.py's _page_shell). The point now
    is visual CONSISTENCY between "the chat's read-only preview of a Gmail
    workflow" and "the full inbox it links to," not a Gmail-brand
    impression — someone bouncing between the two (via the top-left
    view-toggle icon) should recognize it as the same product, not two
    unrelated skins that happen to share data.

    account_label, if given (studio.py passes
    connectors_gmail.get_connected_account() when a real account is
    connected), shows next to the heading so the preview reads as "this is
    actually your inbox," not a generic mockup."""
    heading = _esc(view.get("heading", ""))
    meta = view.get("meta")
    meta_html = f'<p class="gmail-meta">{_esc(meta)}</p>' if meta else ""
    components = view.get("components", [])

    unread_count = 0
    for c in components:
        if c.get("type") == "list":
            for r in c.get("rows", []):
                b = r.get("badge") or {}
                if b.get("tone") == "warning" or "unread" in (b.get("text") or "").lower():
                    unread_count += 1
    count_html = f'<span class="gmail-nav-count">{unread_count}</span>' if unread_count else ""

    account_html = f' <span class="gmail-account">&middot; {_esc(account_label)}</span>' if account_label else ""

    body_html = "".join(_gmail_component_html(c) for c in components)

    return (
        '<div class="gmail-app">'
        '<div class="gmail-sidebar">'
        '<button type="button" class="gmail-compose">&#9998; Compose</button>'
        '<nav class="gmail-nav">'
        f'<a class="gmail-nav-item active">Inbox{count_html}</a>'
        '<a class="gmail-nav-item">Starred</a>'
        '<a class="gmail-nav-item">Sent</a>'
        '<a class="gmail-nav-item">Drafts</a>'
        '</nav>'
        '</div>'
        '<div class="gmail-main">'
        f'<p class="gmail-heading">{heading}{account_html}</p>'
        f'{meta_html}'
        f'<div class="gmail-list">{body_html}</div>'
        '</div>'
        '</div>'
    )


def _slugify(text):
    """Turns a component title ("Jira", "Gmail (unread)") into an id-safe
    token for the feed filter chips below — used both as a chip's
    data-filter value and as the matching list panel's data-src, so the two
    only ever need to agree on this one deterministic transform."""
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return slug or "section"


def _connector_color(title, connector_colors):
    """Same exact-then-substring lookup _list_title_html already uses for
    connector_links, reused here for connector_colors (added 2026-09-01 for
    the merged-screen "feed" redesign — a small colored dot next to a list's
    title, and the filter chips below, use the SAME per-connector color the
    sidebar/Integrations grid already show, so a connector reads as one
    consistent identity everywhere in the app). Returns None (render nothing
    extra) when there's no title, no map, or no match — the normal case for
    every render_fragment() caller except studio.py's live preview."""
    if not title or not connector_colors:
        return None
    t_lower = title.strip().lower()
    color = connector_colors.get(t_lower)
    if color:
        return color
    for label, c in connector_colors.items():
        if label and label in t_lower:
            return c
    return None


def _list_title_html(title, connector_links, connector_colors=None):
    """Renders a 'list' component's title — plain text by default, same as
    always, UNLESS `connector_links` says this title NAMES a real connector
    the person could open directly. That's what makes the merged
    'All apps' screen's per-app sections (agent_unified.py's SYSTEM prompt
    sets a list's title to 'Gmail', 'Slack', etc. when merging more than
    one app) clickable — added 2026-08-31 at the user's explicit request
    ("If we click Gmail. It should navigate to the Gmail interface ...
    make it as a workflow").

    Checks for an EXACT match first, then falls back to substring
    containment (does a connector's label appear anywhere in the title?) —
    added 2026-08-31 after a live miss: the model titled real sections
    "Jira (High priority)" and "Gmail (Marked important)" instead of the
    bare "Jira"/"Gmail" the SYSTEM prompt's own example showed, which is a
    reasonable thing for it to write and was never actually forbidden, but
    an exact-match-only check silently rendered both as plain, unclickable
    text — the button never appeared, and there was no error to notice.
    Substring containment (checked against a lowercased title) still can't
    misfire onto an unrelated app, since it's only ever checking for one of
    the small, fixed set of real connector labels registered in
    connector_links.

    Deliberately a POST <form>, not a plain <a href>, matching the exact
    mechanism the Integrations page's own "Start a workflow" button already
    uses (studio.py's /studio/new/<connector_key> route) — this reuses that
    same route rather than inventing a second way to start a connector-
    scoped workflow, so clicking 'Gmail' here does exactly what clicking
    the Gmail card on the Integrations page does: opens a brand-new
    workflow already scoped to that connector.

    connector_links is None almost everywhere else render_fragment() is
    called from (retail_site.py, the customer-360 composer, etc.) — those
    callers never pass it, so this silently falls back to the exact same
    plain title text rendering as before this feature existed. Only
    studio.py's own live-preview panel passes it."""
    esc_title = _esc(title)
    target = None
    if title and connector_links:
        t_lower = title.strip().lower()
        target = connector_links.get(t_lower)
        if not target:
            for label, url in connector_links.items():
                if label and label in t_lower:
                    target = url
                    break
    color = _connector_color(title, connector_colors)
    dot_html = f'<span class="feed-source-dot" style="background:{color["fg"]}"></span>' if color else ""
    if target:
        return (
            f'<form method="post" action="{_esc(target)}" class="list-title-link-form">'
            f'<button type="submit" class="list-title-link">{dot_html}{esc_title} '
            '<span class="list-title-link-arrow">&rarr;</span></button>'
            '</form>'
        )
    return f'<p class="panel-sub" style="margin-bottom:10px;">{dot_html}{esc_title}</p>'


def _component_html(c, connector_links=None, connector_colors=None, jira_detail_base=None):
    ctype = c.get("type")

    if ctype == "stat_grid":
        cards = "".join(
            f'<div class="stat-card tone-{_esc(s.get("tone", "default"))}">'
            f'<span class="stat-label">{_esc(s.get("label"))}</span>'
            f'<span class="stat-value">{_esc(s.get("value"))}</span></div>'
            for s in c.get("stats", [])
        )
        return f'<div class="stat-grid">{cards}</div>'

    if ctype == "panel":
        fields = "".join(
            f'<div><dt>{_esc(f.get("label"))}</dt><dd>{_esc(f.get("value"))}</dd></div>'
            for f in c.get("fields", [])
        )
        fields_html = f'<dl class="kv-grid">{fields}</dl>' if fields else ""
        action_html = f'<button class="app-btn">{_esc(c["action"])}</button>' if c.get("action") else ""
        subtitle_html = f'<p class="panel-sub">{_esc(c.get("subtitle"))}</p>' if c.get("subtitle") else ""
        return (
            f'<div class="panel"><div class="panel-head"><div>'
            f'<p class="panel-title">{_esc(c.get("title"))}</p>{subtitle_html}'
            f'</div>{_badge(c.get("badge"))}</div>{fields_html}{action_html}</div>'
        )

    if ctype == "list":
        row_items = c.get("rows", [])
        title = c.get("title")
        # Jira row-linking — added 2026-09-01 for the real read+write detail
        # panel (studio.py's /studio?jira_detail=<key>): only when this
        # list's title names Jira AND a caller passed jira_detail_base (only
        # studio.py's live preview does — every other render_fragment/
        # render_html caller passes None and gets identical behavior to
        # before this existed). A row whose 'name' starts with a real issue
        # key (agent_jira.py's and agent_unified.py's SYSTEM prompts both
        # set it up that way) gets a real url pointing at that issue's
        # detail view — reusing the row schema's existing optional 'url'
        # field (see _list_row_html) rather than inventing a second link
        # mechanism. Never overwrites a url the model already set itself.
        if title and jira_detail_base and "jira" in title.lower():
            linked_rows = []
            for r in row_items:
                if isinstance(r, dict) and not r.get("url") and isinstance(r.get("name"), str):
                    m = _ISSUE_KEY_RE.match(r["name"].strip())
                    if m:
                        r = {**r, "url": jira_detail_base + m.group(1)}
                linked_rows.append(r)
            row_items = linked_rows
        # Empty-state message added 2026-08-25: a genuinely correct render_view (real query,
        # real guardrail-verified zero results — e.g. "show recent emails" resolving to
        # newer_than:1d with nothing that new) used to render as a totally blank box with no
        # explanation, indistinguishable from something having silently broken. Say so instead.
        rows = (
            "".join(_list_row_html(r) for r in row_items)
            if row_items
            else '<div class="panel-sub" style="padding:8px 0;">Nothing matched — try a broader request (e.g. a longer time range).</div>'
        )
        title_html = _list_title_html(title, connector_links, connector_colors) if title else ""
        subtitle_html = f'<p class="panel-sub" style="margin-bottom:10px;">{_esc(c.get("subtitle"))}</p>' if c.get("subtitle") else ""
        # data-src/feed-block: added 2026-09-01 for the merged-screen filter
        # chips (_filter_chip_bar_html/FEED_FILTER_SCRIPT below) — only a
        # TITLED list is ever a chip target (an untitled list has no chip to
        # match it to), so this never changes anything for the many callers
        # that don't title their lists.
        if title:
            return f'<div class="panel feed-block" data-src="{_esc(_slugify(title))}">{title_html}{subtitle_html}{rows}</div>'
        return f'<div class="panel">{title_html}{subtitle_html}{rows}</div>'

    if ctype == "suggestions":
        # Added 2026-08-27 for the customer-360 primitive — deliberately its
        # own visual treatment (dashed border, an explicit "AI suggestion"
        # tag) rather than reusing .panel, so a recommended action can never
        # be mistaken for a fact the way a stat_grid/panel/list row is. See
        # schema.py's "suggestions" field docstring and guardrails.py's
        # find_ungrounded_suggestion_facts for the enforcement side of this
        # same distinction.
        items = [s for s in (c.get("suggestions") or []) if isinstance(s, str) and s.strip()]
        if not items:
            return ""
        title_html = f'<p class="suggestion-title">{_esc(c.get("title") or "Suggested actions")}</p>'
        rows = "".join(f"<li>{_esc(s)}</li>" for s in items)
        return (
            '<div class="suggestion-panel"><span class="suggestion-tag">AI suggestion</span>'
            f'{title_html}<ul class="suggestion-list">{rows}</ul></div>'
        )

    return f'<div class="panel"><p class="panel-sub">Unknown component type: {_esc(ctype)}</p></div>'


CSS = """
:root { --ground:#0B1220; --surface:#141C30; --border:#2A3552; --text:#EDEFF5; --text-muted:#8791A8; --accent:#6C7CFF; }
* { box-sizing:border-box; }
body { margin:0; background:var(--ground); color:var(--text); font-family:'IBM Plex Sans',system-ui,sans-serif; }
.page { max-width:760px; margin:0 auto; padding:56px 24px 80px; }
.eyebrow { font-family:'IBM Plex Mono',monospace; font-size:.72rem; letter-spacing:.1em; text-transform:uppercase; color:var(--accent); margin:0 0 14px; }
.request { font-size:1.02rem; color:var(--text-muted); margin:0 0 32px; padding:14px 16px; border:1px solid var(--border); border-radius:8px; background:var(--surface); }
.device { border:1px solid var(--border); border-radius:14px; overflow:hidden; box-shadow:0 6px 16px rgba(23,27,46,.08), 0 2px 4px rgba(23,27,46,.05); }
.device-bar { display:flex; gap:6px; padding:10px 14px; border-bottom:1px solid var(--border); background:var(--surface); }
.device-bar span { width:8px; height:8px; border-radius:50%; background:var(--border); display:block; }
.app-window { --app-bg:#F4F6FB; --app-card:#FFFFFF; --app-border:#E3E7F1; --app-text:#171B2E; --app-muted:#6B7385; --app-accent:#5B6EF5; --app-critical:#C6392F; --app-critical-bg:#FBEAE8; --app-warning:#A56A0E; --app-warning-bg:#FBF1DE; --app-good:#227A55; --app-good-bg:#E6F5EE; background:var(--app-bg); color:var(--app-text); padding:30px; min-height:300px; }
.app-h1 { font-family:'Sora',sans-serif; font-weight:600; font-size:1.3rem; margin:0 0 4px; }
.app-meta { font-size:.85rem; color:var(--app-muted); margin:0 0 20px; }
.stat-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:12px; margin-bottom:16px; }
.stat-card { background:var(--app-card); border:1px solid var(--app-border); border-left:3px solid var(--app-border); border-radius:10px; padding:13px 14px; box-shadow:0 1px 2px rgba(23,27,46,.05), 0 1px 1px rgba(23,27,46,.04); }
.stat-card.tone-critical { border-left-color:var(--app-critical); }
.stat-card.tone-warning { border-left-color:var(--app-warning); }
.stat-card.tone-good { border-left-color:var(--app-good); }
.stat-label { font-size:.68rem; letter-spacing:.05em; text-transform:uppercase; color:var(--app-muted); display:block; margin-bottom:6px; font-family:'IBM Plex Mono',monospace; }
.stat-value { font-family:'Sora',sans-serif; font-weight:700; font-size:1.35rem; }
.panel { background:var(--app-card); border:1px solid var(--app-border); border-radius:12px; padding:18px 20px; margin-bottom:12px; box-shadow:0 1px 2px rgba(23,27,46,.05), 0 1px 1px rgba(23,27,46,.04); }
.panel-head { display:flex; justify-content:space-between; align-items:flex-start; gap:12px; margin-bottom:10px; }
.panel-title { font-family:'Sora',sans-serif; font-weight:600; font-size:1rem; margin:0 0 3px; }
.panel-sub { font-size:.8rem; color:var(--app-muted); margin:0; }
.badge { display:inline-block; font-family:'IBM Plex Mono',monospace; font-size:.68rem; font-weight:500; letter-spacing:.03em; text-transform:uppercase; padding:3px 9px; border-radius:4px; flex:none; }
.badge.critical { background:var(--app-critical-bg); color:var(--app-critical); }
.badge.warning { background:var(--app-warning-bg); color:var(--app-warning); }
.badge.good { background:var(--app-good-bg); color:var(--app-good); }
.badge.default { background:var(--app-border); color:var(--app-muted); }
.kv-grid { display:grid; grid-template-columns:1fr 1fr; gap:10px 24px; margin:14px 0; }
.kv-grid dt { font-size:.68rem; text-transform:uppercase; letter-spacing:.04em; color:var(--app-muted); font-family:'IBM Plex Mono',monospace; margin-bottom:2px; }
.kv-grid dd { margin:0; font-size:.86rem; }
.app-btn { font-family:'IBM Plex Sans',sans-serif; font-weight:500; font-size:.82rem; background:var(--app-accent); color:#fff; border:none; border-radius:6px; padding:9px 16px; cursor:pointer; }
.list-row { display:flex; justify-content:space-between; align-items:center; gap:14px; padding:12px 0; border-bottom:1px solid var(--app-border); }
.list-row:last-child { border-bottom:none; }
.list-name { font-weight:600; font-size:.88rem; }
.list-name a { color:inherit; text-decoration:none; }
.list-name a:hover { color:var(--app-accent); text-decoration:underline; }
.list-note { font-size:.78rem; color:var(--app-muted); margin-top:2px; }
.list-action { font-size:.76rem; color:var(--app-accent); font-weight:500; white-space:nowrap; }
.list-title-link-form { margin:0 0 10px; }
.list-title-link { font-family:'IBM Plex Sans',sans-serif; font-size:.8rem; font-weight:600; color:var(--app-accent); background:none; border:none; padding:0; margin:0; cursor:pointer; display:inline-flex; align-items:center; gap:5px; }
.list-title-link:hover { text-decoration:underline; }
.list-title-link-arrow { font-size:.78rem; transition:transform .12s ease; }
.list-title-link:hover .list-title-link-arrow { transform:translateX(2px); }
.feed-source-dot { display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:7px; vertical-align:middle; flex:none; }
.feed-chip-bar { display:flex; flex-wrap:wrap; gap:8px; margin:0 0 16px; }
.feed-chip { display:inline-flex; align-items:center; font-family:'IBM Plex Sans',sans-serif; font-size:.78rem; font-weight:500; color:var(--app-muted); background:var(--app-card); border:1px solid var(--app-border); border-radius:999px; padding:7px 13px; cursor:pointer; }
.feed-chip:hover { border-color:var(--app-accent); color:var(--app-text); }
.feed-chip.active { color:#fff; background:var(--app-accent); border-color:var(--app-accent); font-weight:600; }
.feed-chip-count { font-family:'IBM Plex Mono',monospace; font-size:.72rem; opacity:.75; margin-left:6px; }
.footer { margin-top:40px; padding-top:20px; border-top:1px solid var(--border); font-family:'IBM Plex Mono',monospace; font-size:.72rem; color:var(--text-muted); }
.back-link { display:inline-block; font-family:'IBM Plex Mono',monospace; font-size:.72rem; color:var(--text-muted); text-decoration:none; margin:0 0 14px; }
.back-link:hover { color:var(--accent); }
.saved-tag { font-family:'IBM Plex Mono',monospace; font-size:.72rem; color:var(--text-muted); }
.save-row { display:flex; gap:8px; margin:0 0 20px; }
.save-row input[type=text] { flex:1; background:var(--surface); border:1px solid var(--border); border-radius:8px; padding:10px 14px; font-size:.85rem; color:var(--text); font-family:'IBM Plex Sans',sans-serif; }
.save-row input[type=text]:focus { outline:none; border-color:var(--accent); }
.save-row input[type=text]::placeholder { color:var(--text-muted); }
.save-row button { background:var(--accent); color:#fff; border:none; border-radius:8px; padding:10px 16px; font-size:.82rem; font-weight:600; cursor:pointer; font-family:'IBM Plex Sans',sans-serif; white-space:nowrap; }
.suggestion-panel { background:var(--app-card); border:1px dashed var(--app-accent); border-radius:12px; padding:16px 18px; margin-bottom:12px; }
.suggestion-tag { display:inline-block; font-family:'IBM Plex Mono',monospace; font-size:.66rem; font-weight:600; letter-spacing:.05em; text-transform:uppercase; color:var(--app-accent); margin-bottom:8px; }
.suggestion-title { font-family:'Sora',sans-serif; font-weight:600; font-size:.92rem; margin:0 0 8px; }
.suggestion-list { margin:0; padding-left:18px; font-size:.85rem; color:var(--app-text); }
.suggestion-list li { margin-bottom:6px; }
.suggestion-list li:last-child { margin-bottom:0; }
"""

# Styling for render_gmail_fragment() above — as of 2026-08-26, deliberately
# the SAME dark tokens/shapes gmail_site.py's real interactive inbox uses
# (--surface/--border/--text/--text-muted/--accent from studio.py's own
# :root, plus a few gmail_site.py-matching hex values for the tones
# gmail_site.py's SHARED_CSS defines that studio.py's root doesn't —
# --star's #F0B429, --danger's #E8A5A0/#D9695F, --good's #5FCE9A — kept as
# literal hex here rather than adding more shared :root tokens, since this
# is the only other place that needs them). This used to be Gmail's own
# real light-mode color palette (see git history / this file's prior
# GMAIL_CSS) — replaced at the user's explicit request ("don't want to
# like gmail. want to look like full view interface") so the chat's
# read-only preview of a Gmail workflow looks like the same product as the
# full inbox it links to, not a separate imitation of the real Gmail app.
GMAIL_CSS = """
.gmail-app { background:var(--card,#182240); border:1px solid var(--border); border-radius:10px; overflow:hidden; font-family:'IBM Plex Sans',Arial,sans-serif; color:var(--text); display:flex; min-height:340px; }
.gmail-sidebar { width:168px; flex:none; background:var(--surface); padding:16px 10px; border-right:1px solid var(--border); }
.gmail-compose { display:flex; align-items:center; justify-content:center; gap:8px; background:var(--accent); color:#fff; border:none; border-radius:24px; padding:11px 14px; font-size:.82rem; font-weight:600; cursor:pointer; margin-bottom:16px; width:100%; font-family:inherit; }
.gmail-nav { display:flex; flex-direction:column; gap:2px; }
.gmail-nav-item { display:flex; justify-content:space-between; align-items:center; padding:8px 12px; border-radius:8px; font-size:.82rem; color:var(--text-muted); text-decoration:none; }
.gmail-nav-item.active { background:var(--accent); font-weight:600; color:#fff; }
.gmail-nav-count { font-size:.72rem; font-weight:600; }
.gmail-main { flex:1; padding:16px 20px 20px; min-width:0; }
.gmail-heading { font-family:'Sora',sans-serif; font-weight:600; font-size:1rem; margin:0 0 2px; color:var(--text); }
.gmail-account { font-family:'IBM Plex Sans',sans-serif; font-weight:400; font-size:.78rem; color:var(--text-muted); }
.gmail-meta { font-size:.78rem; color:var(--text-muted); margin:0 0 12px; }
.gmail-list { border-top:1px solid var(--border); }
.gmail-row { display:flex; align-items:center; gap:10px; padding:10px 4px; border-bottom:1px solid var(--border); font-size:.85rem; }
.gmail-row:hover { background:rgba(108,124,255,.06); }
.gmail-row-unread { font-weight:600; }
.gmail-row-check { flex:none; width:14px; height:14px; border:1.5px solid var(--border); border-radius:3px; }
.gmail-row-star { flex:none; color:var(--text-muted); font-size:1rem; line-height:1; }
.gmail-row-sender { flex:none; width:160px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:var(--text-muted); }
.gmail-row-subject { flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:var(--text); font-weight:inherit; }
.gmail-snippet { color:var(--text-muted); font-weight:400; }
.gmail-row-status { flex:none; font-family:'IBM Plex Mono',monospace; font-size:.66rem; font-weight:500; letter-spacing:.03em; text-transform:uppercase; padding:3px 9px; border-radius:4px; white-space:nowrap; }
.gmail-row-status-warning { color:#F0B429; background:rgba(240,180,41,.15); }
.gmail-row-status-critical { color:#E8A5A0; background:rgba(217,105,95,.15); }
.gmail-row-status-good { color:#5FCE9A; background:rgba(95,206,154,.15); }
.gmail-row-status-default { color:var(--text-muted); background:rgba(135,145,168,.15); }
.gmail-empty { padding:24px 8px; color:var(--text-muted); font-size:.85rem; }

/* Added 2026-08-26 for gmail_site.py's render_inbox_panel() — the real,
   working row list embedded directly in Studio's chat live-preview pane
   (the user's explicit request: "still can't control from this page ...
   just want to extend the interface not a separate full view mode").
   Same .gmail-row/.gmail-row-star/etc. classes above still carry the
   layout; these rules just make the star and delete controls look like
   real inline buttons rather than decorative text, since here they're
   wrapped in real <form>/<button> elements posting to gmail_bp's actual
   star/delete routes instead of inert spans. */
.gmail-row form.inline-form { display:inline-flex; margin:0; }
.gmail-row-star.gmail-row-star-btn { background:none; border:none; padding:0; cursor:pointer; font-size:1rem; line-height:1; font-family:inherit; color:var(--text-muted); }
.gmail-row-star.gmail-row-star-btn.active { color:var(--star,#F0B429); }
.gmail-row-delete { flex:none; background:none; border:none; cursor:pointer; padding:0 2px; font-size:.9rem; line-height:1; color:var(--text-muted); }
.gmail-row-delete:hover { color:var(--danger-strong,#D9695F); }
a.gmail-row-sender, a.gmail-row-subject { text-decoration:none; }
a.gmail-row-sender:hover, a.gmail-row-subject:hover { text-decoration:underline; }
.gmail-compose[href] { text-decoration:none; }

/* Added 2026-08-29 at the user's request: real folder switching (Inbox/
   Sent/Drafts/Starred/Important/Spam/Trash/All Mail, via
   gmail_site.render_inbox_panel_tabs) and minimize/maximize controls
   directly on the embedded Studio panel, so neither one requires leaving
   to the full /inbox page anymore. */
.gmail-panel-header { display:flex; align-items:center; justify-content:space-between; gap:12px; margin:0 0 6px; }
.gmail-panel-controls { display:flex; gap:6px; flex:none; }
.panel-ctrl-btn { background:var(--surface); border:1px solid var(--border); color:var(--text-muted); border-radius:6px; width:26px; height:26px; padding:0; display:inline-flex; align-items:center; justify-content:center; cursor:pointer; font-size:.95rem; line-height:1; font-family:inherit; }
.panel-ctrl-btn:hover { color:var(--text); border-color:var(--accent); }
.gmail-panel-tabs { display:flex; flex-wrap:wrap; gap:4px; margin:10px 0 14px; }
.gmail-panel-tab { font-family:'IBM Plex Mono',monospace; font-size:.7rem; letter-spacing:.03em; text-transform:uppercase; color:var(--text-muted); text-decoration:none; padding:6px 11px; border-radius:6px; border:1px solid var(--border); background:var(--surface); }
.gmail-panel-tab:hover { color:var(--text); border-color:var(--accent); }
.gmail-panel-tab.active { color:#fff; background:var(--accent); border-color:var(--accent); font-weight:600; }
/* Added 2026-08-29: a real search box inside the panel itself (POST
   /studio/panel_search), alongside — not instead of — the chat box. Mirrors
   /inbox's own .searchbox row but under the gmail-panel-* namespace since
   Studio's page shell doesn't load gmail_site.py's SHARED_CSS. */
.gmail-panel-search { display:flex; gap:8px; margin:0 0 12px; }
.gmail-panel-search input[type="text"] { flex:1; min-width:0; background:var(--surface); border:1px solid var(--border); color:var(--text); border-radius:8px; padding:9px 12px; font-size:.82rem; font-family:inherit; }
.gmail-panel-search input[type="text"]::placeholder { color:var(--text-muted); }
.gmail-panel-search input[type="text"]:focus { outline:none; border-color:var(--accent); }
.gmail-panel-search button { flex:none; background:var(--surface); border:1px solid var(--border); color:var(--text); border-radius:8px; padding:9px 14px; font-size:.78rem; font-weight:600; cursor:pointer; font-family:inherit; }
.gmail-panel-search button:hover { border-color:var(--accent); color:var(--accent); }
/* Added 2026-08-29: "improvement idea #1" — one-tap Unread/Starred/Important
   filter chips, alongside the folder tabs and search box rather than in
   place of either. Fully rounded pills (vs. the tabs' softer corners) so
   the two rows read as different kinds of control at a glance. */
.gmail-panel-chips { display:flex; flex-wrap:wrap; gap:6px; margin:0 0 14px; }
.gmail-panel-chip { font-size:.74rem; font-weight:500; color:var(--text-muted); text-decoration:none; padding:5px 13px; border-radius:999px; border:1px solid var(--border); background:transparent; }
.gmail-panel-chip:hover { color:var(--text); border-color:var(--accent); }
.gmail-panel-chip.active { color:#fff; background:var(--accent); border-color:var(--accent); font-weight:600; }
/* Added 2026-08-29 alongside studio.py's fetch_args-driven panel scoping:
   shows the exact real Gmail search a chat request just fetched with,
   since the panel can now be filtered by more than just a folder tab. */
.gmail-panel-query-hint { font-family:'IBM Plex Mono',monospace; font-size:.74rem; color:var(--text-muted); margin:0 0 12px; }
.gmail-panel-query-hint strong { color:var(--text); font-weight:500; }
.gmail-panel-query-hint a { color:var(--accent); text-decoration:none; margin-left:6px; }
.gmail-panel-query-hint a:hover { text-decoration:underline; }
/* Added 2026-08-29: the single-message view inlined directly into the
   Studio panel (gmail_site.render_inbox_panel_message) — the fix for
   "if we click the mail it's navigating to next page want to do
   everything in the studio front page." Same visual language as the
   full /inbox page's own .msg-detail/.msg-toolbar/.reply-box (see
   gmail_site.py's SHARED_CSS), rebuilt here under the gmail-panel-*
   namespace since Studio's page shell never loads that file's CSS. */
.gmail-panel-msg-toolbar { display:flex; gap:8px; margin-bottom:14px; flex-wrap:wrap; align-items:center; }
.gmail-panel-msg-btn { font-family:'IBM Plex Sans',sans-serif; font-weight:500; font-size:.78rem; background:var(--surface); border:1px solid var(--border); color:var(--text); border-radius:6px; padding:7px 12px; cursor:pointer; text-decoration:none; display:inline-flex; align-items:center; }
.gmail-panel-msg-btn:hover { border-color:var(--accent); color:var(--accent); }
.gmail-panel-msg-btn.active { color:#F0B429; border-color:#F0B429; }
.gmail-panel-msg-btn-danger:hover { border-color:#D9695F; color:#D9695F; }
.gmail-panel-msg-btn-primary { background:var(--accent); border-color:var(--accent); color:#fff; font-weight:600; }
.gmail-panel-msg-btn-primary:hover { color:#fff; opacity:.92; }
.gmail-panel-message { background:var(--surface); border:1px solid var(--border); border-radius:8px; padding:18px 20px; margin-bottom:14px; overflow:hidden; }
.gmail-panel-msg-subject { font-family:'Sora',sans-serif; font-weight:600; font-size:1rem; margin:0 0 6px; color:var(--text); }
.gmail-panel-msg-meta { font-size:.78rem; color:var(--text-muted); margin:0 0 14px; }
.gmail-panel-msg-body { font-size:.86rem; line-height:1.6; color:var(--text); border-top:1px solid var(--border); padding-top:12px; overflow-wrap:anywhere; }
.gmail-panel-notice { font-family:'IBM Plex Mono',monospace; font-size:.76rem; padding:8px 12px; border-radius:6px; margin:0 0 12px; }
.gmail-panel-notice-ok { background:rgba(95,206,154,.12); color:#5FCE9A; border:1px solid rgba(95,206,154,.35); }
.gmail-panel-notice-err { background:rgba(217,105,95,.12); color:#E8A5A0; border:1px solid rgba(217,105,95,.35); }
.gmail-panel-reply { background:var(--surface); border:1px solid var(--border); border-radius:8px; padding:16px 18px; }
.gmail-panel-reply-label { font-family:'Sora',sans-serif; font-weight:600; font-size:.86rem; margin:0 0 10px; color:var(--text); }
.gmail-panel-draft-row { display:flex; gap:8px; margin-bottom:10px; }
.gmail-panel-draft-row input[type=text] { flex:1; background:var(--ground,#0B1220); border:1px solid var(--border); border-radius:6px; padding:8px 11px; font-size:.8rem; color:var(--text); font-family:'IBM Plex Sans',sans-serif; }
.gmail-panel-draft-row input[type=text]:focus { outline:none; border-color:var(--accent); }
.gmail-panel-reply-textarea { width:100%; min-height:120px; resize:vertical; background:var(--ground,#0B1220); border:1px solid var(--border); border-radius:6px; padding:10px 12px; font-family:'IBM Plex Sans',sans-serif; font-size:.84rem; color:var(--text); }
.gmail-panel-reply-textarea:focus { outline:none; border-color:var(--accent); }
.gmail-panel-reply-textarea::placeholder { color:var(--text-muted); }
.gmail-panel-reply-send { margin-top:10px; display:flex; justify-content:flex-end; }
.gmail-panel-reply-hint { font-size:.7rem; color:var(--text-muted); margin:8px 0 0; }
/* _attachments_html()'s fixed class names (gmail_site.py), reused verbatim
   by render_inbox_panel_message() — same real attachment chips, styled to
   match the panel's own dark surface instead of gmail_site.py's SHARED_CSS
   version (unavailable on Studio's page shell). */
.msg-attachments { border-top:1px solid var(--border); margin-top:14px; padding-top:12px; }
.attachments-label { font-family:'IBM Plex Mono',monospace; font-size:.66rem; letter-spacing:.05em; text-transform:uppercase; color:var(--text-muted); margin:0 0 8px; }
.attachments-row { display:flex; flex-wrap:wrap; gap:8px; }
.attachment-chip { display:inline-flex; align-items:center; gap:6px; background:var(--ground,#0B1220); border:1px solid var(--border); border-radius:6px; padding:6px 10px; font-size:.76rem; color:var(--text); text-decoration:none; }
.attachment-chip:hover { border-color:var(--accent); color:var(--accent); }
.attachment-size { color:var(--text-muted); font-size:.7rem; white-space:nowrap; }
/* Minimized: collapse everything below the header, same as clicking a
   real window's minimize button — the header (title + controls) is the
   only thing still visible, so maximize/restore is still reachable. */
.gmail-panel-minimized .gmail-panel-tabs,
.gmail-panel-minimized .gmail-panel-search,
.gmail-panel-minimized .gmail-panel-chips,
.gmail-panel-minimized .preview-request,
.gmail-panel-minimized .app-window { display:none; }
/* Maximized: the panel lifts out of the normal chat+preview layout into a
   fixed full-viewport overlay — the same "give it the whole screen"
   affordance a real window's maximize button gives, scoped to just this
   panel rather than navigating away to the full /inbox page. */
.gmail-panel-maximized { position:fixed; inset:20px; z-index:60; background:var(--ground,#0B1220); border:1px solid var(--border); border-radius:12px; padding:22px 26px; overflow-y:auto; box-shadow:0 24px 64px rgba(0,0,0,.55); }
"""

# Tiny vanilla-JS toggle for the embedded Gmail panel's minimize/maximize
# controls above — added 2026-08-29 alongside them. Deliberately just a
# classList toggle, no framework and no server round-trip (unlike the
# panel's folder tabs, which are real page navigations): minimizing or
# maximizing the live preview isn't state anyone needs to persist or that
# the server needs to know about, so a client-only toggle is the honest
# match for what this actually is. Emitted inline next to the panel itself
# (studio.py's _preview_html) rather than added to GSAP_SCRIPT below, since
# it's specific to the Gmail workflow's preview, not every workflow's.
GMAIL_PANEL_TOGGLE_SCRIPT = """
<script>
function pilantTogglePanel(id, mode) {
  var el = document.getElementById(id);
  if (!el) return;
  if (mode === 'min') {
    el.classList.remove('gmail-panel-maximized');
    el.classList.toggle('gmail-panel-minimized');
  } else if (mode === 'max') {
    el.classList.remove('gmail-panel-minimized');
    el.classList.toggle('gmail-panel-maximized');
  }
}
</script>
"""

# Reshape controls only ever appear inside .app-window (a saved view being
# edited), so these use the --app-* tokens, same as every other component
# style above — kept as a separate string purely for readability.
EDITABLE_CSS = """
.reshape-toolbar { display:flex; align-items:center; gap:12px; margin:0 0 16px; flex-wrap:wrap; }
.reshape-toolbar button { font-family:'IBM Plex Sans',sans-serif; font-weight:600; font-size:.8rem; background:var(--app-accent); color:#fff; border:none; border-radius:6px; padding:8px 14px; cursor:pointer; }
.reshape-hint { font-size:.74rem; color:var(--app-muted); font-family:'IBM Plex Mono',monospace; }
.reshape-item { position:relative; border:1px dashed var(--app-border); border-radius:8px; padding:8px 8px 0; margin-bottom:10px; cursor:grab; }
.reshape-item:active { cursor:grabbing; }
.reshape-item.reshape-drag-over { border-color:var(--app-accent); }
.reshape-handle { display:flex; justify-content:space-between; align-items:center; font-size:.7rem; color:var(--app-muted); font-family:'IBM Plex Mono',monospace; padding:0 4px 6px; user-select:none; }
.reshape-remove { background:none; border:none; color:var(--app-muted); font-family:'IBM Plex Mono',monospace; font-size:.68rem; cursor:pointer; padding:2px 6px; }
.reshape-remove:hover { color:var(--app-critical); }
"""

# Added 2026-08-26 at the user's explicit request, after sourcing GreenSock's
# own official AI skills (github.com/greensock/gsap-skills, MIT licensed) —
# see the pilant-interface-motion skill for the full reasoning. A single,
# reusable entrance animation for whatever real content a render_view
# produced — never a per-connector or per-component bespoke script, since
# this same template renders arbitrary content from every connector.
#
# Deliberately targets only the LEAF-level repeating elements
# (.stat-card/.list-row/.gmail-row), not the .panel wrapper that usually
# contains a list's rows — animating both would double-fade the same
# content (the wrapper fades in, then its own children fade in again
# inside it), which reads as broken rather than polished. A lone detail
# panel (kv-grid fields, no rows) simply doesn't animate under this rule;
# that's an intentional trade for avoiding the double-fade case, which is
# far more common in this app's actual output.
#
# gsap.matchMedia() (not a one-off `window.matchMedia` check) is GreenSock's
# own recommended pattern for prefers-reduced-motion — see gsap-core's
# skill. autoAlpha (not opacity) so a not-yet-animated row can't block
# clicks before its tween runs. Only transform (`y`) + autoAlpha are
# animated — never layout properties — per gsap-performance's own guidance.
GSAP_SCRIPT = (
    '<script src="https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.5/gsap.min.js"></script>'
    "<script>(function(){"
    "if (typeof gsap === 'undefined') return;"
    "var targets = '.stat-card, .list-row, .gmail-row';"
    "var mm = gsap.matchMedia();"
    "mm.add('(prefers-reduced-motion: reduce)', function(){"
    "gsap.set(targets, {autoAlpha: 1, y: 0});"
    "});"
    "mm.add('(prefers-reduced-motion: no-preference)', function(){"
    "gsap.from(targets, {autoAlpha: 0, y: 14, duration: 0.4, ease: 'power2.out', "
    "stagger: {each: 0.06, from: 'start'}});"
    "});"
    "})();</script>"
)

PAGE_TEMPLATE = (
    "<!doctype html><html><head><meta charset=\"utf-8\"><title>Pilant — generated view</title>"
    "<link rel=\"preconnect\" href=\"https://fonts.googleapis.com\">"
    "<link rel=\"stylesheet\" href=\"https://fonts.googleapis.com/css2?family=Sora:wght@600;700&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap\">"
    "<style>{{CSS}}</style></head><body><div class=\"page\">"
    "{{NAV}}"
    "<p class=\"eyebrow\">Pilant &middot; generated view</p>"
    "<p class=\"request\">&ldquo;{{REQUEST}}&rdquo;</p>"
    "<div class=\"device\"><div class=\"device-bar\"><span></span><span></span><span></span></div>"
    "<div class=\"app-window\"><h2 class=\"app-h1\">{{HEADING}}</h2>{{META}}{{COMPONENTS}}</div></div>"
    "<p class=\"footer\">Generated live by the Composition Engine — not scripted.</p>"
    "</div>" + GSAP_SCRIPT + "</body></html>"
)


# Added 2026-09-01 for the merged-screen "feed dashboard" redesign — real,
# working source filter chips above a merged screen's per-app list sections
# (the reference the user shared showed a chip row: "All Combined / Gmail /
# Slack Mentions / Jira Assigned / ..."). Deliberately client-side only: all
# the data for every chip is already sitting in the page (this same
# render_view already fetched and rendered every app's rows), so filtering
# which section is VISIBLE needs no new fetch, no server round-trip, and
# can't show anything that wasn't already real, guardrail-checked content.
# Only built when there are 2+ TITLED list components — a single-app screen
# has nothing to filter between, so this silently renders nothing for every
# single-connector workflow, exactly as before this existed.
def _filter_chip_bar_html(titled_lists, connector_colors):
    total = sum(len(c.get("rows") or []) for c in titled_lists)
    chips = [f'<button type="button" class="feed-chip active" data-filter="all">All combined <span class="feed-chip-count">{total}</span></button>']
    for c in titled_lists:
        title = c.get("title")
        slug = _slugify(title)
        count = len(c.get("rows") or [])
        color = _connector_color(title, connector_colors)
        dot_html = f'<span class="feed-source-dot" style="background:{color["fg"]}"></span>' if color else ""
        chips.append(
            f'<button type="button" class="feed-chip" data-filter="{_esc(slug)}">'
            f'{dot_html}{_esc(title)} <span class="feed-chip-count">{count}</span></button>'
        )
    return f'<div class="feed-chip-bar">{"".join(chips)}</div>'


# Plain vanilla JS, same "no framework, no server round-trip" pattern as
# GMAIL_PANEL_TOGGLE_SCRIPT above — toggles which .feed-block sections are
# visible based on which chip is active. Scoped to the nearest common
# ancestor of the chip bar and its blocks (rather than the whole document)
# so this is safe even if a page somehow embeds more than one rendered
# screen — added defensively, though studio.py never does that today.
FEED_FILTER_SCRIPT = (
    "<script>(function(){"
    "document.querySelectorAll('.feed-chip-bar').forEach(function(bar){"
    "bar.addEventListener('click', function(e){"
    "var btn = e.target.closest('.feed-chip');"
    "if (!btn) return;"
    "var filter = btn.getAttribute('data-filter');"
    "bar.querySelectorAll('.feed-chip').forEach(function(b){ b.classList.toggle('active', b === btn); });"
    "var scope = bar.parentElement || document;"
    "scope.querySelectorAll('.feed-block').forEach(function(block){"
    "var show = (filter === 'all' || block.getAttribute('data-src') === filter);"
    "block.style.display = show ? '' : 'none';"
    "});"
    "});"
    "});"
    "})();</script>"
)


def _render_body(view, connector_links=None, connector_colors=None, jira_detail_base=None):
    """Shared by render_fragment() and render_html(): heading + meta + an
    optional filter-chip bar (only when 2+ titled list components make one
    meaningful) + every component. Factored out 2026-09-01 so both entry
    points get the same chip-bar behavior instead of render_html() silently
    missing a feature only render_fragment() got."""
    heading = _esc(view.get("heading", ""))
    meta = view.get("meta")
    meta_html = f'<p class="app-meta">{_esc(meta)}</p>' if meta else ""
    components = view.get("components", [])
    titled_lists = [c for c in components if c.get("type") == "list" and c.get("title")]
    chip_html = _filter_chip_bar_html(titled_lists, connector_colors) if len(titled_lists) >= 2 else ""
    components_html = "".join(_component_html(c, connector_links, connector_colors, jira_detail_base) for c in components)
    script_html = FEED_FILTER_SCRIPT if chip_html else ""
    return f'<h2 class="app-h1">{heading}</h2>{meta_html}{chip_html}{components_html}{script_html}'


def render_fragment(view, connector_links=None, connector_colors=None, jira_detail_base=None):
    """
    Same component rendering as render_html(), but returns only the inner
    content (heading + meta + components) — no <html> shell, no CSS, no
    device chrome. For embedding inside a host page that already has its
    own <html>/<head> (see retail_site.py) — the caller is responsible for
    including CSS (the module-level CSS string above) once on their page
    so the .stat-grid/.panel/.list-row/.badge classes render correctly.

    connector_links: optional {lowercased connector label: url} map — see
    _list_title_html's docstring. None (the default, and what every caller
    except studio.py's live-preview panel passes) means every list title
    renders as plain text, exactly as before this existed.

    jira_detail_base: optional url prefix (studio.py passes
    "/studio?jira_detail=") — see _component_html's list-branch docstring.
    None (the default) means Jira rows render exactly as before this
    existed, with no detail-panel link.

    connector_colors: optional {lowercased connector label: {"bg","fg"}} map
    — see _connector_color's docstring. Drives the small source-color dot
    next to a titled list's heading/chip, and (with 2+ titled lists) the
    filter-chip bar above them. None (the default) renders exactly as
    before this existed.
    """
    return _render_body(view, connector_links, connector_colors, jira_detail_base)


def _chat_inline_component_html(c, max_rows=3):
    """Compact, chat-bubble-sized rendering of one render_view component —
    used by render_chat_inline_result below for "improvement idea #3"
    (inline per-turn chat history results, added 2026-08-29). Unlike
    _component_html/_gmail_component_html (the full-size live-preview
    renderers), this never shows more than `max_rows` rows/fields/stats —
    the point is a quick, scannable snapshot of what THAT turn found; the
    real live preview panel (or, for a Gmail workflow, the full /inbox
    page) is still where you'd go to see everything or take a real action
    like star/delete."""
    ctype = c.get("type")

    if ctype == "list":
        rows = c.get("rows") or []
        # title_html: added 2026-08-31 — this branch used to drop a list
        # component's 'title' entirely, so a merged 'All apps' screen with
        # several list components (one per app — see agent_unified.py's
        # SYSTEM prompt) rendered here as one continuous unlabeled run of
        # items with no indication which app each one came from, even
        # though the live preview panel (render_fragment, via
        # _list_title_html) shows each section clearly labeled. Plain text
        # here, not a clickable link like the live preview's — this card is
        # a compact, read-only history snapshot (see this function's own
        # docstring), not where you'd take an action.
        title_html = f'<p class="chat-result-section">{_esc(c.get("title"))}</p>' if c.get("title") else ""
        if not rows:
            return f'{title_html}<p class="chat-result-empty">Nothing matched.</p>'
        shown = rows[:max_rows]
        items = "".join(
            '<li>'
            f'<span class="chat-result-name">{_esc(r.get("name"))}</span>'
            + (f'<span class="chat-result-note">{_esc(r.get("note"))}</span>' if r.get("note") else "")
            + '</li>'
            for r in shown
        )
        more = len(rows) - len(shown)
        more_html = f'<p class="chat-result-more">+{more} more &mdash; see live preview</p>' if more > 0 else ""
        return f'{title_html}<ul class="chat-result-rows">{items}</ul>{more_html}'

    if ctype == "stat_grid":
        stats = (c.get("stats") or [])[:max_rows]
        if not stats:
            return ""
        chips = "".join(
            f'<span class="chat-result-stat">{_esc(s.get("label"))}: <strong>{_esc(s.get("value"))}</strong></span>'
            for s in stats
        )
        return f'<div class="chat-result-stats">{chips}</div>'

    if ctype == "panel":
        title_html = f'<p class="chat-result-name">{_esc(c.get("title"))}</p>' if c.get("title") else ""
        fields = (c.get("fields") or [])[:max_rows]
        items = "".join(
            f'<li><span class="chat-result-note">{_esc(f.get("label"))}:</span> {_esc(f.get("value"))}</li>'
            for f in fields
        )
        rows_html = f'<ul class="chat-result-rows">{items}</ul>' if items else ""
        return f'{title_html}{rows_html}'

    if ctype == "suggestions":
        items = [s for s in (c.get("suggestions") or []) if isinstance(s, str) and s.strip()][:max_rows]
        if not items:
            return ""
        rows = "".join(f"<li>{_esc(s)}</li>" for s in items)
        return f'<p class="chat-result-name">Suggested</p><ul class="chat-result-rows">{rows}</ul>'

    return ""


def render_chat_inline_result(view, max_rows=3):
    """"Improvement idea #3" from the interface-comparison discussion
    (2026-08-29): a compact snapshot of what THIS turn actually built,
    meant to render directly under that turn's own chat bubble — so
    scrolling back through the conversation shows a real history of
    results, not just whatever the single live-preview panel currently
    happens to show (which only ever reflects the LATEST turn, and for a
    Gmail workflow specifically is real live data that can keep changing
    after the fact — see studio.py's _preview_html docstring).

    This is a read-only, storytelling snapshot of the SAME
    guardrail-verified real data render_view already produced — never a
    second, separately-fetched copy — capped to a handful of rows per
    component (max_rows) so it stays chat-sized. The live preview panel
    (or, for Gmail, the full /inbox page) is still where you'd actually go
    to see everything or take a real action.

    Returns "" for a view with no components worth summarizing (an
    empty/heading-only render, or every component rendering blank) so
    studio.py never shows an empty card under a bubble."""
    heading = _esc(view.get("heading", ""))
    components_html = "".join(
        _chat_inline_component_html(c, max_rows) for c in (view.get("components") or [])
    )
    if not components_html.strip():
        return ""
    heading_html = f'<p class="chat-result-heading">{heading}</p>' if heading else ""
    return f'<div class="chat-result-card">{heading_html}{components_html}</div>'


def render_editable_fragment(view, save_action):
    """
    Like render_fragment(), but wraps each top-level component in a
    draggable container with a "remove" control, plus a toolbar with a
    "Save layout" button. This is the actual "reshape it yourself" piece
    that plain render_fragment()/render_html() don't have — the user, not
    the model, decides the final order and which components stay.

    Only meaningful for a SAVED view (server.py's /view/<id>): that's the
    only place a reshape has somewhere real to persist to (save_action
    posts to /views/<id>/layout). Drag/drop is plain HTML5
    (draggable + dragstart/dragover/drop) — no external library, same
    "just fetch/vanilla JS" approach as every other page in this project.
    """
    heading = _esc(view.get("heading", ""))
    meta = view.get("meta")
    meta_html = f'<p class="app-meta">{_esc(meta)}</p>' if meta else ""
    components = view.get("components", [])

    items_html = "".join(
        f'<div class="reshape-item" draggable="true" data-index="{i}">'
        f'<div class="reshape-handle"><span>&#8942;&#8942; drag to reorder</span>'
        f'<button type="button" class="reshape-remove" data-index="{i}">remove</button></div>'
        f'{_component_html(c)}'
        f'</div>'
        for i, c in enumerate(components)
    )

    toolbar_html = (
        f'<form method="post" action="{_esc(save_action)}" id="reshape-form" class="reshape-toolbar">'
        '<input type="hidden" name="order" id="reshape-order" value="">'
        f'<input type="hidden" name="total" value="{len(components)}">'
        '<button type="submit">Save layout</button>'
        '<span class="reshape-hint">Drag to reorder, remove to hide, then save — applies next time you open this view too</span>'
        '</form>'
    )

    script = (
        "<script>"
        "(function(){"
        "var container = document.getElementById('reshape-items');"
        "if (!container) return;"
        "var orderInput = document.getElementById('reshape-order');"
        "var dragEl = null;"
        "function currentOrder() {"
        "  return Array.prototype.map.call(container.children, function(el){ return el.dataset.index; }).join(',');"
        "}"
        "function refreshOrder() { orderInput.value = currentOrder(); }"
        "container.addEventListener('dragstart', function(e){"
        "  dragEl = e.target.closest('.reshape-item');"
        "  e.dataTransfer.effectAllowed = 'move';"
        "});"
        "container.addEventListener('dragover', function(e){"
        "  e.preventDefault();"
        "  var target = e.target.closest('.reshape-item');"
        "  if (!target || target === dragEl) return;"
        "  var rect = target.getBoundingClientRect();"
        "  var putAfter = (e.clientY - rect.top) / rect.height > 0.5;"
        "  container.insertBefore(dragEl, putAfter ? target.nextSibling : target);"
        "});"
        "container.addEventListener('drop', function(e){ e.preventDefault(); refreshOrder(); });"
        "container.addEventListener('click', function(e){"
        "  if (e.target.classList.contains('reshape-remove')) {"
        "    e.target.closest('.reshape-item').remove();"
        "    refreshOrder();"
        "  }"
        "});"
        "document.getElementById('reshape-form').addEventListener('submit', refreshOrder);"
        "refreshOrder();"
        "})();"
        "</script>"
    )

    return (
        f'<h2 class="app-h1">{heading}</h2>{meta_html}'
        f'{toolbar_html}'
        f'<div id="reshape-items">{items_html}</div>'
        f'{script}'
    )


def render_editable_html(view, request_text, nav_html, save_action):
    """
    Like render_html(), but the rendered screen is a live drag-to-reorder
    / remove-to-hide editor (render_editable_fragment) instead of static
    output. Everything else — schema, guardrails, regenerating from live
    data on every open — is identical to render_html(); only the
    components region changes.
    """
    body = render_editable_fragment(view, save_action)
    out = PAGE_TEMPLATE
    out = out.replace("{{CSS}}", CSS + EDITABLE_CSS)
    out = out.replace("{{NAV}}", nav_html)
    out = out.replace("{{REQUEST}}", _esc(request_text))
    out = out.replace('<h2 class="app-h1">{{HEADING}}</h2>{{META}}{{COMPONENTS}}', body)
    return out


def render_html(view, request_text="", nav_html="", connector_links=None, connector_colors=None):
    """
    nav_html: optional raw HTML inserted above the eyebrow line — used by
    server.py to add a "back to a new query" link when this is rendered as
    part of a live web app instead of a one-off CLI output file.

    connector_links/connector_colors: same as render_fragment() — both None
    by default, which renders exactly as before either feature existed.
    """
    heading = _esc(view.get("heading", ""))
    meta = view.get("meta")
    meta_html = f'<p class="app-meta">{_esc(meta)}</p>' if meta else ""
    components = view.get("components", [])
    titled_lists = [c for c in components if c.get("type") == "list" and c.get("title")]
    chip_html = _filter_chip_bar_html(titled_lists, connector_colors) if len(titled_lists) >= 2 else ""
    components_html = "".join(_component_html(c, connector_links, connector_colors) for c in components)
    script_html = FEED_FILTER_SCRIPT if chip_html else ""

    out = PAGE_TEMPLATE
    out = out.replace("{{CSS}}", CSS)
    out = out.replace("{{NAV}}", nav_html)
    out = out.replace("{{REQUEST}}", _esc(request_text))
    out = out.replace("{{HEADING}}", heading)
    out = out.replace("{{META}}", meta_html)
    out = out.replace("{{COMPONENTS}}", chip_html + components_html + script_html)
    return out


if __name__ == "__main__":
    # Quick self-test with a sample view shaped like a real agent output,
    # so this can be sanity-checked without hitting any API.
    sample = {
        "heading": "Pending approvals",
        "meta": "Awaiting your action",
        "components": [
            {
                "type": "list",
                "title": "Approvals awaiting your decision",
                "rows": [
                    {
                        "name": "Block suspicious IP address",
                        "note": "Exploiting credentials detected 4 hours ago",
                        "badge": {"text": "senior approval", "tone": "default"},
                    }
                ],
            }
        ],
    }
    with open("output_test.html", "w") as f:
        f.write(render_html(sample, "what's waiting on my approval right now?"))
    print("wrote output_test.html")
