"""
Pilant Studio — the "one Studio for all apps" shell discussed with the user
on 2026-08-25, modeled after the DynamisOS pattern they showed (screenshot):
one chat panel, one live-preview pane, and a sidebar list of saved
"workflows" — each workflow is a generated screen tied to a plain-language
request, backed by a real connector. This replaces the "different site per
app" pattern (gmail_site.py, retail_site.py, ...) with the "one interface
for everything" pattern the 500+-apps pitch actually describes: one shell,
one entry point, the connector underneath changes, the interface around it
doesn't.

SCOPE, stated plainly: four connectors are wired in — Gmail
(agent_gmail.py), Slack (agent_slack.py), and, added 2026-08-25, GitHub
(agent_github.py) and Helpdesk (agent_helpdesk.py) — all ported to the same
hardened reliability layer Gmail proved out live: JSON-truncation/quote-
escaping recovery, plain-text tool-call recovery, and the fabrication/
placeholder guardrails (see each file's own module docstring/comments for
its port notes; GitHub also threads a `user` through to scope_issues() for
label-based identity scoping — see CONNECTORS' needs_user flag and
studio_message() below). A fifth, Healthcare (agent_healthcare.py), was
ported the same way but deliberately removed from CONNECTORS on
2026-08-25 at the user's request — the file's still on disk, untouched,
in case it's wanted back later, it's just not wired into this shell.
CONNECTORS below is a small registry specifically so adding another app
later means adding one more entry, not restructuring this file. A new
workflow started from the plain "+ New workflow" button in the sidebar
still has no connector chosen yet — per the DynamisOS pattern this whole
shell is modeled on, the chat itself asks "which app would you like to
connect?" (see _chat_html's empty-connector branch and studio_message's
connector-selection branch below), and the first message the person sends
is read as their answer (matched against CONNECTORS' keys/labels), not
forwarded to any agent.

INTEGRATIONS PAGE, added/redesigned 2026-08-25: originally this was a
second column of clickable icons living inside the same /studio shell —
the user rejected that ("not like this") in favor of the pattern their
DynamisOS reference actually uses: a plain nav link, "Integrations",
underneath the workflow list in the sidebar (see _sidebar_html below),
that navigates to its own page (GET /integrations, render_integrations
below) — cards, one per connector, not a column of icons crammed next to
the chat. For Gmail specifically, the user wants what "connect" actually
means for a real Google account: clicking Connect Gmail redirects the
browser to Google's own real sign-in page, the person enters their actual
Gmail username and password THERE (never typed into anything this app
controls), Google redirects back with an authorization code, and this app
exchanges that for a refresh token — the standard OAuth2 authorization-
code flow, now implemented for real in connectors_gmail.py's
get_authorization_url/exchange_code_for_tokens (see /oauth/gmail/connect
and /oauth/gmail/callback below). This is what makes "connect a DIFFERENT
Gmail account" actually work: signing in again replaces which refresh
token this connector uses, in memory, no .env edit or restart required —
see connectors_gmail.py's _connected for the mechanism. Slack, GitHub, and
Helpdesk don't get this treatment yet — they still run on static server-
side credentials from .env, same as before; their Integrations cards just
say so and offer a "Start a workflow" shortcut instead of a real connect
flow. Don't describe this as "500+ apps, live" — it's the one-Studio
architecture, proven with four real connectors and one real OAuth
connection flow.

RELIABILITY, stated plainly too: unlike gmail_site.py's inbox (which
deliberately avoids the render_view tool-calling loop entirely — see that
file's docstring for why), a Studio that builds a custom screen from a
free-form description has no way around needing that loop; "compose an
interface from what I asked for" is exactly what render_view exists to do.
So this file reuses agent_gmail.run_agent() as-is, with every reliability
fix already built into it (max_tokens=2000, the ast.literal_eval JSON
recovery, the fabrication/placeholder guardrails) — the most reliable
version of that engine anywhere in this project. It can still occasionally
fail on an ambiguous or complex request (the same "hit max_steps" failure
gmail_site.py's old /copilot box hit, back when this file's engine was
wired into that file instead). The UI here is built to fail SAFELY when
that happens: a failed request shows a plain error in the chat and leaves
the last successful preview exactly as it was on the right — never a
stale/blank panel (see gmail_site.py's docstring for the exact bug this is
deliberately avoiding).

STORAGE, stated plainly too: workflows live in an in-memory Python
dict/list at module level (WORKFLOWS / WORKFLOW_ORDER below), not a
database or a file. Restarting this process loses every saved workflow.
That's a fine, honest limitation for a first working version proving the
architecture — swapping it for real persistence is a separate, later step
once the shell itself is proven out, not a detail to solve here.

No per-user scoping of workflows in this first version either, same
reasoning as gmail_site.py/agent_gmail.py: whoever logs in shares the one
set of workflows and the one Gmail inbox this is wired to.
"""

import html as _html
import os
import re
import secrets
import string
import sys
from collections import Counter
from functools import wraps
from pathlib import Path
from urllib.parse import urlencode

from flask import Flask, request, session, redirect, url_for, jsonify, send_from_directory, abort

import connectors_gmail
import connectors_jira
# Modules imported by name (not just run_agent) too, added 2026-08-25 for the
# cross-request conversation memory feature below — each one's SYSTEM
# constant is what memory gets re-seeded with when a brand-new top-level
# request resumes a workflow's persisted conversation. See CONNECTORS'
# "system" key and _append_memory_exchange().
import agent_gmail
import agent_slack
import agent_github
import agent_helpdesk
import agent_jira
import agent_custom
from agent_gmail import run_agent as gmail_run_agent
from agent_slack import run_agent as slack_run_agent
from agent_github import run_agent as github_run_agent
from agent_helpdesk import run_agent as helpdesk_run_agent
from agent_jira import run_agent as jira_run_agent
from agent_custom import run_agent as custom_run_agent
import agent_unified
from agent_unified import run_agent as unified_run_agent
from renderer import (
    CSS as APP_CSS, GMAIL_CSS, GMAIL_PANEL_TOGGLE_SCRIPT, GSAP_SCRIPT,
    render_fragment, render_chat_inline_result,
)
from users import get_user, verify_login
# The real, interactive Gmail inbox (browse/reply/compose/delete/star/
# forward) — added 2026-08-24 as its own standalone app, MERGED into this
# one 2026-08-26 at the user's explicit request ("merge into Studio, one
# app, port 5008") after they tried those actions from Studio's chat page
# and correctly found they weren't there. gmail_site.py is now a Blueprint,
# not its own Flask app — see that file's module docstring for the full
# rationale and what changed. Registered with NO url_prefix below (its
# routes — /inbox, /compose, /message/<id>, etc. — don't collide with any
# route in this file), so it's reachable at exactly the same paths it used
# to have on its old standalone port.
from gmail_site import (
    gmail_bp, render_inbox_panel, render_inbox_panel_tabs,
    render_inbox_panel_chips, render_inbox_panel_message,
    resolve_panel_folder, resolve_panel_query, resolve_panel_chip,
)
# Local RAG (retrieval-augmented generation) knowledge base — added
# 2026-08-26 at the user's explicit request ("do I want to buy agentic rag
# or you can built?" -> built directly here). rag_index.py owns the Chroma
# vector store + Bedrock embeddings; rag_ingest.py turns each connector's
# live data into what rag_index.py expects to index. This file's job is
# just the "Sync knowledge base" section on the Integrations page (POST
# /rag/sync, see _knowledge_base_section_html() below) that keeps the index
# fresh. There used to also be a standalone Search page (GET /rag/search,
# via agent_search.py) — removed 2026-08-26 at the user's request, since
# search_knowledge_base is already available as a tool inside every
# connector's own chat (agent_gmail.py/agent_slack.py/agent_github.py) and
# having a second, connector-less place to search the same index was
# redundant rather than additive. Sync still lives here; searching now only
# happens inside a connector's workflow chat, same as any other tool call.
import rag_index
import rag_ingest
# The Composer — added 2026-08-26, building toward "the full DynamisOS
# product" at the user's explicit request (see agent_composer.py's and
# primitives.py's module docstrings for the full reasoning). Reuses
# saved_views.py, which already existed but was only wired into the OLD,
# now-superseded server.py app (port 5001) — this is its first use inside
# Studio itself.
import agent_composer
import connectors_retail
from primitives import REGISTRY
from saved_views import list_views, get_view, save_view, delete_view
import user_style
import layout_usage

env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)

# The real interactive Gmail inbox — see the import comment above and
# gmail_site.py's module docstring. No url_prefix: its routes keep their
# original paths (/inbox, /compose, /message/<id>, ...), which don't
# collide with anything already registered directly on `app` below.
app.register_blueprint(gmail_bp)

# Fixed on purpose, added 2026-08-25 after a live redirect_uri_mismatch:
# Google's OAuth console requires the redirect URI it sends the browser
# back to be registered EXACTLY (this isn't the loopback-IP exemption
# working the way the original comment here assumed — Google still
# rejected it) so this has to be one fixed, predictable string rather than
# whatever hostname (localhost vs 127.0.0.1) happens to be in the address
# bar when someone clicks "Connect Gmail". PORT must match app.run()'s
# port at the bottom of this file. Whoever is using Studio needs to always
# reach it via this exact origin (http://127.0.0.1:5008, not
# http://localhost:5008) for the whole session — logging in on one and
# hitting the OAuth callback on the other would look like two different
# sites to the browser and the Flask session cookie wouldn't carry over —
# and needs to add GMAIL_OAUTH_REDIRECT_URI below to their OAuth client's
# "Authorized redirect URIs" list in Google Cloud Console (APIs & Services
# -> Credentials -> the OAuth 2.0 Client ID -> Authorized redirect URIs ->
# Add URI) before "Connect Gmail" will work — see connectors_gmail.py's
# module docstring for the rest of that one-time setup.
PORT = 5008
GMAIL_OAUTH_REDIRECT_URI = f"http://127.0.0.1:{PORT}/oauth/gmail/callback"
# Where the built React app lives — see the "JSON API (React frontend)"
# section near the end of this file (_serve_spa/spa_assets). Built via
# `cd frontend && npm run build`, output to frontend/dist by vite.config.js.
FRONTEND_DIST = Path(__file__).parent / "frontend" / "dist"


def _esc(s):
    return _html.escape(str(s)) if s is not None else ""


_CHAT_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_CHAT_NUMBERED_LINE_RE = re.compile(r"^\d+\.\s+(.*)$")


def _render_chat_markdown(text):
    """
    Minimal, SAFE markdown-ish rendering for chat bubbles — added 2026-08-25
    so a clarifying question with a bold intro and a numbered 1-4 list (the
    DynamisOS-style format agent_custom.py's SYSTEM prompt now asks the
    model for — see that file's SYSTEM/TOOLS) actually RENDERS that way
    instead of showing up as one flat run-on paragraph of literal asterisks
    and digits, which is all the old plain _esc()-only rendering could ever
    produce.

    Deliberately not a general markdown library, and deliberately not
    "render whatever HTML the model writes": the raw text is HTML-escaped
    FIRST via _esc() (so real '<', '>', '&', quotes in the model's text can
    never become live tags/attributes no matter what the model writes —
    the model's own output is treated as untrusted here just like a user's
    would be), and only AFTER that does this function ever wrap pieces of
    the now-inert, already-escaped text in a small fixed set of safe tags
    (<p>, <br>, <ol>, <li>, <strong>). There is no code path from raw
    assistant text to an unescaped '<' ever reaching the page.

    Supports exactly two things, matching what the custom connector's
    clarifying questions actually use: **bold** spans, and numbered list
    lines ("1. ...", "2. ...") which get grouped into a real <ol>. Any other
    line becomes its own paragraph; blank lines separate paragraphs.
    """
    escaped = _esc(text or "")
    lines = escaped.split("\n")

    html_parts = []
    list_buffer = []
    paragraph_buffer = []

    def flush_list():
        if list_buffer:
            html_parts.append("<ol>" + "".join(f"<li>{item}</li>" for item in list_buffer) + "</ol>")
            list_buffer.clear()

    def flush_paragraph():
        if paragraph_buffer:
            html_parts.append("<p>" + "<br>".join(paragraph_buffer) + "</p>")
            paragraph_buffer.clear()

    for raw_line in lines:
        line = raw_line.strip()
        m = _CHAT_NUMBERED_LINE_RE.match(line)
        if m:
            flush_paragraph()
            list_buffer.append(_CHAT_BOLD_RE.sub(r"<strong>\1</strong>", m.group(1)))
            continue
        flush_list()
        if line == "":
            flush_paragraph()
            continue
        paragraph_buffer.append(_CHAT_BOLD_RE.sub(r"<strong>\1</strong>", line))

    flush_list()
    flush_paragraph()
    return "".join(html_parts)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("username"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


# --- Connector registry ---------------------------------------------------
# One entry per connector this Studio can build workflows against — see the
# module docstring for which are wired up. Adding another one means adding
# one more entry here (its own run_agent-shaped function: same
# clarify/error/render/text result shape agent_gmail.run_agent already
# returns) plus a matching option in the "+ New workflow" picker below.
# Nothing else in this file should need to change for that.
# "system" below (added 2026-08-25, for the cross-request conversation
# memory feature) is each connector's own module-level SYSTEM prompt
# constant — it's what a workflow's persisted memory gets seeded with when
# a brand-new top-level request resumes a conversation that has no
# in-progress clarify state of its own to inherit a system message from.
# See _append_memory_exchange() below.
# "first_turn": True marks a connector whose run_agent() accepts the
# first_turn kwarg added 2026-08-26 (see agent_gmail.py's module-level
# FIRST_TURN_CLARIFY_PARAGRAPH docstring for the full reasoning: a bundled,
# DynamisOS-style purpose+style question asked once, proactively, before a
# brand-new workflow's very first fetch/render — not just reactively when a
# request happens to be ambiguous). Only the four REAL data connectors get
# it here; agent_custom.py is deliberately excluded — it already got its
# own DynamisOS-style clarifying flow on 2026-08-25 (a 4-item checklist
# covering purpose/data/style/tools, gated on a short/vague request rather
# than on "is this the first turn") which predates this change and covers
# the exact same free-form "describe an app" scenario DynamisOS's own
# reference screenshots show — passing it a first_turn kwarg it doesn't
# accept would just raise a TypeError, and it doesn't need this mechanism
# anyway.
CONNECTORS = {
    "gmail": {
        "label": "Gmail", "icon": "✉️", "run_agent": gmail_run_agent, "oauth": True,
        "system": agent_gmail.SYSTEM, "first_turn": True, "category": "email",
        "description": "Read, search, and act on your real Gmail inbox from Studio's chat.",
    },
    "slack": {
        "label": "Slack", "icon": "💬", "run_agent": slack_run_agent,
        "system": agent_slack.SYSTEM, "first_turn": True, "category": "messaging",
        "description": "Ask about your connected Slack channel and get real messages summarized.",
    },
    "github": {
        "label": "GitHub", "icon": "🐙", "run_agent": github_run_agent, "needs_user": True,
        "system": agent_github.SYSTEM, "first_turn": True, "category": "developer",
        "description": "Track and triage your real GitHub issues from one chat.",
    },
    "helpdesk": {
        "label": "Helpdesk", "icon": "🎫", "run_agent": helpdesk_run_agent,
        "system": agent_helpdesk.SYSTEM, "first_turn": True, "category": "support",
        "description": "See and prioritize your real support tickets from one chat.",
    },
    # Added 2026-08-31 at the user's request for "fake Jira with a crowded
    # interface" — same fully static/dummy shape as "helpdesk" above (see
    # connectors_jira.py's docstring). The "crowded" part is deliberate: its
    # ISSUES carry a dozen-plus fields each, so a generated screen that shows
    # only the 2-4 fields a request actually needs is a live demonstration
    # of Pilant's whole pitch, not just a claim about it.
    "jira": {
        "label": "Jira", "icon": "📋", "run_agent": jira_run_agent,
        "system": agent_jira.SYSTEM, "first_turn": True, "category": "developer",
        "description": "Cut through a crowded Jira-style board — only the fields your request needs.",
    },
    # Added 2026-08-31, the "mixed apps" second tier discussed with the user
    # alongside the four single-app connectors above: one chat with access
    # to Gmail/Slack/GitHub/Helpdesk at once, deciding per-request which are
    # actually relevant and merging their real results into one screen (see
    # agent_unified.py's module docstring for the full design, including
    # exactly how "app isn't connected" is detected and surfaced).
    # "first_turn": True added the SAME day, reversing the original "no
    # first_turn on purpose" decision — see agent_unified.py's docstring's
    # "FIRST_TURN" section for the full story: a bare "connect gmail and
    # jira" first message (studio_message()'s multi-app routing, see
    # _match_all_connectors) used to skip straight to a guessed build with
    # no question at all, which the user flagged directly ("not giving
    # follow-up question") after seeing every single-app connector ask one
    # bundled purpose+layout question on ITS first turn. This connector
    # still never asks WHICH app to use, even now — only purpose/layout,
    # same as before.
    "unified": {
        "label": "All apps", "icon": "🧭", "run_agent": unified_run_agent,
        "system": agent_unified.system_snapshot(), "first_turn": True, "category": "overview",
        "description": "One chat across everything connected — Gmail, Slack, GitHub, Helpdesk, Jira — merged into one screen per request.",
    },
    # Added 2026-08-25 (DynamisOS reference screenshot): not a real data
    # connector like the four above — no backend, no OAuth, nothing to
    # "connect". "freeform": True marks it so _connector_list_text() and
    # _integrations_page_html() skip it — it isn't something you pick by
    # name off a list of real integrations, it's the fallback for typing a
    # free-form app/brand description straight into a new workflow (see
    # studio_message()'s connector-selection branch below). Still registered
    # here, not kept separate, so every other CONNECTORS[...] lookup in this
    # file (sidebar label, chat header, agent dispatch) keeps working
    # unchanged for it. No "first_turn" here on purpose — see the comment
    # above CONNECTORS.
    "custom": {"label": "Custom interface", "icon": "🎨", "run_agent": custom_run_agent, "freeform": True, "system": agent_custom.SYSTEM},
}
DEFAULT_CONNECTOR = "gmail"

# Added 2026-08-31 at the user's request ("If we click Gmail. It should
# navigate to the Gmail interface ... make it as a workflow"): a {lowercased
# connector label: "/studio/new/<key>"} map, passed into
# renderer.render_fragment()'s connector_links parameter so a 'list'
# component's title that names a real connector (this is exactly what
# agent_unified.py's SYSTEM prompt does when merging more than one app's
# results — see its "MERGED RENDERING" docstring section: one 'list'
# component PER app, titled with that app's name) renders as a clickable
# link instead of plain text. Clicking it POSTs to the SAME
# /studio/new/<connector_key> route the Integrations page's "Start a
# workflow" button already uses, so it opens a real, brand-new workflow
# scoped to that one app — not a fake navigation, an actual new entry in
# the WORKFLOWS sidebar list, the same as if you'd clicked that app's own
# Integrations card. "custom" is excluded (freeform, not a real target to
# link to — same reasoning CONNECTORS' own freeform comment gives).
_CONNECTOR_LINKS_BY_LABEL = {
    cfg["label"].strip().lower(): f"/studio/new/{key}"
    for key, cfg in CONNECTORS.items()
    if not cfg.get("freeform")
}

# Per-connector avatar colors — added 2026-09-01 for the sidebar/Integrations
# visual refresh (the user shared a reference screenshot of a light,
# card-based activity feed with colorful per-source icon avatars and asked
# for that visual language). Each connector gets a soft tinted background +
# a saturated foreground for its icon chip, echoing that reference's colorful
# app icons (a reddish Gmail, a purple chat app, a near-black dev-tool icon,
# etc.) without borrowing anyone's actual brand colors wholesale — these are
# Pilant's own palette, just varied per connector instead of one flat
# grey chip for every card. "knowledge_base" isn't a CONNECTORS entry (see
# _knowledge_base_card_data's docstring) so it gets its own fallback below.
_CONNECTOR_ACCENT = {
    "gmail": {"bg": "#FCE9E7", "fg": "#C7462F"},
    "slack": {"bg": "#F1E7FB", "fg": "#6B3FA0"},
    "github": {"bg": "#EAEBEF", "fg": "#20242E"},
    "helpdesk": {"bg": "#E5F0FC", "fg": "#1A5FB4"},
    "jira": {"bg": "#E7ECFC", "fg": "#2E52B8"},
    "unified": {"bg": "#EEEAFE", "fg": "#6C7CFF"},
    "custom": {"bg": "#FBEFDE", "fg": "#B5691D"},
    "knowledge_base": {"bg": "#E7F5EF", "fg": "#227A55"},
}
_DEFAULT_CONNECTOR_ACCENT = {"bg": "#F0F2F7", "fg": "#6B7385"}


def _connector_accent(key):
    return _CONNECTOR_ACCENT.get(key, _DEFAULT_CONNECTOR_ACCENT)


def _connector_avatar_html(key, icon, extra_class=""):
    """One colored icon chip for a connector — the small rounded avatar used
    both in the sidebar's workflow cards and on the Integrations grid, so the
    same connector reads as the same visual identity in both places. `icon`
    is passed in rather than looked up here so callers that already have a
    CONNECTORS entry (or a synthetic one, like the knowledge-base card) don't
    need a second lookup."""
    accent = _connector_accent(key)
    cls = f"wf-avatar {extra_class}".strip()
    return f'<span class="{cls}" style="background:{accent["bg"]};color:{accent["fg"]}">{icon}</span>'


# {lowercased connector label: {"bg","fg"}} — the renderer.py counterpart to
# _CONNECTOR_LINKS_BY_LABEL above, added 2026-09-01 for the merged-screen
# "feed dashboard" redesign (the source-color dot next to a titled list's
# heading, and the filter chips above a merged screen — see renderer.py's
# _connector_color/_filter_chip_bar_html). Keyed by label, not by key,
# because that's what a render_view's list-component 'title' actually
# contains (agent_unified.py's SYSTEM prompt sets a merged list's title to
# the app's plain name, e.g. "Gmail", "Jira") — same lookup shape
# _CONNECTOR_LINKS_BY_LABEL already uses for the same reason.
_CONNECTOR_COLORS_BY_LABEL = {
    cfg["label"].strip().lower(): _connector_accent(key)
    for key, cfg in CONNECTORS.items()
    if not cfg.get("freeform")
}

# Categories for the Integrations page's left-hand filter panel (redesigned
# 2026-08-30 to match the card-grid + category-sidebar layout the user
# referenced). Real categories over Pilant's actual real connectors — not a
# copy of the reference's own app names (HubSpot/Pipedrive/etc aren't real
# Pilant integrations, so they don't belong here; see this module's
# docstring on never offering a connect button that would just be theater).
# "search" covers the Knowledge base (RAG) card, which isn't in CONNECTORS
# above since it isn't a per-connector chat, just its own card built by
# _knowledge_base_card_data().
CATEGORY_META = {
    "overview": {"label": "Overview", "icon": "&#127760;"},
    "email": {"label": "Email", "icon": "&#9993;"},
    "messaging": {"label": "Messaging", "icon": "&#128172;"},
    "developer": {"label": "Developer Tools", "icon": "&#128295;"},
    "support": {"label": "Support", "icon": "&#127915;"},
    "search": {"label": "Knowledge Base", "icon": "&#128269;"},
}


# --- In-memory workflow store ----------------------------------------------
WORKFLOWS = {}
WORKFLOW_ORDER = []  # most-recently-created first
_next_id_counter = [0]


def _new_workflow_id():
    _next_id_counter[0] += 1
    return f"wf_{_next_id_counter[0]}"


def _create_workflow(connector=None):
    """connector=None means "not chosen yet" — the chat's first message in
    a fresh workflow is read as the person's answer to "which app would you
    like to connect?" instead of being forwarded to any agent. See
    studio_message()'s connector-selection branch."""
    wf_id = _new_workflow_id()
    WORKFLOWS[wf_id] = {
        "id": wf_id,
        "title": "Untitled workflow",
        "connector": connector,
        "messages": [],           # [{"role": "user"/"assistant", "text": ...}, ...] — for display
        "agent_messages": None,   # raw OpenAI-format conversation, set only while mid-clarify
        "fetched_data": False,
        "last_render": None,      # last successful render_view result, or None
        "last_request_text": "",
        # Added 2026-08-29: which real folder/search the embedded Gmail
        # panel (a gmail workflow's live preview) is currently scoped to —
        # see _handle_studio_message's "render" branch, which updates these
        # from the render's real fetch_args (agent_gmail.py's dispatch)
        # whenever a chat request actually fetched Gmail data, and the
        # /studio route, which lets an explicit folder-tab click override
        # them for that request. Unused by every other connector.
        "gmail_panel_folder": "inbox",
        "gmail_panel_query": None,
        # Added 2026-08-29 alongside the panel's own search box (POST
        # /studio/panel_search): the raw text someone typed into THAT box,
        # kept separately from gmail_panel_query (the real resolved Gmail
        # syntax actually used to fetch) so the box can keep showing what
        # was typed rather than its translation — same "input keeps your
        # words, a hint nearby shows the real query" split /inbox's own
        # search box already uses. None whenever the current
        # gmail_panel_query instead came from the chat (fetch_args) or a
        # plain folder-tab click, not from this box.
        "gmail_panel_search_text": None,
        # Cross-request conversation memory, added 2026-08-25 — see
        # _append_memory_exchange()'s docstring below. None/empty means "no
        # memory yet" (a brand-new workflow, or one that just switched
        # connectors); once set, a fresh top-level request seeds the agent's
        # conversation with this instead of starting from nothing, so a
        # short follow-up like "make it more compact" or "now add a filter"
        # has the context of what was built/discussed before. Deliberately
        # separate from agent_messages above, which is only ever the raw,
        # exact, short-lived state of a single IN-PROGRESS clarify exchange
        # — memory is the durable, compacted record of exchanges that have
        # already finished.
        "memory": None,
    }
    WORKFLOW_ORDER.insert(0, wf_id)
    return WORKFLOWS[wf_id]


def _current_workflow():
    wf = WORKFLOWS.get(session.get("current_workflow"))
    if wf is None:
        wf = _create_workflow()
        session["current_workflow"] = wf["id"]
    return wf


SHARED_CSS = """
:root { --ground:#F7F8FB; --surface:#FFFFFF; --surface-2:#F0F2F7; --border:#E3E7F1; --text:#171B2E; --text-muted:#6B7385; --accent:#6C7CFF;
  --shadow-sm:0 1px 2px rgba(23,27,46,.05), 0 1px 1px rgba(23,27,46,.04);
  --shadow-md:0 6px 16px rgba(23,27,46,.08), 0 2px 4px rgba(23,27,46,.05); }
* { box-sizing:border-box; }
body { margin:0; background:var(--ground); color:var(--text); font-family:'IBM Plex Sans',system-ui,sans-serif; }

.centerwrap { min-height:100vh; display:flex; align-items:center; justify-content:center; }
.wrap { width:100%; max-width:640px; padding:24px; text-align:center; }
.eyebrow { font-family:'Sora',sans-serif; font-weight:700; font-size:1.4rem; letter-spacing:.02em; color:var(--text); margin:0 0 6px; }
.subeyebrow { font-family:'IBM Plex Mono',monospace; font-size:.72rem; color:var(--text-muted); margin:0 0 28px; }
form.loginform { display:flex; flex-direction:column; gap:14px; }
input[type=text], input[type=password] { width:100%; background:var(--surface); border:1px solid var(--border); border-radius:10px; padding:14px 16px; font-size:1rem; color:var(--text); font-family:'IBM Plex Sans',sans-serif; }
input[type=text]:focus, input[type=password]:focus { outline:none; border-color:var(--accent); }
input[type=text]::placeholder, input[type=password]::placeholder { color:var(--text-muted); }
button, .btn { background:var(--accent); color:#fff; border:none; border-radius:8px; padding:11px 18px; font-size:.85rem; font-weight:600; cursor:pointer; font-family:'IBM Plex Sans',sans-serif; }
button:hover, .btn:hover { opacity:.92; }
.error { color:#C6392F; font-family:'IBM Plex Mono',monospace; font-size:.8rem; margin-top:16px; }

body.studio-body { height:100vh; overflow:hidden; }
.topbar { display:flex; justify-content:space-between; align-items:center; padding:12px 20px; border-bottom:1px solid var(--border); height:52px; box-sizing:border-box; background:var(--surface); }
.topbar-left { display:flex; align-items:center; gap:10px; }
.brand { font-family:'Sora',sans-serif; font-weight:700; font-size:1.05rem; margin:0; letter-spacing:-.01em; }
/* Added 2026-08-26: a persistent top-left icon that jumps straight to the
   full inbox (view-switcher pattern) — matched by an identical-looking
   button on the full-view page (gmail_site.py's _app_shell) that jumps
   back here, so switching between "normal" (this chat+preview shell) and
   "full" (the real interactive inbox) is always a single click from the
   same top-left corner on both. */
.view-toggle-btn {
  display:inline-flex; align-items:center; justify-content:center; width:32px; height:32px;
  border-radius:8px; background:transparent; border:1px solid var(--border); color:var(--text-muted);
  text-decoration:none; font-size:1rem; flex:none;
}
.view-toggle-btn:hover { border-color:var(--accent); color:var(--text); background:rgba(108,124,255,.1); }
.session-row { font-family:'IBM Plex Mono',monospace; font-size:.72rem; color:var(--text-muted); }
.session-row a { color:var(--text-muted); }
.session-row a:hover { color:var(--accent); }

.shell { display:flex; height:calc(100vh - 52px); }

.sidebar { width:220px; flex:none; border-right:1px solid var(--border); padding:16px; overflow-y:auto; }
/* Pill shape + solid-accent active state, added 2026-08-26 to match the
   full-view Gmail interface's .compose-btn/.sidebar-folder look — same
   design language on both "normal" and "full" view now, not two visually
   unrelated sidebars that happen to sit next to each other. */
.new-wf-btn { width:100%; background:var(--accent); color:#fff; border:none; border-radius:24px; padding:11px 12px; font-size:.82rem; font-weight:600; cursor:pointer; font-family:'IBM Plex Sans',sans-serif; margin-bottom:18px; }
.new-wf-btn:hover { opacity:.92; }
.sidebar-label { font-family:'IBM Plex Mono',monospace; font-size:.66rem; letter-spacing:.06em; text-transform:uppercase; color:var(--text-muted); margin:0 0 8px; }
.wf-list { display:flex; flex-direction:column; gap:6px; }
.wf-item { display:block; padding:9px 12px; border-radius:8px; font-size:.82rem; color:var(--text-muted); text-decoration:none; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.wf-item:hover { background:rgba(108,124,255,.08); color:var(--text); }
.wf-item.active { background:var(--accent); color:#fff; font-weight:600; }
.wf-empty { font-size:.78rem; color:var(--text-muted); }
/* Workflow cards — added 2026-09-01, redesigned to match a light, card-based
   activity-feed reference the user shared: each workflow gets a colored
   connector-icon avatar (see _connector_avatar_html/_CONNECTOR_ACCENT) next
   to its title, in a rounded card with a soft resting shadow that lifts
   further on hover — rather than the old plain single-line text link. The
   active workflow gets an accent ring + tinted background instead of a
   solid accent fill, so it stays legible against every connector's own
   avatar color instead of clashing with it. */
.wf-card { display:flex; align-items:center; gap:10px; padding:9px 10px; border-radius:12px; background:var(--surface); border:1px solid var(--border); box-shadow:var(--shadow-sm); text-decoration:none; transition:box-shadow .14s ease, border-color .14s ease, transform .14s ease; }
.wf-card:hover { box-shadow:var(--shadow-md); border-color:rgba(108,124,255,.35); transform:translateY(-1px); }
.wf-card.active { border-color:var(--accent); background:rgba(108,124,255,.07); box-shadow:0 0 0 1px var(--accent) inset, var(--shadow-sm); }
.wf-avatar { flex:none; width:32px; height:32px; border-radius:10px; display:inline-flex; align-items:center; justify-content:center; font-size:1rem; line-height:1; }
.wf-card-text { min-width:0; display:flex; flex-direction:column; gap:1px; }
.wf-card-title { font-size:.83rem; font-weight:600; color:var(--text); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.wf-card-sub { font-size:.72rem; color:var(--text-muted); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }

.sidebar-nav { margin-top:14px; padding-top:14px; border-top:1px solid var(--border); }
.nav-integrations { display:flex; align-items:center; gap:8px; font-weight:500; }
.nav-integrations.active { background:var(--accent); color:#fff; font-weight:600; }

.integrations-main { flex:1; overflow-y:auto; padding:24px 28px; }
.integrations-page { max-width:1120px; }
.integ-header { margin:0 0 20px; }
.integ-page-title { font-family:'Sora',sans-serif; font-weight:700; font-size:1.3rem; margin:0 0 4px; display:flex; align-items:center; gap:9px; }
.integ-page-sub { font-size:.85rem; color:var(--text-muted); margin:0; }
.integ-notice { border-radius:8px; padding:10px 14px; font-size:.82rem; margin:0 0 18px; }
.integ-notice-ok { background:rgba(108,124,255,.1); border:1px solid var(--accent); color:var(--text); }
.integ-notice-error { background:rgba(198,57,47,.08); border:1px solid #C6392F; color:#C6392F; }

/* ---- two-column layout: Categories panel + Available Integrations grid,
   redesigned 2026-08-30 to match the card-grid reference the user shared ---- */
.integ-layout { display:flex; gap:24px; align-items:flex-start; }
.integ-categories { width:230px; flex:none; background:var(--surface); border:1px solid var(--border); border-radius:14px; padding:18px; box-shadow:var(--shadow-sm); }
.integ-categories-title { font-family:'Sora',sans-serif; font-weight:700; font-size:.95rem; margin:0 0 12px; }
.integ-cat-list { display:flex; flex-direction:column; gap:2px; margin-bottom:12px; }
.integ-cat-item { display:flex; align-items:center; justify-content:space-between; gap:8px; padding:9px 10px; border-radius:8px; font-size:.84rem; color:var(--text); text-decoration:none; }
.integ-cat-item .n { display:flex; align-items:center; gap:8px; }
.integ-cat-item:hover { background:var(--surface-2); }
.integ-cat-count { font-size:.72rem; color:var(--text-muted); background:var(--surface-2); border-radius:999px; padding:2px 9px; font-family:'IBM Plex Mono',monospace; }
.integ-cat-item.active { background:rgba(108,124,255,.1); color:var(--accent); font-weight:600; }
.integ-cat-item.active .integ-cat-count { background:rgba(108,124,255,.16); color:var(--accent); }
.integ-cat-all { border-top:1px solid var(--border); padding-top:12px; margin-top:2px; font-weight:600; }

.integ-main-col { flex:1; min-width:0; }
.integ-main-head { display:flex; justify-content:space-between; align-items:flex-start; gap:16px; margin:0 0 18px; flex-wrap:wrap; }
.integ-main-head h2 { font-family:'Sora',sans-serif; font-weight:700; font-size:1.05rem; margin:0 0 3px; }
.integ-main-head p { font-size:.82rem; color:var(--text-muted); margin:0; }
.integ-toggle { display:flex; align-items:center; gap:9px; font-size:.8rem; color:var(--text-muted); white-space:nowrap; text-decoration:none; flex:none; }
.integ-toggle-switch { width:34px; height:19px; border-radius:999px; background:var(--border); position:relative; flex:none; transition:background .15s; }
.integ-toggle-switch::after { content:""; position:absolute; top:2px; left:2px; width:15px; height:15px; border-radius:50%; background:#fff; transition:left .15s; box-shadow:0 1px 2px rgba(0,0,0,.2); }
.integ-toggle.on { color:var(--text); }
.integ-toggle.on .integ-toggle-switch { background:var(--accent); }
.integ-toggle.on .integ-toggle-switch::after { left:17px; }

.integ-grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(250px, 1fr)); gap:16px; }
.integ-card { display:flex; flex-direction:column; gap:10px; background:var(--surface); border:1px solid var(--border); border-radius:14px; padding:18px; box-shadow:var(--shadow-sm); transition:box-shadow .14s ease, transform .14s ease; }
.integ-card:hover { box-shadow:var(--shadow-md); transform:translateY(-1px); }
.integ-card-top { display:flex; align-items:flex-start; justify-content:space-between; gap:10px; }
.integ-card-icon { width:44px; height:44px; border-radius:12px; background:var(--surface-2); display:flex; align-items:center; justify-content:center; font-size:1.35rem; flex:none; }
.integ-card-installed { font-size:.66rem; font-weight:600; letter-spacing:.02em; text-transform:uppercase; color:#227A55; background:rgba(34,122,85,.1); border-radius:999px; padding:3px 9px; white-space:nowrap; }
.integ-card-name { font-weight:700; font-size:.95rem; margin:0; }
.integ-card-tag { display:inline-block; font-size:.68rem; color:var(--text-muted); background:var(--surface-2); border-radius:5px; padding:2px 8px; margin-top:5px; text-transform:lowercase; }
.integ-card-desc { font-size:.82rem; color:var(--text-muted); line-height:1.5; margin:0; }
.integ-card-status { font-size:.76rem; color:var(--text-muted); margin:0; }
.integ-status-connected { color:#227A55; font-weight:600; }
.integ-card-actions { display:flex; flex-direction:column; gap:8px; margin-top:2px; }
.integ-card-actions form { margin:0; }
.integ-card-actions .btn { width:100%; box-sizing:border-box; text-align:center; font-size:.8rem; padding:10px 14px; text-decoration:none; display:block; }
.btn-secondary { background:transparent !important; border:1px solid var(--border) !important; color:var(--text) !important; }
.btn-secondary:hover { border-color:var(--accent) !important; }
.integ-empty { grid-column:1/-1; padding:40px 20px; text-align:center; color:var(--text-muted); font-size:.85rem; background:var(--surface); border:1px dashed var(--border); border-radius:12px; }

.composer-page { max-width:820px; }
.composer-grid { display:flex; gap:20px; margin:0 0 22px; flex-wrap:wrap; }
.composer-palette { flex:1; min-width:240px; }
.composer-canvas { flex:1; min-width:240px; }
.composer-col-label { font-family:'IBM Plex Mono',monospace; font-size:.68rem; letter-spacing:.06em; text-transform:uppercase; color:var(--text-muted); margin:0 0 8px; }
/* display:block is required, not cosmetic — the palette's version of this
   card is a <label> (default display:inline in HTML) wrapping the hidden
   checkbox + block-level <p> tags (see _composer_palette_html). An inline
   element containing block children renders its border/background per
   LINE FRAGMENT, not as one box — with the checkbox hidden, that first
   fragment collapses to a sliver and the real text spills out beneath it
   unstyled. Found live 2026-08-26 from a real screenshot showing exactly
   that broken layout. The canvas side (syncCanvas() in _COMPOSER_JS) uses
   plain <div>s, block by default, so it never showed the bug — but this
   class is shared by both, so the fix belongs here once, not per-caller. */
.primitive-card { display:block; background:var(--surface); border:1px solid var(--border); border-radius:8px; padding:10px 12px; margin-bottom:8px; cursor:pointer; user-select:none; transition:border-color .12s; }
.primitive-card:hover { border-color:var(--accent); }
.primitive-card.selected { border-color:var(--accent); background:rgba(108,124,255,.12); }
.primitive-card-label { font-weight:600; font-size:.85rem; margin:0 0 2px; }
.primitive-card-conn { font-size:.72rem; color:var(--text-muted); }
.composer-canvas-zone { min-height:120px; border:1.5px dashed var(--border); border-radius:10px; padding:12px; display:flex; flex-direction:column; gap:8px; }
.composer-canvas-zone.drag-over { border-color:var(--accent); background:rgba(108,124,255,.06); }
.composer-canvas-empty { color:var(--text-muted); font-size:.8rem; text-align:center; padding:20px 8px; }
.composer-instruction { width:100%; background:var(--surface); border:1px solid var(--border); border-radius:8px; padding:12px 14px; font-size:.9rem; color:var(--text); font-family:'IBM Plex Sans',sans-serif; resize:vertical; min-height:64px; margin:0 0 12px; }
.composer-instruction:focus { outline:none; border-color:var(--accent); }
.composer-compose-btn { background:var(--accent); color:#fff; border:none; border-radius:8px; padding:11px 20px; font-size:.85rem; font-weight:600; cursor:pointer; font-family:'IBM Plex Sans',sans-serif; }
.composer-note { font-size:.82rem; color:var(--text-muted); margin:0 0 18px; }
.composer-style-box { background:var(--surface); border:1px solid var(--accent); border-radius:10px; padding:14px 16px; margin:0 0 20px; }
.composer-style-box .composer-instruction { min-height:44px; margin-bottom:8px; }
.composer-result { margin-top:20px; }
.composer-save-form { display:flex; gap:8px; margin-top:14px; }
.composer-save-form input[type=text] { flex:1; background:var(--surface); border:1px solid var(--border); border-radius:8px; padding:9px 12px; font-size:.85rem; color:var(--text); font-family:'IBM Plex Sans',sans-serif; }
.composer-saved-list { margin-top:28px; }
.composer-saved-item { display:flex; justify-content:space-between; align-items:center; background:var(--surface); border:1px solid var(--border); border-radius:8px; padding:10px 14px; margin-bottom:8px; }
.composer-saved-item a { color:var(--text); text-decoration:none; font-weight:500; font-size:.86rem; }
.composer-saved-item a:hover { color:var(--accent); }
.composer-saved-item form { margin:0; }
.composer-saved-item button { background:transparent; border:none; color:var(--text-muted); font-size:.78rem; cursor:pointer; padding:4px 8px; }
.composer-saved-item button:hover { color:#C6392F; }

.chatpane { width:380px; flex:none; border-right:1px solid var(--border); display:flex; flex-direction:column; }
.chat-scroll { flex:1; overflow-y:auto; padding:18px; display:flex; flex-direction:column; gap:10px; }
.chat-bubble { border-radius:12px; padding:10px 13px; font-size:.85rem; line-height:1.5; max-width:92%; }
.chat-bubble-user { background:rgba(108,124,255,.14); color:var(--text); align-self:flex-end; }
.chat-bubble-assistant { background:var(--surface); border:1px solid var(--border); color:var(--text); align-self:flex-start; box-shadow:var(--shadow-sm); }
.chat-bubble p { margin:0 0 8px; }
.chat-bubble p:last-child { margin-bottom:0; }
.chat-bubble ol { margin:4px 0 8px; padding-left:20px; }
.chat-bubble ol:last-child { margin-bottom:0; }
.chat-bubble li { margin:4px 0; }
.chat-bubble strong { color:var(--text); font-weight:600; }
.chat-bubble a { color:var(--accent); }
/* Added 2026-08-29: "improvement idea #3" — a compact snapshot of what a
   turn actually built, sitting directly under that turn's own chat bubble
   (renderer.render_chat_inline_result) rather than only in the single,
   always-latest live preview panel. Deliberately smaller/quieter than a
   real .chat-bubble (no border-radius pill shape, muted text) so it reads
   as "a peek at the result," not a second message. */
.chat-result-card { align-self:flex-start; max-width:92%; background:var(--ground,#0B1220); border:1px solid var(--border); border-radius:10px; padding:10px 12px; margin-top:-2px; box-shadow:var(--shadow-sm); }
.chat-result-heading { font-weight:600; color:var(--text); margin:0 0 6px; font-size:.8rem; }
/* Added 2026-08-31 alongside renderer.py's _chat_inline_component_html
   change: labels one 'list' component's section within this compact card
   (e.g. "Gmail", "Helpdesk", "Jira" on a merged 'All apps' result) — plain
   text, not a link, matching the live preview panel's own per-app
   grouping but staying read-only here, consistent with this card's
   "quick peek" purpose. A uniform top margin gives every section (the
   first included — it still reads fine right under the card's own
   heading) visible breathing room from whatever came before it. */
.chat-result-section { font-size:.68rem; font-weight:600; letter-spacing:.03em; text-transform:uppercase; color:var(--accent); margin:10px 0 4px; font-family:'IBM Plex Mono',monospace; }
.chat-result-rows { list-style:none; margin:0; padding:0; display:flex; flex-direction:column; gap:5px; }
.chat-result-rows li { display:flex; flex-direction:column; gap:1px; font-size:.78rem; }
.chat-result-name { color:var(--text); font-weight:500; }
.chat-result-note { color:var(--text-muted); font-size:.74rem; }
.chat-result-more { margin:6px 0 0; color:var(--text-muted); font-size:.72rem; font-family:'IBM Plex Mono',monospace; }
.chat-result-empty { margin:0; color:var(--text-muted); font-size:.76rem; }
.chat-result-stats { display:flex; flex-wrap:wrap; gap:6px; }
.chat-result-stat { background:var(--surface); border:1px solid var(--border); border-radius:6px; padding:3px 8px; font-size:.72rem; color:var(--text-muted); }
.chat-result-stat strong { color:var(--text); font-weight:600; }
.chat-form { display:flex; gap:8px; padding:14px; border-top:1px solid var(--border); }
.chat-form input[type=text] { flex:1; background:var(--surface); border:1px solid var(--border); border-radius:8px; padding:10px 12px; font-size:.85rem; color:var(--text); font-family:'IBM Plex Sans',sans-serif; }
.chat-form input[type=text]:focus { outline:none; border-color:var(--accent); }
.chat-form input[type=text]::placeholder { color:var(--text-muted); }
.chat-form button { background:var(--accent); color:#fff; border:none; border-radius:8px; padding:10px 16px; font-size:.82rem; font-weight:600; cursor:pointer; font-family:'IBM Plex Sans',sans-serif; }

.previewpane { flex:1; overflow-y:auto; padding:24px 28px; }
.preview-label { font-family:'IBM Plex Mono',monospace; font-size:.7rem; letter-spacing:.08em; text-transform:uppercase; color:var(--text-muted); margin:0 0 6px; }
.preview-request { font-size:.85rem; color:var(--text-muted); margin:0 0 16px; }
.preview-request a { color:var(--accent); text-decoration:none; font-weight:500; }
.preview-request a:hover { text-decoration:underline; }
.preview-empty { height:100%; display:flex; flex-direction:column; align-items:center; justify-content:center; text-align:center; color:var(--text-muted); gap:6px; }
.preview-empty p:first-child { font-family:'IBM Plex Mono',monospace; font-size:.7rem; letter-spacing:.08em; text-transform:uppercase; }
.preview-hint { font-size:.85rem; max-width:280px; }
"""

# Studio's page shell (_page_shell below) concatenates SHARED_CSS above with
# renderer.py's own APP_CSS/GMAIL_CSS (imported, not owned by this file —
# every other demo in this project still uses that shared module unchanged,
# see this file's own module docstring on why studio.py's dashboard-styled
# choices stay local rather than edited into the shared file). APP_CSS
# defines its OWN `:root` block with the OLD dark tokens, and since it's
# concatenated after SHARED_CSS, it would win the cascade and silently pull
# the page back to dark even after SHARED_CSS above was switched to light.
# This block is appended LAST (see _page_shell's CSS concatenation) purely
# to win that cascade for the :root tokens, plus a few colors GMAIL_CSS
# hardcodes as literal hex (tuned for a dark background) rather than as
# `var(--text)`-based — those need their own light-appropriate values, not
# just a token flip. The already-light `.app-window` (--app-*) tokens run
# the generated-view "device mockup" card and are untouched here — that
# card was always meant to look like a light app inside a dark shell, and
# now it's a light app inside a light shell instead, so no change needed.
STUDIO_LIGHT_OVERRIDE_CSS = """
:root { --ground:#F7F8FB; --surface:#FFFFFF; --surface-2:#F0F2F7; --border:#E3E7F1; --text:#171B2E; --text-muted:#6B7385; --card:#FFFFFF; }
.gmail-row-status-warning { color:#A56A0E; background:rgba(165,106,14,.1); }
.gmail-row-status-critical { color:#C6392F; background:rgba(198,57,47,.1); }
.gmail-row-status-good { color:#227A55; background:rgba(34,122,85,.1); }
.gmail-row-star-btn.active { color:#A56A0E; }
.gmail-row-delete:hover { color:#C6392F; }
.gmail-panel-msg-btn.active { color:#A56A0E; border-color:#A56A0E; }
.gmail-panel-msg-btn-danger:hover { border-color:#C6392F; color:#C6392F; }
.gmail-panel-notice-ok { background:rgba(34,122,85,.1); color:#227A55; border:1px solid rgba(34,122,85,.35); }
.gmail-panel-notice-err { background:rgba(198,57,47,.08); color:#C6392F; border:1px solid rgba(198,57,47,.35); }
"""

LOGIN_PAGE = (
    "<!doctype html><html><head><meta charset=\"utf-8\"><title>Pilant Studio — log in</title>"
    "<link rel=\"preconnect\" href=\"https://fonts.googleapis.com\">"
    "<link rel=\"stylesheet\" href=\"https://fonts.googleapis.com/css2?family=Sora:wght@600;700&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap\">"
    "<style>{{CSS}}</style></head><body><div class=\"centerwrap\">"
    "<div class=\"wrap\">"
    "<p class=\"eyebrow\">Pilant Studio</p>"
    "<p class=\"subeyebrow\">one interface &middot; describe it, get a live screen</p>"
    "<form class=\"loginform\" method=\"post\" action=\"/login\">"
    "<input type=\"text\" name=\"username\" placeholder=\"username\" autofocus required>"
    "<input type=\"password\" name=\"password\" placeholder=\"password\" required>"
    "<button type=\"submit\">Log in</button>"
    "</form>"
    "{{ERROR_HTML}}"
    "</div></div></body></html>"
)


def render_login(error=None):
    error_html = f'<p class="error">{_esc(error)}</p>' if error else ""
    out = LOGIN_PAGE
    out = out.replace("{{CSS}}", SHARED_CSS)
    out = out.replace("{{ERROR_HTML}}", error_html)
    return out


def _sidebar_html(active_workflow_id, active_nav=None):
    """active_workflow_id highlights that workflow in the list below — pass
    None while on a non-workflow page (e.g. Integrations) so nothing in the
    list is wrongly shown as current. active_nav highlights the
    "Integrations" nav link instead — pass "integrations" while on that
    page. Added 2026-08-25: the "Integrations" link below the workflow list
    replaces what used to be a second column of connector icons living
    next to the chat pane — the user asked for a plain nav entry here
    instead, matching the DynamisOS reference's Home/Workflows/Integrations
    sidebar pattern, with its own full page rather than a cramped column."""
    items = []
    for wf_id in WORKFLOW_ORDER:
        w = WORKFLOWS[wf_id]
        active = " active" if w["id"] == active_workflow_id else ""
        conn_key = w["connector"]
        cfg = CONNECTORS.get(conn_key)
        label = cfg["label"] if cfg else "choosing app…"
        icon = cfg.get("icon", "&#128268;") if cfg else "&#8943;"
        avatar_html = _connector_avatar_html(conn_key, icon)
        items.append(
            f'<a class="wf-card{active}" href="/studio/open/{_esc(w["id"])}">'
            f'{avatar_html}'
            f'<span class="wf-card-text"><span class="wf-card-title">{_esc(w["title"])}</span>'
            f'<span class="wf-card-sub">{_esc(label)}</span></span>'
            f'</a>'
        )
    items_html = "".join(items) or '<p class="wf-empty">No workflows yet.</p>'
    integ_active = " active" if active_nav == "integrations" else ""
    composer_active = " active" if active_nav == "composer" else ""
    customers_active = " active" if active_nav == "customers" else ""
    return (
        '<div class="sidebar">'
        '<form method="post" action="/studio/new"><button class="new-wf-btn" type="submit">+ New workflow</button></form>'
        '<p class="sidebar-label">Workflows</p>'
        f'<div class="wf-list">{items_html}</div>'
        '<div class="sidebar-nav">'
        # Added 2026-08-26 alongside the Gmail Blueprint merge — the real
        # interactive inbox (compose/delete/star/forward/reply, not the
        # chat's read-only render) is one click away from anywhere in
        # Studio, not just from a Gmail workflow's preview pane.
        '<a class="wf-item nav-integrations" href="/inbox?folder=inbox">&#128233; Full inbox</a>'
        # Added 2026-08-26 alongside the Composer — a workflow is still
        # "describe it in one connector's chat," but the Composer is the
        # cross-connector, typed-primitives version (see agent_composer.py's
        # module docstring): pull real data from more than one source into
        # one composed screen, or hand-pick primitives on a visual canvas.
        f'<a class="wf-item nav-integrations{composer_active}" href="/composer">&#129513; Composer</a>'
        # Added 2026-08-27 — the customer-360 view (see primitives.py's
        # retail_orders and this file's /customers routes below): a staff
        # member opens one real customer and gets orders, refund status,
        # delivery status, and recommended actions composed onto one
        # screen, instead of checking separate systems for each.
        f'<a class="wf-item nav-integrations{customers_active}" href="/customers">&#128100; Customers</a>'
        f'<a class="wf-item nav-integrations{integ_active}" href="/integrations">&#128268; Integrations</a>'
        '</div>'
        '</div>'
    )


def _connector_card_data(key, cfg):
    """Builds the card data dict for one real CONNECTORS entry — same
    status/actions logic the old _integrations_page_html had inline, just
    factored out so both the card grid and the category counts above it
    can use it. Gmail's card is the one with a real "Connect" action (see
    this file's module docstring and connectors_gmail.py's
    get_authorization_url/exchange_code_for_tokens): it redirects to
    Google's own sign-in page, not a form on this site, so this app never
    sees the person's real Gmail password. The other connectors still run
    on static server-side credentials from .env, so their cards say that
    plainly instead of offering a connect button that would just be
    theater — and are always "installed" for the Show-only-connected
    filter, since those credentials are already active."""
    label = _esc(cfg["label"])
    icon = cfg.get("icon", "&#128268;")
    start_btn = (
        f'<form method="post" action="/studio/new/{_esc(key)}">'
        f'<button class="btn btn-secondary" type="submit">Start a workflow</button></form>'
    )
    if cfg.get("oauth"):
        # Added 2026-08-26 alongside the Gmail Blueprint merge: the real
        # interactive inbox (not the chat's read-only render) is one click
        # from its own Integrations card too, connected or not — browsing/
        # reading never needed the OAuth connect flow (the static .env
        # token already covers that), only the write actions (Reply/
        # Compose/Delete/Star/Forward) need gmail.send/gmail.modify, and
        # those fail with a clear scope message rather than blocking the
        # link itself.
        full_inbox_btn = '<a class="btn btn-secondary" href="/inbox?folder=inbox">Open full inbox</a>'
        connected_email = connectors_gmail.get_connected_account()
        installed = bool(connected_email)
        if connected_email:
            status = f'<p class="integ-card-status integ-status-connected">Connected as {_esc(connected_email)}</p>'
            actions = (
                f'<a class="btn" href="/oauth/gmail/connect">Reconnect a different account</a>'
                '<form method="post" action="/oauth/gmail/disconnect">'
                '<button class="btn btn-secondary" type="submit">Disconnect</button></form>'
                f'{full_inbox_btn}{start_btn}'
            )
        else:
            status = (
                '<p class="integ-card-status">Not connected yet &mdash; falls back to the shared '
                'inbox configured in .env, if any.</p>'
            )
            actions = f'<a class="btn" href="/oauth/gmail/connect">Connect {label}</a>{full_inbox_btn}{start_btn}'
    else:
        status = '<p class="integ-card-status">Connected via this Studio&#39;s server credentials.</p>'
        actions = start_btn
        installed = True
    return {
        "key": key, "label": label, "icon": icon,
        "category": cfg.get("category", "search"),
        "description": cfg.get("description", ""),
        "installed": installed,
        "status_html": status,
        "actions_html": actions,
    }


def _knowledge_base_card_data():
    """Same data shape as _connector_card_data, for the one card that isn't
    a CONNECTORS entry — the "Sync knowledge base" card (rag_index.py/
    rag_ingest.py). Shows what's currently indexed and a "Sync now" button
    (POST /rag/sync) that pulls the latest data from whichever connectors
    are configured via rag_ingest.ingest_all(). Kept separate from the
    per-connector cards since syncing isn't "connecting" a new account —
    it's refreshing the search index behind the search_knowledge_base tool
    every connector chat already has. There's no separate "Search" action
    here — searching happens by asking a connector's own chat."""
    if not rag_index.is_configured():
        return {
            "key": "knowledge_base", "label": "Knowledge base", "icon": "&#128269;",
            "category": "search",
            "description": "Search across Gmail, Slack, and GitHub history once synced.",
            "installed": False,
            "status_html": (
                '<p class="integ-card-status">Not set up yet &mdash; add AWS_BEARER_TOKEN_BEDROCK to your '
                '.env file (a Bedrock long-term API key, used only for generating search embeddings via '
                'Amazon Titan, separate from ANTHROPIC_API_KEY) and restart Studio to enable syncing and '
                'search.</p>'
            ),
            "actions_html": "",
        }
    stats = rag_index.stats()
    total = stats["total"]
    by_source = stats["by_source"]
    if total:
        parts = ", ".join(f"{n} {CONNECTORS[s]['label'] if s in CONNECTORS else s}" for s, n in by_source.items())
        status = f'<p class="integ-card-status integ-status-connected">{total} item(s) indexed &mdash; {_esc(parts)}.</p>'
    else:
        status = '<p class="integ-card-status">Nothing indexed yet &mdash; sync to enable search across older history.</p>'
    return {
        "key": "knowledge_base", "label": "Knowledge base", "icon": "&#128269;",
        "category": "search",
        "description": "Search across Gmail, Slack, and GitHub history once synced.",
        "installed": True,
        "status_html": status,
        "actions_html": '<form method="post" action="/rag/sync"><button class="btn" type="submit">Sync now</button></form>',
    }


def _render_integ_card(it):
    tag = CATEGORY_META.get(it["category"], {}).get("label", it["category"])
    installed_badge = '<span class="integ-card-installed">Connected</span>' if it["installed"] else ""
    actions = f'<div class="integ-card-actions">{it["actions_html"]}</div>' if it["actions_html"] else ""
    # Recolored 2026-09-01 to match the sidebar's per-connector avatar chips
    # (_connector_accent/_connector_avatar_html) — same connector, same
    # color, wherever its icon shows up in the app.
    accent = _connector_accent(it["key"])
    icon_style = f'background:{accent["bg"]};color:{accent["fg"]};'
    return (
        '<div class="integ-card">'
        '<div class="integ-card-top">'
        f'<div class="integ-card-icon" style="{icon_style}">{it["icon"]}</div>'
        f'{installed_badge}'
        '</div>'
        '<div>'
        f'<p class="integ-card-name">{it["label"]}</p>'
        f'<span class="integ-card-tag">{_esc(tag)}</span>'
        '</div>'
        f'<p class="integ-card-desc">{_esc(it["description"])}</p>'
        f'{it["status_html"]}'
        f'{actions}'
        '</div>'
    )


def _integrations_page_html(notice=None, notice_kind=None, category=None, installed_only=False):
    """The full Integrations page (GET /integrations) — redesigned
    2026-08-30 to match the card-grid + category-sidebar layout the user
    referenced (a real SaaS integrations page screenshot): a "Categories"
    panel on the left with real counts, "Available Integrations" cards on
    the right with a real "Show only connected" filter, one card per
    registered connector plus the knowledge base. Categories are Pilant's
    own real connector types (email/messaging/developer/support/search) —
    not a copy of the reference's own app names (HubSpot/Pipedrive/etc
    aren't real Pilant integrations, so a card for one of those would be
    exactly the "theater" this file's module docstring says to avoid)."""
    notice_html = ""
    if notice:
        cls = "integ-notice-error" if notice_kind == "error" else "integ-notice-ok"
        notice_html = f'<div class="integ-notice {cls}">{_esc(notice)}</div>'

    items = [
        _connector_card_data(key, cfg) for key, cfg in CONNECTORS.items() if not cfg.get("freeform")
    ]
    items.append(_knowledge_base_card_data())
    counts_by_cat = Counter(it["category"] for it in items)
    total = len(items)

    def _url(cat=None, inst=installed_only):
        params = []
        if cat:
            params.append(f"category={cat}")
        if inst:
            params.append("installed=1")
        return "/integrations" + ("?" + "&".join(params) if params else "")

    cat_rows = []
    for cat_key, meta in CATEGORY_META.items():
        n = counts_by_cat.get(cat_key, 0)
        active = " active" if category == cat_key else ""
        cat_rows.append(
            f'<a class="integ-cat-item{active}" href="{_url(cat_key)}">'
            f'<span class="n">{meta["icon"]} {meta["label"]}</span>'
            f'<span class="integ-cat-count">{n}</span></a>'
        )
    all_active = " active" if not category else ""

    visible = [
        it for it in items
        if (not category or it["category"] == category) and (not installed_only or it["installed"])
    ]
    cards_html = "".join(_render_integ_card(it) for it in visible) if visible else (
        '<div class="integ-empty">Nothing matches this filter.</div>'
    )

    toggle_on = " on" if installed_only else ""
    toggle_href = _url(category, not installed_only)

    return (
        '<div class="integrations-page">'
        '<div class="integ-header">'
        '<p class="integ-page-title">&#128268; Integrations</p>'
        '<p class="integ-page-sub">Connect your real accounts, or jump straight into a workflow for one that&#39;s already connected.</p>'
        '</div>'
        f'{notice_html}'
        '<div class="integ-layout">'
        '<div class="integ-categories">'
        '<p class="integ-categories-title">Categories</p>'
        f'<div class="integ-cat-list">{"".join(cat_rows)}</div>'
        f'<a class="integ-cat-item integ-cat-all{all_active}" href="{_url(None)}">'
        f'<span class="n">&#9635; All Integrations</span><span class="integ-cat-count">{total}</span></a>'
        '</div>'
        '<div class="integ-main-col">'
        '<div class="integ-main-head">'
        '<div><h2>Available Integrations</h2><p>Manage your connected accounts and credentials</p></div>'
        f'<a class="integ-toggle{toggle_on}" href="{toggle_href}"><span class="integ-toggle-switch"></span>Show only connected</a>'
        '</div>'
        f'<div class="integ-grid">{cards_html}</div>'
        '</div>'
        '</div>'
        '</div>'
    )


# Per-connector welcome copy for a brand-new, empty workflow — keyed by the
# same key used in CONNECTORS above. Falls back to a generic line (naming
# whichever connector's label is registered) for any connector added later
# that hasn't gotten bespoke example prompts yet.
_WELCOME_EXAMPLES = {
    "unified": (
        "whichever of your connected apps are relevant to what you ask",
        "what needs my attention today",
        "catch me up across everything",
    ),
    "gmail": (
        "your real Gmail inbox",
        "show my unread emails as a task list",
        "summarize what came in from finance this week",
    ),
    "slack": (
        "your real Slack channel",
        "what's been happening in the channel today",
        "show me anything mentioning the release",
    ),
    "github": (
        "your connected GitHub issues",
        "show my open issues as a list",
        "what's still unassigned",
    ),
    "helpdesk": (
        "your connected helpdesk tickets",
        "show open tickets as a list",
        "what's escalated right now",
    ),
    "jira": (
        "your Jira-style ENG and DES issues",
        "what's blocked right now",
        "show Priya's in-progress issues",
    ),
    "custom": (
        "whatever you describe, built with realistic sample data behind it",
        "a project tracker for my team",
        "an inventory dashboard for my warehouse",
    ),
}


def _connector_list_text():
    # Excludes "freeform" entries (currently just "custom") — those aren't
    # picked by name off a list, they're the fallback for describing your
    # own app; see CONNECTORS' comment above and studio_message() below.
    labels = [cfg["label"] for cfg in CONNECTORS.values() if not cfg.get("freeform")]
    if len(labels) == 1:
        return labels[0]
    return ", ".join(labels[:-1]) + " or " + labels[-1]


def _match_connector(text):
    """Matches a person's free-text answer to "which app would you like to
    connect?" against CONNECTORS' keys/labels — e.g. "slack", "Slack",
    "let's do slack" all match "slack". Returns the matched key, or None if
    nothing in the registry was mentioned. Deliberately excludes "custom"
    itself — that key/label ("custom"/"Custom interface") is a plausible
    substring of an ordinary sentence ("no custom sorting needed"), and this
    function is also used by _detect_connector_switch, where a false match
    on "custom" would silently swap a real connector out from under the
    person. The only way into the 'custom' connector is the no-match
    fallback in studio_message() below (or explicitly starting a workflow
    with it from /integrations)."""
    t = (text or "").strip().lower()
    for key, cfg in CONNECTORS.items():
        if key == "custom":
            continue
        if key in t or cfg["label"].lower() in t:
            return key
    return None


def _match_all_connectors(text):
    """Like _match_connector, but returns EVERY real (non-'custom')
    connector named in `text`, in CONNECTORS' own dict order, instead of
    stopping at the first hit. Added 2026-08-31 after a live bug: typing
    "connect gmail and jira" as a brand-new workflow's first message used
    to go through _match_connector alone, which returns on its first
    match — "gmail" — and never even looks further, so "jira" being named
    too was silently dropped and the whole workflow landed on the
    single-app Gmail connector instead of the merged 'unified' one. Used
    only by studio_message()'s connector-less branch, to notice when a
    first message names more than one real app and route to 'unified'
    accordingly — see there for how the result is used."""
    t = (text or "").strip().lower()
    matched = []
    for key, cfg in CONNECTORS.items():
        if key == "custom":
            continue
        if key in t or cfg["label"].lower() in t:
            matched.append(key)
    return matched
    return None


# Bug fixed 2026-08-25: a brand-new workflow's very first message used to
# fall straight through to the 'custom' connector — which fabricates
# realistic-looking but entirely invented sample data by design, see
# agent_custom.py's SYSTEM prompt — unless that message happened to contain
# a connector's literal name/label. "show my inbox" or "what's in my email"
# obviously means the real Gmail connector, but neither sentence contains
# the word "gmail", so it used to be treated exactly like "clothing brand"
# and answered with a made-up inbox full of invented names like "John Doe"
# and "Jane" instead of either the real data or a nudge to connect one.
# _infer_connector_from_intent() (used only by studio_message()'s
# connector-less branch, not by _detect_connector_switch, which stays
# strict on purpose) catches that: it tries an exact name/label match
# first, then falls back to a small set of domain words per real connector
# that clearly signal "I want my real data", before ever giving up and
# treating the message as a from-scratch custom build.
_CONNECTOR_INTENT_KEYWORDS = {
    "gmail": ("inbox", "email", "emails", "e-mail", "unread", "mailbox"),
    "slack": ("channel", "channels", "workspace", "dm", "slack message"),
    "github": ("issue", "issues", "pull request", "repo", "repository", "commit"),
    "helpdesk": ("ticket", "tickets", "helpdesk", "support queue", "escalated"),
    # "jira" deliberately avoids "issue"/"issues" — github's entry above
    # already claims those words and is checked first (see the dict-order
    # comment on _infer_connector_from_intent below), so jira only needs
    # words that are actually distinctive to it.
    "jira": ("jira", "sprint", "backlog", "epic", "story point", "story points", "kanban"),
    # "unified" deliberately LAST — this dict is checked in order and the
    # first match wins (see _infer_connector_from_intent below), so a
    # request that also happens to name a specific app's own domain word
    # (e.g. "catch up on my email today") still correctly matches that
    # app's own entry above, not this one. These phrases only catch
    # requests that don't already name a specific app.
    # "high priority"/"urgent"/"important" added 2026-08-31 after a real
    # miss: typing "what's high priority" as a brand-new workflow's first
    # message (naming no app) used to match nothing here and fall all the
    # way through to 'custom' — which FABRICATES realistic-looking sample
    # data by design (see CONNECTORS' freeform comment) — instead of
    # reaching this real connector, which actually fetches live
    # Helpdesk/Jira priority fields (and Gmail's importance/unread signals)
    # and merges them. None of gmail/slack/github/helpdesk's own keyword
    # tuples above contain these words, so this is always a safe fallback,
    # never a false steal from a more specific app match.
    "unified": (
        "catch up", "catch me up", "across my apps", "across all my apps",
        "what needs my attention", "overview of everything",
        "high priority", "priority", "urgent", "important",
    ),
}


def _infer_connector_from_intent(text):
    """Used only for a workflow's very first, connector-less message (see
    studio_message() below). Same as _match_connector, but if the message
    doesn't literally name a connector, also checks _CONNECTOR_INTENT_KEYWORDS
    for domain words that make the intent to see REAL data clear even
    without naming the app — see the bug note above this dict for why that
    matters. Only matched connectors that are actually registered are
    considered, so this degrades gracefully if one is ever removed from
    CONNECTORS. Returns None (falls through to 'custom') only when nothing,
    name or keyword, points to a real connector."""
    matched = _match_connector(text)
    if matched:
        return matched
    t = (text or "").strip().lower()
    for key, words in _CONNECTOR_INTENT_KEYWORDS.items():
        if key not in CONNECTORS:
            continue
        if any(word in t for word in words):
            return key
    return None


_SWITCH_TRIGGER_WORDS = ("connect", "switch", "integrate", "hook up", "link", "add", "change")


def _is_bare_connector_name(text):
    """Returns the connector key if `text`, stripped of surrounding
    whitespace/punctuation and case, is EXACTLY a real connector's key or
    label and nothing else — e.g. "gmail", "Gmail.", "gmail!" all match,
    but "gmail please" or "a gmail-style inbox" do not (those still go
    through the normal, verb-gated _detect_connector_switch, or are just
    forwarded to the current connector's agent as a real request).

    Bug this fixes (reproduced live 2026-08-25): a workflow with no
    connector yet starts on 'custom' as soon as the first message doesn't
    match anything (see studio_message()'s connector-less branch above);
    "hi" alone lands there and 'custom' asks its own ask_user clarifying
    question. If the person's very next message is simply "gmail" — clearly
    meaning "connect me to the real Gmail" — _detect_connector_switch alone
    doesn't catch it (no connect-ish verb), so it used to be swallowed as
    literal answer text for the 'custom' connector's pending question. That
    connector will happily riff on the word "gmail" as a mockup theme and
    invent an entire fake, alarming-looking inbox (fabricated unread
    counts, phishing-style subject lines like "Your account has been
    compromised") instead of ever connecting the real thing — exactly the
    opposite of what someone typing just "gmail" wanted. A bare, exact
    connector name is about as unambiguous a signal as text gets, so this
    check fires even without a connect-ish verb and even mid-clarify — see
    its use in studio_message() below, checked alongside
    _detect_connector_switch before any message is forwarded to the
    current connector's agent."""
    t = (text or "").strip().lower().strip(string.punctuation + " ")
    if not t:
        return None
    for key, cfg in CONNECTORS.items():
        if key == "custom":
            continue
        if t == key or t == cfg["label"].lower():
            return key
    return None


def _detect_connector_switch(text, current_connector):
    """Recognizes a mid-conversation request to connect/switch to a
    DIFFERENT app than the one this workflow is already talking to — e.g.
    "now connect slack", "switch to slack", "integrate slack" — so it can
    be handled the same way the very first "which app?" answer is, instead
    of being forwarded to the current connector's agent as if it were a
    real data request (which is exactly what used to happen: a message
    like "now connected to slack" sent to agent_gmail as literal text has
    nothing to fetch or render, so it just burned all 8 steps rejecting an
    empty render_view and surfaced a generic "couldn't build that" error —
    confusing, since nothing was actually wrong with either connector).

    Deliberately requires BOTH a connect-ish verb (connect/switch/
    integrate/...) AND a recognized app name that differs from the
    current connector — "show me slack-related emails" wouldn't trigger
    this (no connect-ish verb), and "connect gmail" while already on Gmail
    wouldn't either (same connector, nothing to switch). Returns the
    matched connector key, or None if this doesn't look like a switch
    request. Plain substring checks, not word-boundary regex — deliberate,
    so "now connected to slack" still matches on "connect" even though
    "connected" isn't a standalone word match."""
    t = (text or "").strip().lower()
    if not any(word in t for word in _SWITCH_TRIGGER_WORDS):
        return None
    matched = _match_connector(text)
    if matched and matched != current_connector:
        return matched
    return None


def _chat_html(wf):
    if not wf["messages"]:
        if wf["connector"] is None:
            bubbles = (
                '<div class="chat-bubble chat-bubble-assistant">'
                "Tell me what you&#39;d like built and I&#39;ll put the interface together for you &mdash; "
                "afterward you can ask me to update its data or add new actions to it any time. If "
                f"you&#39;d rather connect a real app directly, just type its name &mdash; {_esc(_connector_list_text())}."
                '</div>'
                '<div class="chat-bubble chat-bubble-assistant">'
                "So, what would you like to build?"
                '</div>'
            )
        else:
            connector_label = CONNECTORS[wf["connector"]]["label"]
            source, ex1, ex2 = _WELCOME_EXAMPLES.get(wf["connector"], (
                f"your connected {connector_label}",
                "show me what's recent",
                "summarize what's happened this week",
            ))
            bubbles = (
                '<div class="chat-bubble chat-bubble-assistant">'
                f"Hi, I&#39;m Pilant Studio. Describe the interface you want and I&#39;ll build it from "
                f"{_esc(source)}. Try &quot;{_esc(ex1)}&quot; or &quot;{_esc(ex2)}.&quot;"
                '</div>'
            )
            if wf["connector"] == "gmail":
                # Added 2026-08-26, REWRITTEN same day once the live preview
                # became a real, working inbox panel (star/delete/mark-read
                # all work right there now, not just in /inbox) — this used
                # to send people to /inbox for anything real; now it only
                # needs to point out where reply/search/folders still live,
                # since those genuinely need more room than the inline panel.
                bubbles += (
                    '<div class="chat-bubble chat-bubble-assistant">'
                    'Your live inbox is on the right &mdash; star, delete, and compose work directly '
                    'from there. For reply, search, and folders, open the <a href="/inbox?folder=inbox">full inbox</a> '
                    'in the sidebar.'
                    '</div>'
                )
    else:
        # "improvement idea #3" (added 2026-08-29): an assistant message
        # that built something used to carry its own compact inline result
        # card (render_chat_inline_result) right under its bubble, so
        # scrolling back through the chat showed what each turn actually
        # found. REMOVED 2026-08-31 at the user's explicit request, after
        # the merged 'unified' connector's per-app-grouped card (Gmail/
        # Helpdesk/Jira sections) started reading as clutter duplicating
        # the live preview panel right next to it — the chat is now plain
        # conversational bubbles only, and the built screen lives solely
        # in the live preview. m["render"] snapshots are still stored on
        # each message (other code may still use them; nothing upstream
        # changed) — this loop just no longer renders a card from them.
        # render_chat_inline_result itself is left intact in renderer.py,
        # unused for now, in case this is ever wanted back.
        parts = []
        for m in wf["messages"]:
            parts.append(
                f'<div class="chat-bubble chat-bubble-{_esc(m["role"])}">{_render_chat_markdown(m["text"])}</div>'
            )
        bubbles = "".join(parts)
    return (
        '<div class="chatpane">'
        f'<div class="chat-scroll">{bubbles}</div>'
        '<form class="chat-form" method="post" action="/studio/message">'
        '<input type="text" name="text" placeholder="Describe the interface you want..." autocomplete="off">'
        '<button type="submit">Send</button>'
        '</form>'
        '</div>'
    )


def _jira_detail_panel_html(issue, back_url="/studio"):
    """Real read+write detail view for one Jira issue — added 2026-09-01 at
    the user's explicit request ("read and write and workflow all of them")
    after a reference screenshot showed a ticket's full fields plus a real
    action button. Shows every one of connectors_jira.ISSUES' crowded
    fields for THIS issue's own real current values (re-fetched via
    get_issue, never the model's trimmed-down row summary), reusing the
    same .panel/.kv-grid/.badge classes every other generated screen
    already uses so it looks like part of the same product. "Mark
    resolved" POSTs to /studio/jira_resolve/<key>, which genuinely mutates
    connectors_jira's in-memory ISSUES store via set_issue_status — a real,
    if ephemeral (mock-connector, in-memory) write, not a themed button
    with nothing behind it. Gmail rows already have their own real
    read+write panel (the embedded gmail-panel-* machinery above); Slack/
    GitHub/Helpdesk don't have a write connector at all yet, so this
    pattern will extend to them once one exists rather than faking a
    button now with no real action behind it."""
    key = _esc(issue["key"])
    tone = "good" if issue["status"] == "Done" else ("critical" if issue["priority"] in ("High", "Highest") else "default")
    fields = [
        ("Project", issue["project"]), ("Type", issue["type"]), ("Status", issue["status"]),
        ("Priority", issue["priority"]), ("Assignee", issue["assignee"] or "Unassigned"),
        ("Reporter", issue["reporter"]), ("Sprint", issue["sprint"] or "Backlog"),
        ("Epic", issue["epic"] or "—"),
        ("Story points", issue["story_points"] if issue["story_points"] is not None else "—"),
        ("Labels", ", ".join(issue["labels"]) or "—"),
        ("Components", ", ".join(issue["components"]) or "—"),
        ("Fix version", issue["fix_version"] or "—"), ("Due", issue["due"] or "—"),
        ("Created", issue["created"]), ("Updated", issue["updated"]),
        ("Watchers", issue["watchers"]), ("Comments", issue["comments"]),
    ]
    fields_html = "".join(f'<div><dt>{_esc(lbl)}</dt><dd>{_esc(val)}</dd></div>' for lbl, val in fields)
    action_html = (
        f'<form method="post" action="/studio/jira_resolve/{key}" style="margin-top:14px;">'
        '<button class="app-btn" type="submit">Mark resolved</button></form>'
    ) if issue["status"] != "Done" else '<p class="panel-sub" style="margin-top:14px;">Already resolved.</p>'
    badge_html = f'<span class="badge {_esc(tone)}">{_esc(issue["priority"])}</span>'
    return (
        f'<a class="back-link" href="{_esc(back_url)}">&larr; back to results</a>'
        '<div class="panel"><div class="panel-head"><div>'
        f'<p class="panel-title">{key} &middot; {_esc(issue["summary"])}</p>'
        f'</div>{badge_html}</div>'
        f'<dl class="kv-grid">{fields_html}</dl>{action_html}</div>'
    )


def _preview_html(wf, panel_folder="inbox", panel_message=None, jira_detail=None):
    # jira_detail: added 2026-09-01 for the real read+write Jira detail panel
    # (the user's request: a click-through detail view with a genuinely
    # working action button, not just a themed panel with nothing behind
    # it — see connectors_jira.get_issue/set_issue_status and the
    # /studio/jira_resolve/<key> route below). Deliberately checked FIRST,
    # ahead of the gmail branch and the generic render_fragment branch below
    # — a Jira row is clickable from EITHER a "jira" workflow's own preview
    # or a "unified" merged screen's Jira section (renderer.py's
    # jira_detail_base rewrites a matching row's url to point here), so this
    # can't be scoped to one connector the way the gmail branch is. Same
    # transient-query-param pattern as panel_message: never stored on wf, so
    # going back returns to exactly what was showing before.
    if jira_detail:
        issue = connectors_jira.get_issue(jira_detail)
        if issue:
            return (
                '<p class="preview-label">Live preview</p>'
                f'<div class="app-window">{_jira_detail_panel_html(issue)}</div>'
            )
        # Bad/stale key (issue no longer exists) — fall through to the
        # normal preview below rather than showing a dead end.
    # Added 2026-08-25, REPLACED 2026-08-26: a Gmail-connected workflow used
    # to get renderer.render_gmail_fragment() — a read-only skin built from
    # the LLM's render_view JSON, which has no real message ID field (see
    # gmail_site.py's top docstring on why that was deliberate: the model
    # was never trusted to pick which real email a delete/star action
    # targets). That meant every real action lived only on the separate
    # /inbox page, one click away — which the user then flagged directly:
    # "still can't control from this page ... just want to extend the
    # interface not a separate full view mode." So this now embeds
    # gmail_site.render_inbox_panel() instead: the SAME deterministic,
    # code-driven inbox /inbox itself shows (never the LLM's rendering),
    # with real working star/delete actions, laid out right here. Compose
    # is a real link into /compose?next=/studio, so sending returns here
    # instead of bouncing to /inbox. Opening a specific message still goes
    # to its own page — a full message deserves more room than this inline
    # panel — but the actions someone actually reaches for from a list
    # (star, delete, mark read) now work without leaving this page. This
    # panel is real live data, not tied to wf["last_render"], so it shows
    # immediately — before the first chat message, unlike every other
    # connector's preview below, which still needs something generated
    # first.
    #
    # `panel_folder` and the folder-tab strip / minimize-maximize header
    # below were added 2026-08-29 at the user's request: the earlier
    # version's only way to reach Sent/Spam/Drafts/etc. was the "Open the
    # full inbox for search, folders, reply, and forward" link out to
    # /inbox — removed now that the panel offers those folders directly,
    # which was the point of extending the interface in the first place
    # rather than sending people to a separate full view.
    if wf["connector"] == "gmail":
        # panel_message: added 2026-08-29 at the user's direct request,
        # after screenshots showed opening a message from the panel
        # navigating away to a separate, differently-branded "Pilant Mail"
        # page — "want to do everything in the studio front page." When
        # set, the panel shows THAT message (subject/body/reply, via
        # gmail_site.render_inbox_panel_message) in place of the list —
        # compose/search/tabs/chips step aside while looking at one
        # message, same as clicking into a message narrows real Gmail's
        # own view, and its own toolbar's back arrow (or plain browser
        # back) returns to exactly the list scoping (folder/query/chip)
        # that was active before, since panel_message is never stored on
        # wf — see the /studio route's own docstring on that.
        if panel_message:
            fragment = (
                '<div class="gmail-app"><div class="gmail-main" style="flex:1;">'
                f'{render_inbox_panel_message(panel_message, next_url="/studio")}'
                '</div></div>'
            )
        else:
            # panel_query: the real Gmail search this Studio chat's most
            # recent data request actually fetched with
            # (see _handle_studio_message's "render" branch) — added
            # 2026-08-29 so the live preview shows PRECISELY what was asked
            # for ("no from uber.com" actually excludes uber.com, not just
            # the plain unfiltered Inbox regardless of what was typed).
            # Cleared by an explicit folder-tab click (see the /studio
            # route) so switching folders manually always starts from that
            # folder's full contents, not a stale chat filter.
            panel_query = wf.get("gmail_panel_query")
            panel_rows = render_inbox_panel(next_url="/studio", folder=panel_folder, query=panel_query)
            tabs_html = render_inbox_panel_tabs(panel_folder, next_url="/studio")
            compose_link = '<a class="gmail-compose" href="/compose?next=/studio">&#9998; Compose</a>'
            folder_param = "all" if panel_folder is None else panel_folder
            # search_box_value: what the box itself should show — the raw
            # text last typed into IT specifically if that's the active
            # filter's source, else the real query if one is active from
            # elsewhere (chat, or a prior search) so it's still there to
            # read or refine further, else empty. Added 2026-08-29
            # alongside POST /studio/panel_search — a real search box in
            # the panel itself, not just a read-only hint of whatever the
            # chat last fetched with.
            search_box_value = wf.get("gmail_panel_search_text") or panel_query or ""
            search_html = (
                '<form class="gmail-panel-search" method="post" action="/studio/panel_search">'
                f'<input type="hidden" name="panel_folder" value="{_esc(folder_param)}">'
                f'<input type="text" name="panel_q" value="{_esc(search_box_value)}" '
                'placeholder="Search this folder — real syntax (from:, is:unread...) or plain English" '
                'autocomplete="off">'
                '<button type="submit">Search</button>'
                '</form>'
            )
            # chips_html: the one-tap Unread/Starred/Important row
            # ("improvement idea #1", added 2026-08-29 right after the
            # search box above) — its active state just reflects whatever
            # the panel's real query currently is, the actual toggle logic
            # lives in the /studio route.
            chips_html = render_inbox_panel_chips(panel_query, next_url="/studio")
            query_hint_html = (
                f'<p class="gmail-panel-query-hint">Filtered to: <strong>{_esc(panel_query)}</strong> '
                f'<a href="/studio?panel_folder={_esc(folder_param)}">&times; clear</a></p>'
            ) if panel_query else ""
            fragment = (
                '<div class="gmail-app"><div class="gmail-main" style="flex:1;">'
                f'{compose_link}'
                f'{search_html}'
                f'{tabs_html}'
                f'{chips_html}'
                f'{query_hint_html}'
                f'<div class="gmail-list">{panel_rows}</div>'
                '</div></div>'
            )
        request_html = (
            f'<p class="preview-request">&ldquo;{_esc(wf["last_request_text"])}&rdquo;</p>'
            if wf["last_request_text"] else ""
        )
        header_html = (
            '<div class="gmail-panel-header">'
            '<p class="preview-label">Live preview</p>'
            '<div class="gmail-panel-controls">'
            '<button type="button" class="panel-ctrl-btn" title="Minimize" '
            'onclick="pilantTogglePanel(\'gmailPanel\',\'min\')">&#8722;</button>'
            '<button type="button" class="panel-ctrl-btn" title="Maximize" '
            'onclick="pilantTogglePanel(\'gmailPanel\',\'max\')">&#10530;</button>'
            '</div></div>'
        )
        return (
            '<div class="gmail-panel" id="gmailPanel">'
            f'{header_html}'
            f'{request_html}'
            f'<div class="app-window">{fragment}</div>'
            '</div>'
            f'{GMAIL_PANEL_TOGGLE_SCRIPT}'
        )

    if not wf["last_render"]:
        return (
            '<div class="preview-empty">'
            '<p>Live preview</p>'
            '<p class="preview-hint">Nothing generated yet &mdash; describe what you want in the chat on the left.</p>'
            '</div>'
        )
    fragment = render_fragment(
        wf["last_render"], connector_links=_CONNECTOR_LINKS_BY_LABEL, connector_colors=_CONNECTOR_COLORS_BY_LABEL,
        jira_detail_base="/studio?jira_detail=",
        # Added 2026-09-06, fixing a real "how do I navigate from here?"
        # gap: a single-app Jira workflow's own render often leaves its
        # list untitled (or titled something like "Priya's In-Progress
        # Issues" with no "jira" substring), which used to silently starve
        # every row of its detail-panel link — see render_fragment's
        # jira_only docstring. Every row on a Jira-connector workflow's
        # preview unambiguously IS a Jira issue, so bypass the title
        # heuristic entirely here.
        jira_only=(wf["connector"] == "jira"),
    )
    return (
        '<p class="preview-label">Live preview</p>'
        f'<p class="preview-request">&ldquo;{_esc(wf["last_request_text"])}&rdquo;</p>'
        f'<div class="app-window">{fragment}</div>'
    )


def _page_shell(user, title, body):
    # GSAP_SCRIPT (renderer.py, added 2026-08-26 — see that module's and
    # the pilant-interface-motion skill's docstrings for the full
    # reasoning) is appended here, once, on the SHARED shell every Studio
    # page renders through — not duplicated per page or per connector, and
    # not something a fresh render_studio()/render_composer() call needs to
    # remember to add itself.
    out = (
        "<!doctype html><html><head><meta charset=\"utf-8\"><title>{{TITLE}}</title>"
        "<link rel=\"preconnect\" href=\"https://fonts.googleapis.com\">"
        "<link rel=\"stylesheet\" href=\"https://fonts.googleapis.com/css2?family=Sora:wght@600;700&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap\">"
        "<style>{{CSS}}</style></head><body class=\"studio-body\">"
        "<div class=\"topbar\">"
        "<div class=\"topbar-left\">"
        "<a class=\"view-toggle-btn\" href=\"/inbox?folder=inbox\" title=\"Open the full inbox\">&#128229;</a>"
        "<p class=\"brand\">Pilant Studio</p>"
        "</div>"
        "<span class=\"session-row\">logged in as {{USER}} &middot; <a href=\"/logout\">log out</a></span></div>"
        "<div class=\"shell\">{{BODY}}</div>"
        + GSAP_SCRIPT +
        "</body></html>"
    )
    out = out.replace("{{TITLE}}", f"Pilant Studio — {_esc(title)}")
    out = out.replace("{{CSS}}", SHARED_CSS + APP_CSS + GMAIL_CSS + STUDIO_LIGHT_OVERRIDE_CSS)
    out = out.replace("{{USER}}", _esc(user["name"]))
    out = out.replace("{{BODY}}", body)
    return out


def render_studio(user, wf, panel_folder="inbox", panel_message=None, jira_detail=None):
    body = (
        f'{_sidebar_html(wf["id"])}'
        f'{_chat_html(wf)}'
        '<div class="previewpane">'
        f'{_preview_html(wf, panel_folder=panel_folder, panel_message=panel_message, jira_detail=jira_detail)}'
        '</div>'
    )
    return _page_shell(user, wf["title"], body)


def render_integrations(user, notice=None, notice_kind=None, category=None, installed_only=False):
    body = (
        f'{_sidebar_html(None, active_nav="integrations")}'
        f'<div class="integrations-main">{_integrations_page_html(notice, notice_kind, category, installed_only)}</div>'
    )
    return _page_shell(user, "Integrations", body)


# Tiny vanilla-JS layer for the Composer's visual canvas (GET/POST
# /composer below) — no framework, this is a server-rendered Flask app.
# Each primitive is a real <input type=checkbox name=primitive_ids> inside
# a <label> card (see _composer_palette_html): CLICKING a card always
# works, since it's just a native checkbox toggle — that's the reliable
# path. Dragging a card onto the canvas dropzone is layered ADDITIONALLY
# on top of that same checkbox (see ondragstart/ondrop below), for the
# actual "drag primitives onto a canvas" feel DynamisOS's site describes —
# but it's not the only way to select one, since real HTML5 drag-and-drop
# can be finicky and a prototype composer shouldn't be unusable if it
# misfires. syncCanvas() is the single source of truth either way: it
# just reads whichever checkboxes are currently checked and rebuilds the
# canvas pill list from that — drag and click both just flip a checkbox,
# nothing else.
_COMPOSER_JS = """
function syncCanvas() {
  var zone = document.getElementById('composer-canvas-zone');
  var checked = Array.prototype.slice.call(document.querySelectorAll('input[name=primitive_ids]:checked'));
  zone.innerHTML = '';
  if (!checked.length) {
    var empty = document.createElement('p');
    empty.className = 'composer-canvas-empty';
    empty.textContent = 'Drag primitive cards here, or click one on the left, to include it.';
    zone.appendChild(empty);
    return;
  }
  checked.forEach(function(cb) {
    var label = cb.closest('.primitive-card').querySelector('.primitive-card-label').textContent;
    var card = document.createElement('div');
    card.className = 'primitive-card selected';
    card.style.cursor = 'default';
    var p = document.createElement('p');
    p.className = 'primitive-card-label';
    p.textContent = label;
    var remove = document.createElement('a');
    remove.href = '#';
    remove.style.fontSize = '.72rem';
    remove.style.color = 'var(--text-muted)';
    remove.textContent = 'remove';
    remove.addEventListener('click', function(ev) {
      ev.preventDefault();
      cb.checked = false;
      onPrimitiveToggle(cb);
    });
    card.appendChild(p);
    card.appendChild(remove);
    zone.appendChild(card);
  });
}
function onPrimitiveToggle(cb) {
  cb.closest('.primitive-card').classList.toggle('selected', cb.checked);
  syncCanvas();
}
function allowDrop(ev) { ev.preventDefault(); ev.currentTarget.classList.add('drag-over'); }
function dragLeave(ev) { ev.currentTarget.classList.remove('drag-over'); }
function dropPrimitive(ev) {
  ev.preventDefault();
  ev.currentTarget.classList.remove('drag-over');
  var id = ev.dataTransfer.getData('text/plain');
  var cb = document.querySelector('input[name=primitive_ids][value="' + id + '"]');
  if (cb) { cb.checked = true; onPrimitiveToggle(cb); }
}
document.addEventListener('DOMContentLoaded', syncCanvas);
"""


def _composer_palette_html(selected_ids):
    selected = set(selected_ids or [])
    cards = []
    for p in REGISTRY.all():
        checked = "checked" if p["id"] in selected else ""
        sel_cls = " selected" if p["id"] in selected else ""
        conn = p["connector"] or "cross-source"
        cards.append(
            f'<label class="primitive-card{sel_cls}" draggable="true" '
            f'ondragstart="event.dataTransfer.setData(\'text/plain\', \'{_esc(p["id"])}\')">'
            f'<input type="checkbox" name="primitive_ids" value="{_esc(p["id"])}" {checked} '
            f'style="display:none" onchange="onPrimitiveToggle(this)">'
            f'<p class="primitive-card-label">{_esc(p["label"])}</p>'
            f'<p class="primitive-card-conn">{_esc(conn)}</p>'
            '</label>'
        )
    return "".join(cards)


def _composer_saved_list_html(username):
    # Only "composition" saves (ones with primitive_ids) show here — a
    # plain query-only saved view (server.py's old single-connector
    # feature) is a different thing and wouldn't make sense re-opened
    # through agent_composer.run_agent, which always expects the full
    # cross-primitive registry to be in play.
    views = [v for v in list_views(username) if "primitive_ids" in v]
    if not views:
        return ""
    items = "".join(
        '<div class="composer-saved-item">'
        f'<a href="/composer/open/{_esc(v["id"])}">{_esc(v["name"])}</a>'
        f'<form method="post" action="/composer/{_esc(v["id"])}/delete" '
        'onsubmit="return confirm(\'Delete this saved composition?\')">'
        '<button type="submit">Delete</button></form>'
        '</div>'
        for v in views
    )
    return (
        '<div class="composer-saved-list">'
        '<p class="composer-col-label">Saved compositions</p>'
        f'{items}'
        '</div>'
    )


def render_composer(user, selected_ids=None, instruction="", render=None, clarify=None,
                     agent_error=None, opened=None, style_saved=False):
    """The Composer page (GET/POST /composer, GET /composer/open/<id>) —
    see agent_composer.py's and primitives.py's module docstrings for what
    this actually is: a cross-connector alternative to a single-connector
    workflow chat, built on primitives.py's typed registry instead of one
    connector's hard-coded tools. `opened`, when set, is the saved_views.py
    view currently being viewed (re-run live, never a stale snapshot — same
    philosophy saved_views.py's own docstring describes).

    Added 2026-08-26: the "Your interface style" box at the top, backed by
    user_style.py — see that module's and agent_composer.py's docstrings
    for why. Deliberately separate from `instruction` below: instruction is
    WHAT to fetch/show for this one compose; style is HOW this person wants
    it shaped, persisted, and applied to every future compose/reopen until
    changed — different lifetimes, different form, own save action
    (POST /composer/style) so saving one never touches the other."""
    palette = _composer_palette_html(selected_ids)
    current_style = user_style.get_style(user["username"])
    style_note = '<p class="composer-note">Style saved — it&#39;ll shape every composition from here on.</p>' if style_saved else ""
    style_box = (
        '<div class="composer-style-box">'
        '<p class="composer-col-label">Your interface style '
        '<span style="font-weight:400;text-transform:none;letter-spacing:0;color:var(--text-muted);"> '
        '&mdash; how YOUR compositions look, not what data you see</span></p>'
        f'{style_note}'
        '<form method="post" action="/composer/style">'
        '<textarea class="composer-instruction" name="style" placeholder="Optional: describe how you like your interface shaped, e.g. '
        '&#39;I prefer compact lists over stat grids, grouped by urgency&#39; &mdash; leave blank for no preference"'
        f'>{_esc(current_style)}</textarea>'
        '<button class="btn btn-secondary" type="submit">Save style</button>'
        '</form>'
        '</div>'
    )
    result_html = ""
    if clarify:
        result_html = f'<p class="composer-note">Needs more detail: &ldquo;{_esc(clarify)}&rdquo; Try rephrasing your request with that answered in.</p>'
    elif agent_error:
        result_html = f'<p class="composer-note">{_esc(agent_error)}</p>'
    elif render:
        fragment = render_fragment(render)
        save_form = ""
        if not opened:
            hidden_ids = "".join(f'<input type="hidden" name="primitive_ids" value="{_esc(i)}">' for i in (selected_ids or []))
            save_form = (
                '<form class="composer-save-form" method="post" action="/composer/save">'
                f'<input type="hidden" name="instruction" value="{_esc(instruction)}">'
                f'{hidden_ids}'
                '<input type="text" name="name" placeholder="Name this composition to save it...">'
                '<button class="btn" type="submit">Save</button>'
                '</form>'
            )
        result_html = f'<div class="composer-result"><div class="app-window">{fragment}</div>{save_form}</div>'

    opened_banner = ""
    delete_form = ""
    if opened:
        opened_banner = f'<p class="composer-note">Reopened &ldquo;{_esc(opened["name"])}&rdquo; &mdash; regenerated from live data just now.</p>'
        delete_form = (
            f'<form method="post" action="/composer/{_esc(opened["id"])}/delete" style="display:inline;" '
            'onsubmit="return confirm(\'Delete this saved composition?\')">'
            '<button class="btn btn-secondary" type="submit">Delete this composition</button></form>'
        )

    body = (
        f'{_sidebar_html(None, active_nav="composer")}'
        '<div class="integrations-main"><div class="composer-page">'
        '<p class="integ-page-title">Composer</p>'
        '<p class="integ-page-sub">Pull real data from more than one connected source into one '
        'screen the model composes &mdash; pick primitives on the canvas, describe what you want, '
        'or both.</p>'
        f'{style_box}'
        f'{opened_banner}'
        '<form method="post" action="/composer">'
        '<div class="composer-grid">'
        '<div class="composer-palette">'
        '<p class="composer-col-label">Available primitives &mdash; click or drag</p>'
        f'{palette}'
        '</div>'
        '<div class="composer-canvas">'
        '<p class="composer-col-label">Canvas</p>'
        '<div class="composer-canvas-zone" id="composer-canvas-zone" '
        'ondragover="allowDrop(event)" ondragleave="dragLeave(event)" ondrop="dropPrimitive(event)">'
        '</div>'
        '</div>'
        '</div>'
        f'<textarea class="composer-instruction" name="instruction" placeholder="Optional: describe what you want in plain language (e.g. \'anything about the Q2 budget, across email and Slack\')">{_esc(instruction)}</textarea>'
        '<button class="composer-compose-btn" type="submit">Compose</button>'
        '</form>'
        f'{result_html}'
        f'{delete_form}'
        f'{_composer_saved_list_html(user["username"])}'
        '</div></div>'
    )
    body += f'<script>{_COMPOSER_JS}</script>'
    return _page_shell(user, "Composer", body)


# --- Customer-360 -----------------------------------------------------------
# Added 2026-08-27: a staff member opens one real customer and gets orders,
# refund status, delivery status, and recommended actions composed onto one
# screen — the actual "too many separate systems" problem this was built
# for, applied to retail/support ops instead of email or chat. Built on
# primitives.py's retail_orders + agent_composer.py's existing multi-source
# composition, the same way composer_open() re-runs a saved canvas
# selection — no new connector file, no new agent. See primitives.py's
# module docstring for why that's the whole point of this being easy.
#
# Deliberately NOT a CONNECTORS[...] entry or a workflow chat: "open this
# customer" is a fixed, unambiguous action (unlike a free-form chat
# request), so it skips the first-turn clarifying question entirely and
# renders straight away, same reasoning composer_open() already uses for a
# saved view.
def _customer_directory():
    """Every distinct customer visible in the connected store's real
    orders, deduped by email (falling back to Shopify's customer_id when an
    order genuinely has no email — a guest checkout, say). Deliberately NOT
    a separate customer database: there isn't one anywhere in this app, and
    inventing one would mean fabricating a directory instead of showing
    who's actually real in the connected store's own data. Returns
    (customers, error) — error is a human-readable string if the store
    isn't configured/reachable, so the route can show that instead of a
    stack trace."""
    try:
        orders = connectors_retail.get_orders()
    except RuntimeError as e:
        return [], str(e)
    by_key = {}
    for o in orders:
        key = o.get("email") or (str(o["customer_id"]) if o.get("customer_id") else None)
        if not key:
            continue
        entry = by_key.setdefault(key, {"key": key, "name": o.get("customer") or key, "order_count": 0})
        entry["order_count"] += 1
    return sorted(by_key.values(), key=lambda c: c["name"].lower()), None


@app.route("/customers")
@login_required
def customers():
    user = get_user(session["username"])
    q = (request.args.get("q") or "").strip().lower()
    directory, error = _customer_directory()
    if q:
        directory = [c for c in directory if q in c["name"].lower() or q in c["key"].lower()]

    if error:
        list_html = f'<p class="composer-note">Can&#39;t reach the connected store: {_esc(error)}</p>'
    elif not directory:
        list_html = '<p class="integ-page-sub">No customers found yet — no real orders in the connected store to derive a list from.</p>'
    else:
        rows = "".join(
            f'<a class="wf-item" style="display:flex;justify-content:space-between;" '
            f'href="/customers/open?key={_esc(c["key"])}">'
            f'<span>{_esc(c["name"])}</span>'
            f'<span style="opacity:.6">{c["order_count"]} order{"s" if c["order_count"] != 1 else ""}</span></a>'
            for c in directory
        )
        list_html = f'<div class="wf-list" style="max-width:520px;">{rows}</div>'

    body = (
        f'{_sidebar_html(None, active_nav="customers")}'
        '<div class="integrations-main"><div class="composer-page">'
        '<p class="integ-page-title">Customers</p>'
        '<p class="integ-page-sub">Open a customer to see their orders, refund status, delivery '
        'status, and recommended actions composed onto one screen &mdash; pulled from the connected '
        'store, live.</p>'
        '<form method="get" action="/customers" class="save-row" style="max-width:420px;">'
        f'<input type="text" name="q" value="{_esc(request.args.get("q") or "")}" placeholder="Search by name or email...">'
        '<button type="submit">Search</button>'
        '</form>'
        f'{list_html}'
        '</div></div>'
    )
    return _page_shell(user, "Customers", body)


@app.route("/customers/open")
@login_required
def customer_open():
    user = get_user(session["username"])
    key = (request.args.get("key") or "").strip()
    if not key:
        return redirect(url_for("customers"))

    request_text = (
        f"Show everything about the customer identified by '{key}': their orders, "
        "refund status (has_refund), and delivery/fulfillment status. If the real data "
        "shows anything worth flagging (e.g. a delayed or unfulfilled order, a refund "
        "already issued), also include a suggestions component with 1-3 short "
        "recommended next actions for the staff member viewing this — otherwise omit "
        "that component entirely."
    )
    result = agent_composer.run_agent(
        request_text, user=user, primitive_ids=["retail_orders"], first_turn=False,
    )

    error = None
    render = result.get("render")
    if "clarify" in result:
        # Shouldn't normally happen (first_turn=False, request_text is fully
        # specified) — surfaced honestly rather than silently swallowed if
        # the model still finds something genuinely ambiguous to ask about.
        error = f"Needs clarification: {result['clarify']}"
    elif render is None:
        error = result.get("error") or "Couldn't compose this customer's view unexpectedly."

    fragment_html = f'<div class="app-window">{render_fragment(render)}</div>' if render else ""
    error_html = f'<p class="composer-note">{_esc(error)}</p>' if error else ""

    body = (
        f'{_sidebar_html(None, active_nav="customers")}'
        '<div class="integrations-main"><div class="composer-page">'
        '<a class="back-link" href="/customers">&larr; All customers</a>'
        f'<p class="integ-page-title">{_esc(key)}</p>'
        f'{error_html}{fragment_html}'
        '</div></div>'
    )
    return _page_shell(user, key, body)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        # Serves the React SPA shell — the real login form is now built
        # client-side against POST /api/login (see the JSON API section
        # below). Legacy form POST handling below is left working as a
        # fallback.
        return _serve_spa()
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    user = verify_login(username, password)
    if user is None:
        return render_login(error="Incorrect username or password.")
    session.clear()
    session["username"] = user["username"]
    return redirect(url_for("studio"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    return redirect(url_for("studio"))


@app.route("/studio")
@login_required
def studio():
    """Serves the React SPA shell — the real Studio UI (sidebar, chat, live
    preview) is now built client-side against the JSON API below (see
    "--- JSON API (React frontend) ---" further down this file). The old
    server-rendered version of this route (query-param-driven panel_folder/
    panel_chip/panel_message/jira_detail handling + render_studio()) was
    removed 2026-09-07 during the React migration; render_studio/_chat_html/
    _preview_html/_sidebar_html are left defined but unused rather than
    deleted, in case any of that HTML-string logic is wanted again."""
    return _serve_spa()


@app.route("/studio/<workflow_id>")
@login_required
def studio_workflow_spa(workflow_id):
    """Serves the SPA shell for React Router's own "/studio/:id" route (a
    direct load or a browser refresh on that URL, not client-side
    navigation — the SPA already handles that without a real request).
    Registered AFTER the more specific single-segment /studio/<literal>
    GET routes above (gmail_panel_frame) and matched only if none of those
    literal rules win first — Werkzeug's routing always prefers a static
    path segment over a <converter> one at the same position, so this can't
    accidentally swallow a real named route; it only catches an actual
    workflow id (or a stale/typo'd one, which the SPA's own fetch to
    GET /api/workflows/<id> will report as not_found, same as the API
    already handles today). Does NOT validate workflow_id against
    WORKFLOWS here on purpose — that check belongs to the JSON API the SPA
    calls after it mounts, not to whichever route happened to serve the
    static shell."""
    return _serve_spa()


@app.route("/workspace")
@app.route("/workspace/<connector_key>")
@login_required
def workspace_spa(connector_key=None):
    """Serves the SPA shell for the Workspace screen's React Router routes
    ("/workspace" and "/workspace/:connector") — same reasoning as
    studio_workflow_spa above: a direct load or refresh needs Flask to hand
    back the same static shell; the real per-connector page is built
    client-side (WorkspacePage.jsx) against the same JSON API every other
    screen already uses (no new workflow model — a "workspace" is just the
    connector's existing workflow, found or created via
    GET/POST /api/workflows)."""
    return _serve_spa()


@app.route("/studio/panel_search", methods=["POST"])
@login_required
def studio_panel_search():
    """Handles a submission of the embedded panel's OWN search box (added
    2026-08-29 — see resolve_panel_query's docstring in gmail_site.py for the
    "real syntax or plain English, either works" translation it does). This
    is separate from the chat box: it searches the panel directly, the same
    way /inbox's own search box searches the full page, without going
    through the LLM at all.

    Deliberately redirects to plain /studio — NOT /studio?panel_folder=... —
    because the /studio route above treats an explicit ?panel_folder= as a
    folder-TAB click and clears any active query/search text as part of
    that. Landing back on /studio with no query param instead falls through
    to wf["gmail_panel_folder"]/wf["gmail_panel_query"], which this route
    sets directly below, so the just-submitted search actually sticks.
    """
    wf = _current_workflow()
    if wf["connector"] != "gmail":
        return redirect(url_for("studio"))
    raw_folder = request.form.get("panel_folder")
    panel_folder = resolve_panel_folder(raw_folder) if raw_folder is not None else wf.get("gmail_panel_folder", "inbox")
    raw_q = request.form.get("panel_q", "")
    wf["gmail_panel_folder"] = panel_folder
    wf["gmail_panel_search_text"] = raw_q.strip() or None
    wf["gmail_panel_query"] = resolve_panel_query(raw_q)
    return redirect(url_for("studio"))


@app.route("/studio/jira_resolve/<key>", methods=["POST"])
@login_required
def studio_jira_resolve(key):
    """The Jira detail panel's real write action (see
    _jira_detail_panel_html and connectors_jira.set_issue_status) — marks
    the issue Done in connectors_jira's in-memory ISSUES store, a genuine
    mutation (not a themed button with nothing behind it), then redirects
    back into the SAME detail view so the person sees the status actually
    change. Not scoped to any one workflow (unlike the gmail_panel_* routes
    above) since a Jira issue isn't owned by a particular workflow the way
    the embedded Gmail panel is owned by a "gmail" workflow — any workflow
    showing this issue's row would show the same updated status on its
    next fetch."""
    connectors_jira.set_issue_status(key, "Done")
    return redirect(f"/studio?jira_detail={key}")


@app.route("/integrations")
@login_required
def integrations():
    """Serves the React SPA shell — the real Integrations page is now built
    client-side against GET /api/integrations (see the JSON API section
    below). The OAuth routes further down redirect back to this same path
    with ?notice=&kind= query params instead of a session-flash + server
    render, since Google's callback has to redirect somewhere and the SPA
    reads/clears those params on mount."""
    return _serve_spa()


@app.route("/composer", methods=["GET", "POST"])
@login_required
def composer():
    """GET shows the canvas with nothing composed yet. POST runs
    agent_composer.run_agent() with whatever primitives were selected on
    the canvas (request.form.getlist — zero, one, or many checkboxes) and
    whatever free-text instruction was typed, exactly the same "real
    render_view call, real guardrails" pipeline every workflow chat already
    uses — see agent_composer.py's module docstring for what's actually
    different about it (it can call primitives from more than one
    connector in the same request)."""
    user = get_user(session["username"])
    if request.method == "GET":
        return render_composer(user)

    selected_ids = request.form.getlist("primitive_ids")
    instruction = (request.form.get("instruction") or "").strip()
    if not selected_ids and not instruction:
        return render_composer(user, agent_error="Pick at least one primitive on the canvas, or describe what you want, before composing.")

    # Selected primitives are named explicitly; a bare instruction with no
    # canvas selection still works — the model picks primitives itself,
    # same as any other connector's chat (see agent_composer.py's SYSTEM).
    request_text = instruction or f"Show me: {', '.join(selected_ids)}"

    # first_turn: added 2026-08-26, same DynamisOS-comparison behavior as
    # the four real connector chats (see CONNECTORS' "first_turn" flag and
    # agent_gmail.py's FIRST_TURN_CLARIFY_PARAGRAPH docstring for the full
    # reasoning) — a bundled purpose/style/(source) question asked once
    # before the first real compose. The Composer has no conversational
    # memory of its own (see this route's and agent_composer.py's
    # docstrings: every submission is independent, "rephrase with the
    # answer baked in" rather than a true multi-turn resume like the
    # workflow chats), so "once per conversation" doesn't map cleanly here
    # — the closest honest equivalent is "once per login session": ask the
    # bundled question on this user's very first compose attempt since
    # logging in, then never again, tracked in the session cookie rather
    # than in agent_messages the way the workflow chats do it.
    first_turn = not session.get("composer_first_turn_done")
    if first_turn:
        session["composer_first_turn_done"] = True
    result = agent_composer.run_agent(request_text, user=user, primitive_ids=selected_ids or None, first_turn=first_turn)

    if "render" in result:
        return render_composer(user, selected_ids=selected_ids, instruction=instruction, render=result["render"])
    if "clarify" in result:
        return render_composer(user, selected_ids=selected_ids, instruction=instruction, clarify=result["clarify"])
    return render_composer(user, selected_ids=selected_ids, instruction=instruction,
                            agent_error=result.get("error") or "Composing failed unexpectedly.")


@app.route("/composer/style", methods=["POST"])
@login_required
def composer_style():
    """Saves THIS user's interface-style preference (user_style.py) —
    separate action from composing, since it has its own lifetime (set
    once, applies to every future compose/reopen) rather than being tied
    to one request. See render_composer()'s and agent_composer.py's
    docstrings for why this exists at all: without it, two users asking
    for the same thing with the same primitives got the identical screen,
    which is agent-COMPOSED but not agent-CUSTOMIZED per person."""
    user = get_user(session["username"])
    user_style.set_style(user["username"], request.form.get("style") or "")
    return render_composer(user, style_saved=True)


@app.route("/composer/save", methods=["POST"])
@login_required
def composer_save():
    user = get_user(session["username"])
    name = (request.form.get("name") or "").strip()
    instruction = (request.form.get("instruction") or "").strip()
    primitive_ids = request.form.getlist("primitive_ids")
    if not primitive_ids and not instruction:
        return redirect(url_for("composer"))
    saved = save_view(user["username"], name, instruction, primitive_ids=primitive_ids)
    return redirect(url_for("composer_open", view_id=saved["id"]))


@app.route("/composer/open/<view_id>")
@login_required
def composer_open(view_id):
    """Same 'not a snapshot, a real live re-run' philosophy saved_views.py's
    docstring describes for the old single-connector version — every open
    re-runs agent_composer.run_agent() against real, current data. A
    view_id that doesn't exist or belongs to someone else looks identical
    (redirect to a fresh Composer), same trust boundary saved_views.get_view
    already enforces."""
    user = get_user(session["username"])
    saved = get_view(user["username"], view_id)
    if saved is None:
        return redirect(url_for("composer"))

    primitive_ids = saved.get("primitive_ids") or []
    instruction = saved.get("query") or ""
    request_text = instruction or f"Show me: {', '.join(primitive_ids)}"
    result = agent_composer.run_agent(request_text, user=user, primitive_ids=primitive_ids or None)

    if "render" in result:
        return render_composer(user, selected_ids=primitive_ids, instruction=instruction,
                                render=result["render"], opened=saved)
    if "clarify" in result:
        return render_composer(user, selected_ids=primitive_ids, instruction=instruction,
                                clarify=(f"Couldn't regenerate \"{saved['name']}\" — it needs clarification: "
                                         f"\"{result['clarify']}\". Try running it as a new request instead."),
                                opened=saved)
    return render_composer(user, selected_ids=primitive_ids, instruction=instruction,
                            agent_error=f"Couldn't regenerate \"{saved['name']}\" ({result.get('error', 'unexpected failure')}).",
                            opened=saved)


@app.route("/composer/<view_id>/delete", methods=["POST"])
@login_required
def composer_delete(view_id):
    user = get_user(session["username"])
    delete_view(user["username"], view_id)
    return redirect(url_for("composer"))


def _integrations_redirect(notice, kind):
    """Redirects to the (SPA-served) /integrations page carrying a one-shot
    notice as query params instead of a session flash — used only by the
    two OAuth legs below, which have to end in a real browser redirect
    (Google controls oauth_gmail_connect's destination and is the one
    calling oauth_gmail_callback), so there's no way to just return JSON
    from a fetch() call the way rag_sync/oauth_gmail_disconnect now do.
    The React app reads ?notice=&kind= on mount and clears them via
    history.replaceState so a page refresh doesn't keep re-showing it."""
    return redirect("/integrations?" + urlencode({"notice": notice, "kind": kind}))


@app.route("/rag/sync", methods=["POST"])
@login_required
def rag_sync():
    """Runs rag_ingest.ingest_all() and returns a JSON summary of what
    happened per connector, for the React Integrations page's "Sync now"
    button (a fetch() call, not a full page navigation — unlike the OAuth
    routes below, nothing external needs to redirect the browser here, so
    this can just answer directly instead of round-tripping through a
    session-flash + redirect)."""
    if not rag_index.is_configured():
        return jsonify({
            "ok": False, "kind": "error",
            "notice": (
                "Can't sync: AWS_BEARER_TOKEN_BEDROCK isn't set in .env (a Bedrock long-term API "
                "key, needed to generate search embeddings). Add it and restart Studio, then try again."
            ),
        }), 400

    results = rag_ingest.ingest_all()
    parts = []
    any_error = False
    for name, outcome in results.items():
        label = CONNECTORS[name]["label"] if name in CONNECTORS else name
        if "error" in outcome:
            any_error = True
            parts.append(f"{label}: {outcome['error']}")
        else:
            parts.append(f"{label}: {outcome['indexed']} indexed")
    return jsonify({
        "ok": not any_error,
        "kind": "error" if any_error else "ok",
        "notice": "Sync complete — " + "; ".join(parts),
    })


@app.route("/oauth/gmail/connect")
@login_required
def oauth_gmail_connect():
    """Starts the real Google sign-in — redirects the browser to Google's
    own OAuth consent page. Nobody types a Gmail password into this app;
    Google collects it on Google's own page and redirects back here with
    just an authorization code. Uses the fixed GMAIL_OAUTH_REDIRECT_URI
    (see its comment above) rather than reflecting back whatever host is
    in the current request — Google needs that exact string registered in
    the Cloud Console, so it can't be allowed to vary. This is a real
    top-level browser navigation (a plain <a href> in the React app, never
    a fetch() call) since Google's consent page has to take over the
    whole tab."""
    print(f"[studio] Gmail OAuth redirect_uri: {GMAIL_OAUTH_REDIRECT_URI}", file=sys.stderr)
    print("[studio]   ^ must be registered EXACTLY in Google Cloud Console -> APIs & Services", file=sys.stderr)
    print("[studio]     -> Credentials -> your OAuth 2.0 Client ID -> Authorized redirect URIs", file=sys.stderr)
    state = secrets.token_urlsafe(24)
    session["gmail_oauth_state"] = state
    try:
        auth_url = connectors_gmail.get_authorization_url(GMAIL_OAUTH_REDIRECT_URI, state=state)
    except RuntimeError as e:
        return _integrations_redirect(str(e), "error")
    return redirect(auth_url)


@app.route("/oauth/gmail/callback")
@login_required
def oauth_gmail_callback():
    """Where Google sends the browser back to after the person signs in
    (or cancels). `state` is checked against what oauth_gmail_connect
    stashed in session, to make sure this callback is actually completing
    a sign-in this app started — not a forged/replayed callback URL. Uses
    the same fixed GMAIL_OAUTH_REDIRECT_URI oauth_gmail_connect sent
    Google — it must be byte-identical on both legs or Google rejects the
    code exchange with invalid_grant."""
    redirect_uri = GMAIL_OAUTH_REDIRECT_URI
    expected_state = session.pop("gmail_oauth_state", None)
    error = request.args.get("error")
    if error:
        return _integrations_redirect(f"Google sign-in was cancelled or denied ({error}).", "error")
    if not expected_state or request.args.get("state") != expected_state:
        return _integrations_redirect("That sign-in link looks stale or tampered with — try connecting again.", "error")
    code = request.args.get("code")
    if not code:
        return _integrations_redirect("Google didn't return an authorization code — try connecting again.", "error")
    try:
        email_address = connectors_gmail.exchange_code_for_tokens(code, redirect_uri)
    except RuntimeError as e:
        return _integrations_redirect(str(e), "error")
    return _integrations_redirect(f"Connected Gmail as {email_address}.", "ok")


@app.route("/oauth/gmail/disconnect", methods=["POST"])
@login_required
def oauth_gmail_disconnect():
    """A fetch() call from the React Integrations page, not a full page
    navigation (unlike connect/callback above, nothing external is
    involved) — answers directly with JSON instead of redirecting."""
    connectors_gmail.disconnect()
    return jsonify({
        "ok": True, "kind": "ok",
        "notice": "Disconnected. New requests will fall back to the shared inbox configured in .env, if any.",
    })


@app.route("/studio/new", methods=["POST"])
@login_required
def new_workflow():
    wf = _create_workflow()  # connector=None — the chat asks which app next
    session["current_workflow"] = wf["id"]
    return redirect(url_for("studio"))


@app.route("/studio/new/<connector_key>", methods=["POST"])
@login_required
def new_workflow_with_connector(connector_key):
    """The integrations-column click path — start a workflow already
    connected to a specific app, so the chat skips straight to that
    connector's welcome message instead of asking "which app?" first. An
    unrecognized connector_key (stale/tampered form) falls back to the
    same connector-less flow the plain "+ New workflow" button uses,
    rather than erroring."""
    connector = connector_key if connector_key in CONNECTORS else None
    wf = _create_workflow(connector=connector)
    session["current_workflow"] = wf["id"]
    return redirect(url_for("studio"))


@app.route("/studio/open/<workflow_id>")
@login_required
def open_workflow(workflow_id):
    if workflow_id in WORKFLOWS:
        session["current_workflow"] = workflow_id
    return redirect(url_for("studio"))


# How many finished exchanges (one user request + one compact summary of
# what happened) a workflow's persisted memory keeps before the oldest ones
# start getting dropped. This is the actual "memory management" part: memory
# is deliberately bounded, not an ever-growing transcript, so a long-lived
# workflow's prompt size stays predictable no matter how many requests it's
# handled. 6 was picked as "enough recent context for a natural back-and-
# forth" without keeping so much history that older, likely-irrelevant
# exchanges start crowding out the model's attention on the CURRENT request.
MEMORY_MAX_EXCHANGES = 6


def _render_memory_line(render):
    """One short, human-readable line summarizing an accepted render_view
    result, for storing in a workflow's persisted memory (see
    _append_memory_exchange below) — e.g. 'Built "Unread emails" — a list of
    8 item(s).' This is deliberately NOT the raw render JSON (too large to
    keep accumulating turn over turn) and NOT the raw internal tool-calling
    transcript that produced it (full of this reliability layer's own retry/
    rejection nudge text, and — see agent_gmail.py's 2026-08-25 multi-tool-
    call fix — replaying old raw tool-call turns back into a fresh
    completion request is exactly the kind of thing this hosted model's own
    chat template has already proven fragile about). A short synthesized
    summary carries enough context for the model to understand what a
    follow-up like 'make it more compact' or 'now add a filter' is
    referring to, without either problem."""
    if not isinstance(render, dict):
        return "Built a screen."
    heading = render.get("heading") or "a screen"
    parts = []
    for comp in (render.get("components") or []):
        if not isinstance(comp, dict):
            continue
        t = comp.get("type")
        if t == "list":
            parts.append(f"a list of {len(comp.get('rows') or [])} item(s)")
        elif t == "stat_grid":
            parts.append(f"{len(comp.get('stats') or [])} stat card(s)")
        elif t == "panel":
            parts.append("a detail panel")
        # 2026-09-05: the 6 new component types (plus "suggestions", which
        # predates them but was never added here either) were silently
        # falling through to "a screen with no components" whenever they
        # were the only thing on a screen — weakening a follow-up request's
        # ("make it more compact", "now add a filter") ability to refer back
        # to what was actually just built. Same one-clause-per-type pattern
        # as the three above, just extended to cover the full vocabulary.
        elif t == "suggestions":
            parts.append(f"{len(comp.get('suggestions') or [])} suggested action(s)")
        elif t == "timeline":
            parts.append(f"a timeline of {len(comp.get('rows') or [])} event(s)")
        elif t == "metric":
            stats = comp.get("stats") or []
            label = stats[0].get("label") if stats and isinstance(stats[0], dict) else None
            parts.append(f"a headline metric ({label})" if label else "a headline metric")
        elif t == "data_table":
            parts.append(f"a table of {len(comp.get('table_rows') or [])} row(s)")
        elif t == "chart":
            parts.append(f"a chart with {len(comp.get('stats') or [])} bar(s)")
        elif t == "alert":
            parts.append("an alert banner")
        elif t == "task_queue":
            parts.append(f"a task queue of {len(comp.get('rows') or [])} item(s)")
        # 2026-09-06 (round 3): same reasoning as the block above — these 7
        # new types were falling through to "a screen with no components"
        # whenever one was the only thing on a screen.
        elif t == "detail_view":
            parts.append(f"a detail view of \"{comp.get('title') or 'one record'}\"")
        elif t == "status_badge":
            parts.append(f"a status badge ({comp.get('title')})" if comp.get('title') else "a status badge")
        elif t == "empty_state":
            parts.append(f"an empty-state note (\"{comp.get('title')}\")" if comp.get('title') else "an empty-state note")
        elif t == "error_state":
            parts.append(f"an error card (\"{comp.get('title')}\")" if comp.get('title') else "an error card")
        elif t == "connection_state":
            parts.append(f"a connection-status row of {len(comp.get('stats') or [])} app(s)")
        elif t == "pagination":
            parts.append("a pagination status line")
        elif t == "popover":
            parts.append(f"a popover (\"{comp.get('title')}\")" if comp.get('title') else "a popover")
    body = ", ".join(parts) if parts else "a screen with no components"
    return f'Built "{heading}" — {body}.'


def _append_memory_exchange(wf, user_text, assistant_summary):
    """
    Appends one finished exchange (what the person asked, a compact summary
    of what happened) to a workflow's persisted cross-request memory, seeding
    it with the connector's own system prompt first if this is the first
    exchange since the workflow started (or since it last switched
    connectors / lost its memory some other way). Then trims from the front
    — oldest exchange first, system message always kept — down to
    MEMORY_MAX_EXCHANGES.

    This is the ONLY place wf["memory"] is written to on a successful
    outcome; _run_agent_for_workflow() below calls it after "render" and
    after a plain conversational "text" reply, but deliberately NOT after
    "error" — a failed attempt shouldn't erase or corrupt whatever good
    memory the workflow already had going into it.
    """
    if not wf["memory"]:
        system_content = CONNECTORS[wf["connector"]].get("system")
        wf["memory"] = [{"role": "system", "content": system_content}] if system_content else []
    wf["memory"].append({"role": "user", "content": user_text})
    wf["memory"].append({"role": "assistant", "content": assistant_summary})

    has_system = wf["memory"][0].get("role") == "system"
    head = [wf["memory"][0]] if has_system else []
    body = wf["memory"][1:] if has_system else wf["memory"]
    max_messages = MEMORY_MAX_EXCHANGES * 2
    if len(body) > max_messages:
        body = body[-max_messages:]
    wf["memory"] = head + body


def _run_agent_for_workflow(wf, text):
    """
    Actually calls the current connector's run_agent — resuming an
    in-progress clarify exactly as before if one is pending, otherwise (2026-
    08-25) seeding a FRESH top-level request with the workflow's persisted
    memory (wf["memory"]) when it has any, so a short follow-up like "make it
    more compact" or "now add a filter" has the context of what was already
    built/discussed, instead of every single request starting the agent's
    conversation from nothing. Applies the result to wf's state either way.
    Factored out of studio_message() on 2026-08-25 so the "nothing in the
    registry was named" fallback below — which now treats a free-form app/
    brand description like "clothing brand" as an immediate first build
    request for the 'custom' connector, instead of asking "which app?"
    again — can reuse the exact same call+result-handling logic as a normal
    in-conversation request, rather than duplicating it.
    """
    connector = CONNECTORS[wf["connector"]]
    print(f"[studio] {session['username']} -> {wf['id']} ({wf['connector']}): {text}", file=sys.stderr)

    user = get_user(session["username"])
    try:
        # Only GitHub's run_agent takes a `user` kwarg (for scope_issues()
        # identity filtering, per users.py's label_scope) — every other
        # connector's run_agent signature doesn't accept it at all, so this
        # is only added to the call for connectors that opt in via
        # needs_user in the CONNECTORS registry above.
        call_kwargs = {}
        if connector.get("needs_user"):
            call_kwargs["user"] = user
        # Added 2026-08-30 — layout_usage.py, the answer to "does Pilant
        # learn which layouts are most useful per role/goal": a one-
        # sentence, role-level usage hint folded into this request's
        # system prompt via every connector's extra_system kwarg. Returns
        # None (so call_kwargs stays exactly as before) until there's
        # enough real history for this role+connector to say anything —
        # see that module's MIN_EVENTS_FOR_HINT.
        hint = layout_usage.summarize_usage(user.get("role"), wf["connector"])
        if hint:
            call_kwargs["extra_system"] = hint
        if wf["agent_messages"]:
            # An in-progress clarify round — resume with its exact raw
            # state, unchanged from before memory existed at all.
            agent_messages = wf["agent_messages"] + [{"role": "user", "content": text}]
            result = connector["run_agent"](messages=agent_messages, fetched_data=wf["fetched_data"], **call_kwargs)
        elif wf["memory"]:
            # A brand-new top-level request, but this workflow has finished
            # at least one exchange before — seed with that memory instead
            # of starting from nothing. fetched_data=False (not carried
            # over) so the connector naturally re-fetches current real data
            # for this new request, even though the conversational CONTEXT
            # persists.
            agent_messages = wf["memory"] + [{"role": "user", "content": text}]
            result = connector["run_agent"](messages=agent_messages, fetched_data=False, **call_kwargs)
        else:
            # Genuinely the first request of a brand-new workflow — no
            # in-progress clarify, no prior successful exchange to seed
            # memory from. The one moment first_turn=True fires (see
            # CONNECTORS' "first_turn" flag and agent_gmail.py's
            # FIRST_TURN_CLARIFY_PARAGRAPH docstring) — never on the two
            # branches above, which are either resuming an ask_user round
            # that already asked something, or a follow-up within an
            # established conversation that's already past its first turn.
            if connector.get("first_turn"):
                call_kwargs["first_turn"] = True
            result = connector["run_agent"](text, **call_kwargs)
    except Exception as e:
        # run_agent() already catches its own network/model errors and
        # returns {"error": ...} rather than raising — this is belt-and-
        # braces against a truly unexpected exception, so a bug in the
        # engine degrades to a chat message instead of a broken page.
        wf["messages"].append({
            "role": "assistant",
            "text": f"Something went wrong building that ({type(e).__name__}). Try rephrasing — the last working preview is still there on the right.",
        })
        wf["agent_messages"] = None
        wf["fetched_data"] = False
        return

    if "clarify" in result:
        wf["messages"].append({"role": "assistant", "text": result["clarify"]})
        wf["agent_messages"] = result["messages"]
        wf["fetched_data"] = result.get("fetched_data", False)
    elif "error" in result:
        wf["messages"].append({
            "role": "assistant",
            "text": f"Couldn't build that ({result['error']}). Try rephrasing — the last working preview is still there on the right.",
        })
        wf["agent_messages"] = None
        wf["fetched_data"] = False
        # wf["memory"] deliberately left untouched — see _append_memory_exchange()'s docstring.
    elif "render" in result:
        wf["last_render"] = result["render"]
        wf["last_request_text"] = text
        if wf["title"] == "Untitled workflow":
            wf["title"] = text if len(text) <= 42 else text[:41].rstrip() + "…"
        # "render" on the message dict itself (added 2026-08-29, alongside
        # wf["last_render"] above which only ever holds the LATEST turn's
        # result): a snapshot of THIS turn's real, guardrail-verified
        # render_view JSON, kept with the message that produced it so
        # _chat_html can show a compact inline result card right under this
        # specific bubble — "improvement idea #3." Cheap to keep (small
        # JSON, already validated/fabrication-checked by dispatch above);
        # never re-fetched or re-rendered later, just redisplayed.
        wf["messages"].append({
            "role": "assistant",
            "text": "Built it — see the live preview on the right.",
            "render": result["render"],
        })
        wf["agent_messages"] = None
        wf["fetched_data"] = False
        # Added 2026-08-30: the other half of layout_usage.py — an
        # ACCEPTED render (past guardrails, already in result["render"] by
        # this point) is exactly the "what turned out useful" signal
        # summarize_usage() above needs for next time. Only the screen's
        # SHAPE (component "type"s) and the request text are logged, never
        # the underlying data — see layout_usage.py's module docstring.
        layout_usage.record_render(
            user.get("role"), wf["connector"], text,
            [c.get("type") for c in (result["render"].get("components") or []) if isinstance(c, dict)],
        )
        # Added 2026-08-29: for a Gmail workflow, make "see the live preview
        # on the right" literally true — scope the embedded panel to the
        # SAME real folder/query/unread_only this request actually fetched
        # with (result["fetch_args"], set by agent_gmail.py's dispatch),
        # rather than always showing the plain unfiltered Inbox regardless
        # of what was asked for. fetch_args can legitimately be None (an
        # ask_user-resume that seeded fetched_data=True from an earlier
        # turn — see agent_gmail.py's dispatch docstring) — in that case
        # the panel's current scoping is left exactly as it was, not reset.
        if wf["connector"] == "gmail":
            fetch_args = result.get("fetch_args")
            if fetch_args:
                wf["gmail_panel_folder"] = fetch_args.get("folder")
                query_parts = []
                if fetch_args.get("unread_only"):
                    query_parts.append("is:unread")
                if fetch_args.get("query"):
                    query_parts.append(str(fetch_args["query"]))
                wf["gmail_panel_query"] = " ".join(query_parts) or None
                # Chat just took over scoping — drop any stale text sitting
                # in the panel's own search box (POST /studio/panel_search)
                # so it doesn't keep showing an old search that's no longer
                # what's actually driving the panel.
                wf["gmail_panel_search_text"] = None
        _append_memory_exchange(wf, text, _render_memory_line(result["render"]))
    else:
        reply_text = result.get("text") or "I'm not sure how to answer that — try describing a screen you want built."
        wf["messages"].append({"role": "assistant", "text": reply_text})
        wf["agent_messages"] = None
        wf["fetched_data"] = False
        _append_memory_exchange(wf, text, reply_text)


def _handle_chat_message(wf, text):
    """
    The one function that actually talks to the composition engine, given a
    workflow and the person's raw message text. Mutates `wf` in place and
    returns nothing — every failure branch only ever appends to the chat
    transcript, never touches wf["last_render"]. That split is deliberate: a
    failed or ambiguous request should never blank out or corrupt a working
    preview the person already has on screen, only add a message explaining
    what happened.

    Factored out of the old studio_message() route on 2026-09-07 during the
    JSON-API/React migration so BOTH the legacy HTML route (kept for
    backward compatibility) and the new POST /api/workflows/<id>/message
    route can share the exact same connector-selection/switch/dispatch
    logic instead of it living inline in one Flask view function.
    """
    wf["messages"].append({"role": "user", "text": text})

    # No connector chosen yet. If the message names a real, registered app
    # (gmail/slack/github/helpdesk), lock it in with a short confirmation +
    # example prompts, same as before. Otherwise — added 2026-08-25, per the
    # DynamisOS reference screenshot the user showed — treat it as the
    # actual first request for a free-form, custom interface instead of
    # asking "which app?" again: typing "clothing brand" into a brand-new
    # workflow should start building (the custom connector's own ask_user
    # flow will ask its own clarifying question if the description is too
    # thin — see agent_custom.py), not bounce back an "I didn't catch an
    # app" error.
    if wf["connector"] is None:
        # Added 2026-08-31: check for MORE THAN ONE real app named in this
        # one message ("connect gmail and jira") before falling back to
        # the single-match logic below — see _match_all_connectors'
        # docstring for the exact bug this fixes. Routes to 'unified' and
        # acts immediately, same "don't make them repeat themselves"
        # reasoning the single-connector branch below already uses.
        all_matched = _match_all_connectors(text)
        if len(all_matched) >= 2:
            wf["connector"] = "unified"
            labels = [CONNECTORS[k]["label"] for k in all_matched]
            joined = ", ".join(labels[:-1]) + " and " + labels[-1] if len(labels) > 1 else labels[0]
            wf["messages"].append({
                "role": "assistant",
                "text": f"Connected — I can pull from {joined} together here, whichever of them a request actually needs.",
            })
            _run_agent_for_workflow(wf, f"Show me what's relevant across {joined}.")
            return

        name_matched = all_matched[0] if all_matched else None
        if name_matched:
            # The message names (or is) a real connector, e.g. just "gmail"
            # or "let's do slack". UPDATED 2026-08-26, per the user testing
            # this live against the DynamisOS reference twice: naming the
            # app used to just show a static "Connected to X. Try ..."
            # example-prompts message and wait for a SECOND message before
            # ever asking anything — DynamisOS asks its clarifying question
            # immediately off the first thing you say, not after a second
            # round-trip. So this now treats "connect" and "the first real
            # turn" as one step: confirm the connection, THEN immediately
            # call the agent (which, per CONNECTORS' "first_turn" flag and
            # agent_gmail.py's FIRST_TURN_CLARIFY_PARAGRAPH, is forced to
            # ask its bundled purpose/style question before it's allowed to
            # fetch or render anything) — same _run_agent_for_workflow()
            # every other request already goes through, not a special case.
            # A deliberately explicit request_text ("I want to build
            # something with my Gmail.") rather than the bare matched word
            # — verified live that a bare "gmail"/"slack"/"github" still
            # gets a good result most of the time, but an explicit build
            # intent removes the chance a connector with
            # force_tool_choice=False (Gmail/Slack) reads a one-word
            # message as pure small talk and skips the question.
            wf["connector"] = name_matched
            label = CONNECTORS[name_matched]["label"]
            wf["messages"].append({"role": "assistant", "text": f"Connected to {label}."})
            _run_agent_for_workflow(wf, f"I want to build something with my {label}.")
        else:
            keyword_matched = _infer_connector_from_intent(text)
            if keyword_matched:
                # The message didn't name an app, but the wording clearly
                # asks for real data (e.g. "show my inbox") — this text IS
                # the actual request, not just an app name, so connect AND
                # act on it now instead of making them repeat themselves.
                wf["connector"] = keyword_matched
                _run_agent_for_workflow(wf, text)
            else:
                wf["connector"] = "custom"
                _run_agent_for_workflow(wf, text)
        return

    # A connector is already chosen, but this message looks like a request
    # to connect/switch to a DIFFERENT one mid-conversation — e.g. "now
    # connect slack", "switch to slack" — rather than a real data request
    # for the current connector. Handle it the same way the initial "which
    # app?" answer is (switch + confirm), instead of forwarding literal
    # text like that to the current connector's agent, which has nothing
    # to fetch or render from it and would just burn all its steps failing.
    # See _detect_connector_switch()'s docstring for why this only fires
    # on an explicit connect-ish verb plus a different app's name, not on
    # every message that happens to mention another app. The bare-name
    # fallback below (_is_bare_connector_name) catches the narrower, more
    # dangerous case that verb-gating misses: a one-word reply like just
    # "gmail" — see its own docstring for the live bug this fixes.
    switch_target = _detect_connector_switch(text, wf["connector"])
    if not switch_target:
        bare = _is_bare_connector_name(text)
        if bare and bare != wf["connector"]:
            switch_target = bare
    if switch_target:
        wf["connector"] = switch_target
        wf["agent_messages"] = None
        wf["fetched_data"] = False
        # Memory built up for the OLD connector's domain (and seeded with its
        # system prompt) doesn't carry over to a different app — start fresh.
        wf["memory"] = None
        label = CONNECTORS[switch_target]["label"]
        _, ex1, ex2 = _WELCOME_EXAMPLES.get(switch_target, (
            f"your connected {label}",
            "show me what's recent",
            "summarize what's happened this week",
        ))
        wf["messages"].append({
            "role": "assistant",
            "text": f"Switched this workflow to {label}. Try \"{ex1}\" or \"{ex2}.\" "
                    "(The last preview on the right is still from the previous connector until you build something new here.)",
        })
        return

    _run_agent_for_workflow(wf, text)


@app.route("/studio/message", methods=["POST"])
@login_required
def studio_message():
    """Legacy server-rendered route, kept for backward compatibility after
    the JSON-API/React migration — just calls _handle_chat_message and
    redirects back to the (now largely unused) old /studio HTML page. The
    real frontend calls POST /api/workflows/<id>/message instead (see the
    API routes section below)."""
    text = (request.form.get("text") or "").strip()
    wf = _current_workflow()
    if text:
        _handle_chat_message(wf, text)
    return redirect(url_for("studio"))



# Bumped whenever this file changes in a way worth confirming actually
# loaded — printed at startup below. Added 2026-08-25 after a live mix-up
# where a running process kept showing pre-app-picker behavior (a brand
# new workflow already connected to Gmail, no "which app?" question) even
# though the file on disk was confirmed byte-identical to the fix — the
# runtime state didn't match the file, and there was no way to tell that
# from the terminal alone. If the line below doesn't say
# "conversational-app-picker" (or a later build tag) after a restart, this
# process is not running the file you think it is — check `pwd` and
# `ls -la studio.py` in the terminal you launched it from.
# =============================================================================
# --- JSON API (React frontend), added 2026-09-07 -------------------------
# =============================================================================
# Everything below is new surface for the React SPA migration — every route
# above this point is either untouched (gmail_bp, /composer*, /customers*,
# which stay exactly as they were: separate, still-server-rendered pages,
# deliberately NOT ported to React in this pass) or was a pre-existing
# route repointed to serve the SPA shell / answer JSON instead of building
# an HTML string (see each one's own updated docstring above: login,
# studio, integrations, rag_sync, the oauth_gmail_* routes).
#
# SCOPE, stated plainly: this covers auth, the workflow list + chat/compose
# flow (all 6 real connectors + custom), the full 17-type render_view JSON
# passthrough (the frontend implements renderer.py's component-type switch
# in React instead), the Jira detail/resolve action, and the Integrations
# page + Gmail OAuth + RAG sync. It does NOT cover the Composer
# (/composer*) or Customer-360 (/customers*) — those stay on their old
# server-rendered pages, reachable by direct link, outside the new React
# shell, for now.
#
# GMAIL is the one connector whose live preview was NEVER render_view JSON
# to begin with (see _preview_html's gmail branch above, and gmail_site.py's
# own module docstring on why: render_view's schema has no real message-ID
# field, so the model is never trusted to pick which real email a delete/
# star action targets — the embedded panel is a fully separate, real,
# deterministic subsystem with its own dozen-plus routes for star/delete/
# mark-read/reply/forward/compose/attachments). Rebuilding all of THAT as
# React components + a matching JSON API would be a comparably-sized
# second project on its own, so this pass takes the pragmatic option
# instead: the React Studio shell embeds it as an <iframe> pointing at
# /studio/gmail_panel_frame below, which reuses the exact same proven
# gmail_site.py rendering functions (render_inbox_panel/_tabs/_chips/
# _message) the old server-rendered /studio page used, just wrapped in its
# own tiny standalone HTML page instead of studio.py's full page shell.
# Every link/form inside that subsystem is driven by a `next_url`/`next`
# parameter (see gmail_site.py's own docstrings on render_inbox_panel and
# _safe_next) — pointing all of them back at this frame's own URL is what
# makes star/delete/reply/compose/mark-read all keep working correctly
# *inside the iframe* with zero changes to gmail_site.py itself.


def _serve_spa():
    """Returns the built React app's index.html — the SPA takes over
    client-side routing for /, /login, /studio, /integrations from there.
    If frontend/dist doesn't exist yet (frontend not built), returns a
    plain instruction page instead of a raw 404/500, since this is the
    single most likely "why is my browser showing nothing" moment during
    setup."""
    index_path = FRONTEND_DIST / "index.html"
    if not index_path.exists():
        return (
            "<h1>Pilant Studio frontend not built yet</h1>"
            "<p>Run <code>cd frontend &amp;&amp; npm install &amp;&amp; npm run build</code>, "
            "then reload this page.</p>",
            200,
        )
    return send_from_directory(FRONTEND_DIST, "index.html")


@app.route("/assets/<path:filename>")
def spa_assets(filename):
    """Vite's build output puts every hashed JS/CSS bundle under dist/assets/
    — served directly, no auth needed (same as any other static asset;
    nothing sensitive lives in a compiled frontend bundle)."""
    return send_from_directory(FRONTEND_DIST / "assets", filename)


def _api_login_required(view):
    """Same session check as login_required above, but answers 401 JSON
    instead of a 302 redirect to /login — a fetch() call following a
    redirect would just get the SPA's index.html back with a 200, which
    the frontend can't distinguish from a real API response, so an API
    route needs to fail loudly instead."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("username"):
            return jsonify({"error": "not_authenticated"}), 401
        return view(*args, **kwargs)
    return wrapped


def _user_json(user):
    return {"username": user["username"], "name": user.get("name", user["username"]), "role": user.get("role")}


@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    user = verify_login(username, password)
    if user is None:
        return jsonify({"error": "Incorrect username or password."}), 401
    session.clear()
    session["username"] = user["username"]
    return jsonify({"user": _user_json(user)})


@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/me")
def api_me():
    username = session.get("username")
    if not username:
        return jsonify({"error": "not_authenticated"}), 401
    return jsonify({"user": _user_json(get_user(username))})


@app.route("/api/connectors")
@_api_login_required
def api_connectors():
    """The registry the "+ New workflow" picker and the Integrations page
    both need — every CONNECTORS entry's display-relevant fields, with the
    callables (run_agent, etc.) left out since those aren't JSON-safe and
    the frontend has no use for them."""
    items = []
    for key, cfg in CONNECTORS.items():
        items.append({
            "key": key,
            "label": cfg["label"],
            "icon": cfg.get("icon", "🔌"),
            "category": cfg.get("category"),
            "description": cfg.get("description", ""),
            "has_oauth": bool(cfg.get("oauth")),
            "freeform": bool(cfg.get("freeform")),
        })
    return jsonify({"connectors": items, "default": DEFAULT_CONNECTOR})


def _workflow_json(wf):
    cfg = CONNECTORS.get(wf["connector"]) if wf["connector"] else None
    out = {
        "id": wf["id"],
        "title": wf["title"],
        "connector": wf["connector"],
        "connector_label": cfg["label"] if cfg else None,
        "connector_icon": cfg.get("icon") if cfg else None,
        "messages": wf["messages"],
        "last_render": wf["last_render"],
        "last_request_text": wf["last_request_text"],
        "pending_clarify": bool(wf["agent_messages"]),
    }
    if wf["connector"] == "gmail":
        out["gmail"] = {
            "folder": wf.get("gmail_panel_folder", "inbox"),
            "query": wf.get("gmail_panel_query"),
            "search_text": wf.get("gmail_panel_search_text"),
        }
    return out


@app.route("/api/workflows")
@_api_login_required
def api_workflows():
    items = []
    for wf_id in WORKFLOW_ORDER:
        wf = WORKFLOWS.get(wf_id)
        if not wf:
            continue
        cfg = CONNECTORS.get(wf["connector"]) if wf["connector"] else None
        items.append({
            "id": wf["id"], "title": wf["title"], "connector": wf["connector"],
            "connector_label": cfg["label"] if cfg else None,
            "connector_icon": cfg.get("icon") if cfg else None,
        })
    return jsonify({"workflows": items, "current_workflow_id": session.get("current_workflow")})


@app.route("/api/workflows", methods=["POST"])
@_api_login_required
def api_create_workflow():
    data = request.get_json(silent=True) or {}
    connector_key = data.get("connector")
    connector = connector_key if connector_key in CONNECTORS else None
    wf = _create_workflow(connector=connector)
    session["current_workflow"] = wf["id"]
    return jsonify({"workflow": _workflow_json(wf)})


@app.route("/api/workflows/<workflow_id>")
@_api_login_required
def api_get_workflow(workflow_id):
    wf = WORKFLOWS.get(workflow_id)
    if not wf:
        return jsonify({"error": "not_found"}), 404
    return jsonify({"workflow": _workflow_json(wf)})


@app.route("/api/workflows/<workflow_id>/open", methods=["POST"])
@_api_login_required
def api_open_workflow(workflow_id):
    wf = WORKFLOWS.get(workflow_id)
    if not wf:
        return jsonify({"error": "not_found"}), 404
    session["current_workflow"] = workflow_id
    return jsonify({"workflow": _workflow_json(wf)})


@app.route("/api/workflows/<workflow_id>/message", methods=["POST"])
@_api_login_required
def api_workflow_message(workflow_id):
    wf = WORKFLOWS.get(workflow_id)
    if not wf:
        return jsonify({"error": "not_found"}), 404
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "empty_message"}), 400
    _handle_chat_message(wf, text)
    return jsonify({"workflow": _workflow_json(wf)})


def _jira_issue_json(issue):
    return {
        "key": issue["key"], "summary": issue["summary"], "status": issue["status"],
        "priority": issue["priority"], "assignee": issue["assignee"], "reporter": issue["reporter"],
        "sprint": issue["sprint"], "epic": issue["epic"], "story_points": issue["story_points"],
        "labels": issue["labels"], "components": issue["components"], "fix_version": issue["fix_version"],
        "due": issue["due"], "created": issue["created"], "updated": issue["updated"],
        "watchers": issue["watchers"], "comments": issue["comments"], "type": issue["type"],
        "project": issue["project"],
        "resolved": issue["status"] == "Done",
        "tone": "good" if issue["status"] == "Done" else ("critical" if issue["priority"] in ("High", "Highest") else "default"),
    }


@app.route("/api/jira/<key>")
@_api_login_required
def api_jira_detail(key):
    issue = connectors_jira.get_issue(key)
    if not issue:
        return jsonify({"error": "not_found"}), 404
    return jsonify({"issue": _jira_issue_json(issue)})


@app.route("/api/jira/<key>/resolve", methods=["POST"])
@_api_login_required
def api_jira_resolve(key):
    issue = connectors_jira.get_issue(key)
    if not issue:
        return jsonify({"error": "not_found"}), 404
    connectors_jira.set_issue_status(key, "Done")
    issue = connectors_jira.get_issue(key)
    return jsonify({"issue": _jira_issue_json(issue)})


@app.route("/api/integrations")
@_api_login_required
def api_integrations():
    items = []
    for key, cfg in CONNECTORS.items():
        if cfg.get("freeform"):
            continue
        item = {
            "key": key, "label": cfg["label"], "icon": cfg.get("icon", "🔌"),
            "category": cfg.get("category", "search"), "description": cfg.get("description", ""),
            "has_oauth": bool(cfg.get("oauth")),
        }
        if cfg.get("oauth"):
            connected_email = connectors_gmail.get_connected_account()
            item["installed"] = bool(connected_email)
            item["connected_as"] = connected_email
        else:
            item["installed"] = True
            item["connected_as"] = None
        items.append(item)

    # Knowledge base (RAG) — a synthetic card, not a CONNECTORS entry (see
    # _knowledge_base_card_data's docstring above for why it's kept separate).
    if rag_index.is_configured():
        stats = rag_index.stats()
        items.append({
            "key": "knowledge_base", "label": "Knowledge base", "icon": "🔍",
            "category": "search",
            "description": "Search across Gmail, Slack, and GitHub history once synced.",
            "has_oauth": False, "installed": True, "connected_as": None,
            "rag_configured": True, "rag_stats": stats,
        })
    else:
        items.append({
            "key": "knowledge_base", "label": "Knowledge base", "icon": "🔍",
            "category": "search",
            "description": "Search across Gmail, Slack, and GitHub history once synced.",
            "has_oauth": False, "installed": False, "connected_as": None,
            "rag_configured": False, "rag_stats": None,
        })

    counts = Counter(it["category"] for it in items)
    categories = [
        # CATEGORY_META's icons are HTML-entity strings (e.g. "&#127760;")
        # meant for the old HTML renderer — unescaped here so the JSON API
        # hands React a plain unicode character it can render as text,
        # never needing dangerouslySetInnerHTML for something this simple.
        {"key": key, "label": meta["label"], "icon": _html.unescape(meta["icon"]), "count": counts.get(key, 0)}
        for key, meta in CATEGORY_META.items()
    ]
    return jsonify({"categories": categories, "items": items})


@app.route("/api/views")
@_api_login_required
def api_list_views():
    """Backs the new Workspace screen's "Save view" button (see the React
    frontend's WorkspacePage) — reuses saved_views.py exactly as the old
    Composer already did (see agent_composer.py's own docstring on the
    same "store what to re-run, not a snapshot" philosophy), just exposed
    as JSON now instead of server-rendered HTML. Scoped to the logged-in
    user, same ownership enforcement list_views() always had."""
    username = session["username"]
    return jsonify({"views": list_views(username)})


@app.route("/api/views", methods=["POST"])
@_api_login_required
def api_save_view():
    username = session["username"]
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    query = (data.get("query") or "").strip()
    if not query:
        return jsonify({"error": "nothing_to_save"}), 400
    view = save_view(username, name, query)
    return jsonify({"view": view})


def _gmail_panel_body_html(wf, panel_folder, panel_message, frame_base):
    """Same fragment-building logic as _preview_html's gmail branch above,
    parameterized so it can target the iframe frame route's own URL
    (frame_base) instead of hardcoding "/studio" — see this section's
    module-level comment on why the Gmail panel is embedded via iframe
    rather than ported to React component-by-component."""
    if panel_message:
        return (
            '<div class="gmail-app"><div class="gmail-main" style="flex:1;">'
            f'{render_inbox_panel_message(panel_message, next_url=frame_base)}'
            '</div></div>'
        )
    panel_query = wf.get("gmail_panel_query")
    panel_rows = render_inbox_panel(next_url=frame_base, folder=panel_folder, query=panel_query)
    tabs_html = render_inbox_panel_tabs(panel_folder, next_url=frame_base)
    compose_link = f'<a class="gmail-compose" href="/compose?next={_esc(frame_base)}">&#9998; Compose</a>'
    folder_param = "all" if panel_folder is None else panel_folder
    search_box_value = wf.get("gmail_panel_search_text") or panel_query or ""
    search_html = (
        '<form class="gmail-panel-search" method="post" action="/studio/gmail_panel_frame/search">'
        f'<input type="hidden" name="workflow_id" value="{_esc(wf["id"])}">'
        f'<input type="hidden" name="panel_folder" value="{_esc(folder_param)}">'
        f'<input type="text" name="panel_q" value="{_esc(search_box_value)}" '
        'placeholder="Search this folder — real syntax (from:, is:unread...) or plain English" '
        'autocomplete="off">'
        '<button type="submit">Search</button>'
        '</form>'
    )
    chips_html = render_inbox_panel_chips(panel_query, next_url=frame_base)
    query_hint_html = (
        f'<p class="gmail-panel-query-hint">Filtered to: <strong>{_esc(panel_query)}</strong> '
        f'<a href="{_esc(frame_base)}&panel_folder={_esc(folder_param)}">&times; clear</a></p>'
    ) if panel_query else ""
    return (
        '<div class="gmail-app"><div class="gmail-main" style="flex:1;">'
        f'{compose_link}'
        f'{search_html}'
        f'{tabs_html}'
        f'{chips_html}'
        f'{query_hint_html}'
        f'<div class="gmail-list">{panel_rows}</div>'
        '</div></div>'
    )


@app.route("/studio/gmail_panel_frame")
@login_required
def gmail_panel_frame():
    """The iframe target the React Studio shell embeds for a Gmail
    workflow's live preview — a small standalone HTML page (its own
    <html>, not the SPA shell) reusing gmail_site.py's real, deterministic
    inbox-rendering functions. `workflow_id` identifies which workflow's
    gmail_panel_* state to read/mutate (falls back to the session's current
    workflow if missing/stale, same as the old /studio route did
    implicitly via _current_workflow())."""
    wf = WORKFLOWS.get(request.args.get("workflow_id")) or _current_workflow()
    frame_base = f"/studio/gmail_panel_frame?workflow_id={wf['id']}"

    raw_panel_folder = request.args.get("panel_folder")
    if raw_panel_folder is not None:
        panel_folder = resolve_panel_folder(raw_panel_folder)
        wf["gmail_panel_folder"] = panel_folder
        wf["gmail_panel_query"] = None
        wf["gmail_panel_search_text"] = None
    else:
        panel_folder = wf.get("gmail_panel_folder", "inbox")

    raw_panel_chip = request.args.get("panel_chip")
    if raw_panel_chip is not None:
        chip_term = resolve_panel_chip(raw_panel_chip)
        if chip_term:
            current_query = (wf.get("gmail_panel_query") or "").strip()
            if current_query == chip_term:
                wf["gmail_panel_query"] = None
                wf["gmail_panel_search_text"] = None
            else:
                wf["gmail_panel_query"] = chip_term
                wf["gmail_panel_search_text"] = chip_term

    panel_message = (request.args.get("panel_message") or "").strip() or None
    body = _gmail_panel_body_html(wf, panel_folder, panel_message, frame_base)
    html = (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<style>{SHARED_CSS}\nbody{{margin:0;background:#fff;}}</style>"
        f"</head><body>{body}</body></html>"
    )
    return html


@app.route("/studio/gmail_panel_frame/search", methods=["POST"])
@login_required
def gmail_panel_frame_search():
    wf = WORKFLOWS.get(request.form.get("workflow_id")) or _current_workflow()
    raw_folder = request.form.get("panel_folder")
    panel_folder = resolve_panel_folder(raw_folder) if raw_folder is not None else wf.get("gmail_panel_folder", "inbox")
    raw_q = request.form.get("panel_q", "")
    wf["gmail_panel_folder"] = panel_folder
    wf["gmail_panel_search_text"] = raw_q.strip() or None
    wf["gmail_panel_query"] = resolve_panel_query(raw_q)
    return redirect(f"/studio/gmail_panel_frame?workflow_id={wf['id']}")


BUILD = "Cross-request conversation memory (2026-08-25)"

if __name__ == "__main__":
    print(f"Pilant Studio [build: {BUILD}] running at http://127.0.0.1:{PORT}", file=sys.stderr)
    print(f"  IMPORTANT: always open it as http://127.0.0.1:{PORT} in the browser, not", file=sys.stderr)
    print(f"  http://localhost:{PORT} — Gmail OAuth's redirect URI is fixed to 127.0.0.1", file=sys.stderr)
    print(f"  and won't work (or will look logged-out) from a different hostname.", file=sys.stderr)
    print(f"  loaded from: {__file__}", file=sys.stderr)
    print(f"  connectors:  {', '.join(CONNECTORS.keys())}", file=sys.stderr)
    print(f"  Gmail OAuth redirect_uri (register this EXACTLY in Google Cloud Console", file=sys.stderr)
    print(f"  -> APIs & Services -> Credentials -> your OAuth Client -> Authorized redirect", file=sys.stderr)
    print(f"  URIs, if you haven't already): {GMAIL_OAUTH_REDIRECT_URI}", file=sys.stderr)
    app.run(host="127.0.0.1", port=PORT, debug=False)
