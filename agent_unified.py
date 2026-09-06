"""
agent_unified.py — the "mixed apps" connector, added 2026-08-31 at the
user's explicit request after walking through the two-tier design in chat:
"we need two connectors, one gives interface of separate apps, and another
connector whatever you connected it gives mixed apps interface according to
the query." The connectors (agent_gmail.py, agent_slack.py, agent_github.py,
agent_helpdesk.py, agent_jira.py, agent_custom.py) are the first tier — each
one scoped to exactly one app, unchanged by this file. This is the second
tier: ONE connector with access to every real fetch tool across Gmail,
Slack, GitHub, the Helpdesk, and Jira at once, deciding per-request which of
them are actually relevant and merging their real results into a single
screen — "what's unread in my inbox" only touches Gmail; "what needs my
attention" or "show me high priority" touches whichever of them are
connected and actually relevant (e.g. Gmail + Jira, if that's what's
connected — see below).

NOT CONNECTED HANDLING, the user's second explicit requirement ("if user
ask for github it should say not connected, ask them to connect"): unlike
the single-app connectors (which just fail with a raw config error if their
one app isn't set up), this one computes real connection status for every
app at the START of every call — see _connection_status() below — and folds
it into the system prompt as ground truth, so the model knows before even
trying whether an app is usable. _make_dispatch() below is also a
CODE-ENFORCED backstop: even if the model ignores the prompt and tries to
call a not-connected app's fetch tool anyway, dispatch refuses the call
with a clear nudge rather than letting a raw connectors_*.py RuntimeError
leak through. The actual "ask them to connect" UX text still comes from the
model's own reply/render — this file only guarantees it always has accurate
information to say it correctly, not a canned string.

CONNECTION STATUS, the actual checks (kept intentionally simple, mirroring
what already exists elsewhere rather than inventing a new mechanism):
  - Gmail:    connected if get_connected_account() is set (a real OAuth
              sign-in — see connectors_gmail.py) OR GMAIL_REFRESH_TOKEN is
              present in .env (the static shared-inbox fallback). Same two
              paths studio.py's own Integrations card already checks.
  - Slack:    connected if SLACK_BOT_TOKEN and SLACK_CHANNEL are both set.
  - GitHub:   connected if GITHUB_REPO is set (GITHUB_TOKEN is only
              required for private repos — connectors_github.py handles
              that distinction itself).
  - Helpdesk: always connected — connectors_helpdesk.py is fully static/
              dummy data (see its own docstring), nothing to configure.
  - Jira:     always connected — connectors_jira.py is likewise fully
              static/dummy data (see its own docstring; added 2026-08-31),
              nothing to configure. Same reasoning as Helpdesk above: there
              is no real account behind it yet, so there is nothing that
              could ever be "not connected."
This is a fresh, real check every call (not cached), same reasoning as
every other piece of "current state" in this codebase — connecting/
disconnecting Gmail mid-session is a real, supported action.

MERGED RENDERING: when more than one app is relevant, the instructions
below ask for one 'list' component PER app (title = the app's name), rather
than interleaving rows from different apps into one list — this avoids
inventing a cross-app sort/ranking scheme the schema doesn't describe, and
keeps each source's real data visibly attributed to where it came from.

SCOPE NOT COVERED YET, stated honestly rather than silently skipped:
GitHub's per-user label-based identity scoping (scope_issues(), used by
agent_github.py via CONNECTORS' needs_user flag) is NOT applied here — same
reasoning agent_helpdesk.py's own docstring already gives for itself (any
logged-in person sees the same GitHub issues through this connector). If
that scoping ever needs to hold for the mixed view too, it should be added
here explicitly, not assumed.

FIRST_TURN, added 2026-08-31 — REVERSING this file's own earlier design
choice: this connector originally shipped deliberately WITHOUT the
DynamisOS-style bundled first-turn question the four single-app connectors
all have (see FIRST_TURN_CLARIFY_PARAGRAPH in agent_gmail.py etc.), on the
reasoning that asking "what do you want to see?" up front would fight the
point of a connector whose whole job is inferring relevant apps from the
request itself. That reasoning held as long as 'unified' was only ever
reached via an already-specific query ("what needs my attention"). It
stopped holding once studio.py started routing a BARE "connect gmail and
jira" (naming 2+ apps, no real request yet) straight into this connector's
very first call — tested live, and the person reasonably expected the same
"ask once before building" moment every single-app connector already gives
on ITS first message, not a screen built from a guess. So this connector
now supports first_turn the same way the others do (see
FIRST_TURN_CLARIFY_PARAGRAPH below and CONNECTORS['unified']['first_turn']
in studio.py) — with one difference from theirs: the bundled question never
asks which APP to use (this connector's own job is still figuring that out
from the request + connection status, never the person), only purpose and
layout/style, same as before.

run_agent()'s signature and return contract are otherwise UNCHANGED from
every other connector (see agent_gmail.py's docstring for the full
contract) — studio.py dispatches to this one exactly the same generic way
it dispatches to any other CONNECTORS entry.
"""

import json
import os
import re
import sys

from schema import UI_SCHEMA
from connectors_gmail import get_gmail_messages, get_connected_account
from connectors_slack import get_slack_messages
from connectors_github import get_github_issues
from connectors_helpdesk import get_tickets
from connectors_jira import get_issues as get_jira_issues
from validation import validate_view
from guardrails import find_fabricated_content, find_fabricated_stats, fabricated_content_nudge
from skills import load_skill
import claude_engine as ce

APPS = {
    "gmail": {"label": "Gmail", "tool": "get_gmail_messages"},
    "slack": {"label": "Slack", "tool": "get_slack_messages"},
    "github": {"label": "GitHub", "tool": "get_github_issues"},
    "helpdesk": {"label": "Helpdesk", "tool": "get_tickets"},
    "jira": {"label": "Jira", "tool": "get_jira_issues"},
}


def _connection_status():
    """Real, fresh-every-call connection status for every app — see this
    module's docstring for what each check actually means. Returns
    {app_key: bool}."""
    gmail_ok = bool(get_connected_account()) or bool(os.environ.get("GMAIL_REFRESH_TOKEN", "").strip())
    slack_ok = bool(os.environ.get("SLACK_BOT_TOKEN", "").strip()) and bool(os.environ.get("SLACK_CHANNEL", "").strip())
    github_ok = bool(os.environ.get("GITHUB_REPO", "").strip())
    return {"gmail": gmail_ok, "slack": slack_ok, "github": github_ok, "helpdesk": True, "jira": True}


TOOLS = [
    {
        "name": "get_gmail_messages",
        "description": (
            "Fetch recent real messages from the connected Gmail inbox, most recent first. "
            "Only call this if Gmail shows as connected above. `query` is real Gmail search "
            "syntax (from:, subject:, newer_than:Nd, has:attachment, ...). `unread_only`, if "
            "true, restricts to genuinely unread messages. `limit` caps how many come back "
            "(default 10, max 50)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "unread_only": {"type": "boolean"},
                "limit": {"type": "integer"},
                "folder": {"type": "string", "enum": ["inbox", "sent", "spam", "drafts", "trash", "starred", "important"]},
            },
        },
    },
    {
        "name": "get_slack_messages",
        "description": (
            "Fetch recent real messages from the connected Slack channel, most recent first. "
            "Only call this if Slack shows as connected above. `contains`, if given, is a "
            "simple case-insensitive substring filter. `limit` caps how many come back "
            "(default 20, max 100)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "contains": {"type": "string"},
                "limit": {"type": "integer"},
            },
        },
    },
    {
        "name": "get_github_issues",
        "description": (
            "Fetch real issues from the connected GitHub repo. Only call this if GitHub shows "
            "as connected above. `state` is open/closed/all (default open). `label` optionally "
            "filters to one real label."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "state": {"type": "string", "enum": ["open", "closed", "all"]},
                "label": {"type": "string"},
            },
        },
    },
    {
        "name": "get_tickets",
        "description": (
            "Fetch support tickets from the connected Helpdesk (always connected — this is a "
            "demo data source with no setup required). Optionally filtered by status (open/"
            "in_progress/resolved/escalated), priority (low/medium/high/critical), and/or "
            "category (account/billing/bug/feature_request)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["open", "in_progress", "resolved", "escalated"]},
                "priority": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
                "category": {"type": "string", "enum": ["account", "billing", "bug", "feature_request"]},
            },
        },
    },
    {
        "name": "get_jira_issues",
        "description": (
            "Fetch Jira-style issues from the connected Jira (always connected — this is a "
            "demo data source with no setup required, covering two projects: ENG and DES). "
            "Optionally filtered by project (ENG/DES), status (To Do/In Progress/In Review/"
            "Blocked/Done — pass an ARRAY of these when the request implies more than one, "
            "e.g. [\"To Do\", \"In Progress\", \"Blocked\"] for 'not done yet'/'still open', in "
            "ONE call — never a single combined or JSON-looking string), priority (Lowest/Low/"
            "Medium/High/Highest — pass an ARRAY of these when the request implies more than "
            "one level, e.g. [\"High\", \"Highest\"] for a broad 'urgent'/'high priority' "
            "request, in ONE call rather than picking just one or fetching twice), issue_type "
            "(Story/Bug/Task/Epic/Sub-task), sprint (e.g. \"Sprint 24\", or \"backlog\"), "
            "assignee (name substring, or \"unassigned\"), and/or label. Each issue carries "
            "many fields (project, type, status, priority, assignee, reporter, sprint, epic, "
            "story points, labels, components, fix version, due date, watchers, comments) — "
            "when rendering, show only the 2-4 that answer the actual request, not all of them."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "enum": ["ENG", "DES"]},
                "status": {
                    "anyOf": [
                        {"type": "string", "enum": ["To Do", "In Progress", "In Review", "Blocked", "Done"]},
                        {
                            "type": "array",
                            "description": "Use this when the request implies more than one status, e.g. [\"To Do\", \"In Progress\", \"Blocked\"] for 'not done yet'/'still open'.",
                            "items": {"type": "string", "enum": ["To Do", "In Progress", "In Review", "Blocked", "Done"]},
                        },
                    ]
                },
                "priority": {
                    "anyOf": [
                        {"type": "string", "enum": ["Lowest", "Low", "Medium", "High", "Highest"]},
                        {
                            "type": "array",
                            "description": "Use this when the request implies more than one priority level, e.g. [\"High\", \"Highest\"] for 'urgent'/'high priority'.",
                            "items": {"type": "string", "enum": ["Lowest", "Low", "Medium", "High", "Highest"]},
                        },
                    ]
                },
                "issue_type": {"type": "string", "enum": ["Story", "Bug", "Task", "Epic", "Sub-task"]},
                "sprint": {"type": "string"},
                "assignee": {"type": "string"},
                "label": {"type": "string"},
            },
        },
    },
    {
        "name": "ask_user",
        "description": (
            "Ask the person a single clarifying question before doing anything else. Use this "
            "ONLY when the request is genuinely ambiguous in a way that would change which "
            "apps you'd fetch from or what you'd fetch — e.g. 'what needs my attention' could "
            "mean urgent email, unread Slack, escalated tickets, or all of them. The question "
            "must NAME the specific ambiguity and offer concrete options. Do NOT use this for "
            "requests you can reasonably interpret yourself, and do not ask which apps to use "
            "just because more than one is connected — infer that from the request itself. Ask "
            "AT MOST ONE question per request."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "One short, specific question."}
            },
            "required": ["question"],
        },
    },
    {
        "name": "render_view",
        "description": (
            "Emit the final generated screen for the person's request, built from whichever "
            "connected apps' real data you fetched. Call this exactly once, as the last step. "
            "Only call this if at least one relevant, connected app was actually fetched — if "
            "nothing relevant is connected, reply in plain text instead (do not call this)."
        ),
        "input_schema": UI_SCHEMA,
    },
]


def _status_block(status):
    lines = []
    for key, meta in APPS.items():
        lines.append(f"  - {meta['label']}: {'CONNECTED' if status[key] else 'NOT CONNECTED'}")
    return "\n".join(lines)


FIRST_TURN_CLARIFY_PARAGRAPH = (
    "\n\nOne more thing, for right now only: this is the very first request in a brand-new "
    "conversation, so before calling any fetch tool or render_view, call ask_user ONE time "
    "with a single bundled question covering both (a) what they actually want to see — if "
    "the request is broad or is just naming apps to connect ('what needs my attention', "
    "'connect gmail and jira'), offer 2-3 concrete options grounded in whichever apps "
    "actually show CONNECTED above (e.g. 'unread email, high-priority Jira issues, or "
    "both?'); skip this half if they were already specific — and (b) how they'd like it "
    "laid out (a compact list, a stat summary up top, or something more detailed). Do NOT "
    "ask which apps to use as part of this question — deciding that from the request and "
    "connection status is still your own job, never the person's, even on this first turn. "
    "Ask both (a) and (b) in ONE message, never two separate questions. This replaces the "
    "ambiguity-only ask_user rule above for this one first request; every later request in "
    "this same conversation goes back to that normal rule — never ask this bundled question "
    "again."
)

FIRST_TURN_GATE_NUDGE = (
    "Not yet: this is the very first request of a brand-new conversation, so call ask_user "
    "first with one bundled question covering purpose and layout/style before fetching or "
    "rendering anything — see the instructions for exactly what to cover."
)


def _build_system(status, first_turn=False, extra_system=""):
    base = (
        "You are Pilant's Composition Engine, embedded as a unified AI Copilot with access to "
        "whichever of the person's real connected apps are relevant to their request — Gmail, "
        "Slack, GitHub, the internal Helpdesk, and Jira. Some requests only need one of these "
        "('what's unread in my inbox' -> Gmail only); some need several ('what do I need to "
        "catch up on today', or 'what's high priority' -> whichever are connected and relevant, "
        "e.g. Gmail + Jira if that's what a 'high priority' request actually touches). Decide "
        "which apps a request actually needs from its content, not from which ones happen to be "
        "connected. Note that each app has its own vocabulary for urgency — Gmail has no formal "
        "priority field (use 'important'/starred/unread as signals instead), Helpdesk uses low/"
        "medium/high/critical, Jira uses Lowest/Low/Medium/High/Highest — map each app's own "
        "real values to sensible badge tones rather than assuming they share one scale. When a "
        "request implies more than one Jira priority level at once ('urgent'/'high priority' "
        "means BOTH High and Highest), pass get_jira_issues's priority argument as an array "
        "covering all of them, e.g. [\"High\", \"Highest\"], in that one call — remember each "
        "app's fetch tool can only be called once per request, so there is no second chance to "
        "pick up a level left out of the first call.\n\n"
        "Current connection status, real and accurate right now:\n"
        f"{_status_block(status)}\n\n"
        "Only call a fetch tool for an app that shows CONNECTED above — never call one for a "
        "NOT CONNECTED app, it will fail. If the request needs an app that is NOT CONNECTED:\n"
        "  - If NONE of the apps the request actually needs are connected: do not call "
        "render_view. Reply in plain text, clearly naming which app(s) aren't connected, and "
        "let them know they can connect it from the Integrations page.\n"
        "  - If AT LEAST ONE relevant app IS connected: go ahead and fetch/render using just "
        "the connected one(s), and set the screen's 'meta' field to a short honest note about "
        "the gap, e.g. 'GitHub isn't connected yet — showing Gmail and Slack only.'\n\n"
        "Do not ask the same connectivity question twice. If any earlier message in this "
        "conversation already told the person an app isn't connected, that gap has been "
        "communicated — do not ask again whether to 'proceed with just the connected ones' or "
        "'wait until X is connected,' even if their reply still mentions the unavailable app "
        "(people often restate their whole original request rather than answering narrowly, "
        "and that is not new ambiguity). Once the gap has been stated once, always just proceed: "
        "fetch from whatever relevant apps ARE connected and call render_view, noting the gap in "
        "'meta' as above. Only ask a further question if their reply raises a genuinely NEW, "
        "unrelated ambiguity that has nothing to do with an app being connected or not.\n\n"
        "Call each relevant, connected app's fetch tool AT MOST ONCE per request — never call "
        "the same one twice. When more than one app is relevant, call each of them once, then "
        "call render_view exactly once with ALL of it merged into a single screen: use ONE "
        "'list' component PER app (set that component's 'title' to the app's name, e.g. "
        "'Gmail', 'Slack'), never interleave rows from different apps into one list. Use "
        "judgment on badge tones: unread/urgent/escalated/critical -> 'warning' or 'critical'; "
        "resolved/closed/low priority -> 'good'; otherwise 'default'. Include only what answers "
        "the request — no unrelated messages, no default dashboard.\n\n"
        "Whenever the merged screen covers 2 or more apps, lead with a 'stat_grid' component "
        "BEFORE the per-app 'list' components — one stat per app, its 'value' the real COUNT of "
        "items that app's fetch tool actually returned for this request (e.g. label 'Gmail', "
        "value the number of matching messages; label 'Jira', value the number of matching "
        "issues) so the person sees the headline numbers before scanning the lists themselves. "
        "Tone follows the same urgency judgment as the badges (a high count of critical/unread "
        "items -> 'critical' or 'warning', otherwise 'default'). Every stat's value must be a "
        "real count of real rows you're about to list — never an estimate, never a number from "
        "outside this request's own fetched data.\n\n"
        "Per-app component choice: a per-app section defaults to 'list' as described above, but "
        "doesn't have to be — the same guidance a single-app connector would use for choosing "
        "among the newer component types applies to any one app's own section here too:\n\n"
        + load_skill("ui_composition") + "\n\n" +
        "Critical: render_view's fields must contain real, literal data from the fetch tools "
        "you actually called this conversation — never invent a sender, message, issue, or "
        "ticket, and never write placeholder text like 'Subject' or 'Message 1' as a value. If "
        "you're not looking at a real string a tool returned, don't write it. A filtered query "
        "coming back with few results or none is normal — render that honestly.\n\n"
        "If the message isn't a data request at all (a greeting, thanks, small talk, a question "
        "about what you can do), don't call any tool — just reply normally in plain text.\n\n"
        + load_skill("render_dont_narrate") + "\n\n" +
        "Memory: if this conversation already includes a line like 'Built \"...\"' describing "
        "something you built earlier, that's a real screen you already showed this person — a "
        "short follow-up means adjust or narrow THAT, using it as context. Still re-fetch "
        "current data for anything you show again, since it can change between requests."
    )
    if first_turn:
        base = base + FIRST_TURN_CLARIFY_PARAGRAPH
    return base + "\n\n" + extra_system if extra_system else base


_PLACEHOLDER_VALUES = {
    "subject", "snippet", "from", "sender", "date", "recipient", "to", "title", "status",
    "summary", "assignee", "reporter", "priority", "sprint", "epic", "project", "label", "labels",
}
_PLACEHOLDER_PATTERN = re.compile(r"^(email|message|issue|ticket|eng|des)[\s-]*\d+$", re.IGNORECASE)


def _find_placeholder_labels(view):
    """Same connector-local safety net as agent_gmail.py's version, kept
    consistent across both since this connector's output can include the
    same kinds of rows Gmail's own does, mixed with Slack/GitHub/Helpdesk/
    Jira rows in the same screen."""
    bad = []
    for comp in (view.get("components") or []):
        if not isinstance(comp, dict):
            continue
        for row in (comp.get("rows") or []):
            if not isinstance(row, dict):
                continue
            for key in ("name", "note"):
                val = row.get(key)
                if isinstance(val, str):
                    v = val.strip().lower()
                    if v in _PLACEHOLDER_VALUES or _PLACEHOLDER_PATTERN.match(v):
                        bad.append(val)
    seen = set()
    return [b for b in bad if not (b in seen or seen.add(b))]


ALREADY_ASKED_NUDGE = (
    "ask_user was rejected: you already asked one clarifying question in this conversation "
    "and the person just answered it. Use their answer now instead of asking again."
)


def _is_lazy_clarifying_question(question, user_request):
    def tokens(s):
        return set(re.findall(r"[a-z]+", (s or "").lower()))

    q_tokens = tokens(question)
    r_tokens = tokens(user_request)
    if not q_tokens or not r_tokens:
        return False
    overlap = len(q_tokens & r_tokens) / len(q_tokens)
    new_words = q_tokens - r_tokens
    return overlap >= 0.7 and len(new_words) <= 1


LAZY_CLARIFY_NUDGE = (
    "ask_user was rejected: the question just restates the person's original request instead "
    "of narrowing it. Either ask a real, narrowing question with actual options, or skip "
    "ask_user and use your best reasonable interpretation."
)

NO_DATA_YET_NUDGE = (
    "render_view was rejected: you haven't successfully fetched from any connected app yet "
    "this conversation, so you have no real data to show. Fetch first, then render_view."
)


def _make_dispatch(status, seed_fetched_data, verbose, first_turn=False):
    """first_turn: see agent_helpdesk.py's/agent_jira.py's copy of this
    parameter for the full reasoning — a code-enforced backstop for
    FIRST_TURN_CLARIFY_PARAGRAPH, added here 2026-08-31 (see this module's
    docstring's "FIRST_TURN" section for why this connector didn't have it
    until now)."""
    state = {"fetched_data": seed_fetched_data, "sources_fetched": set(), "tool_failures": {}}
    fetchers = {
        "get_gmail_messages": ("gmail", lambda args: get_gmail_messages(**args)),
        "get_slack_messages": ("slack", lambda args: get_slack_messages(**args)),
        "get_github_issues": ("github", lambda args: get_github_issues(**args)),
        "get_tickets": ("helpdesk", lambda args: get_tickets(**args)),
        "get_jira_issues": ("jira", lambda args: get_jira_issues(**args)),
    }

    def dispatch(name, args, tc_id, prior_messages, _seed_fetched_data_unused):
        if name in fetchers:
            if first_turn:
                return ce.ToolOutcome(tool_result=FIRST_TURN_GATE_NUDGE)
            app_key, fn = fetchers[name]
            label = APPS[app_key]["label"]
            if not status[app_key]:
                return ce.ToolOutcome(tool_result=(
                    f"{label} is not connected — do not call {name} again this conversation. "
                    f"If the request needs {label}, tell the person plainly that it isn't "
                    "connected and they can connect it from the Integrations page; still help "
                    "with whatever other connected apps are relevant."
                ))
            if app_key in state["sources_fetched"]:
                return ce.ToolOutcome(tool_result=(
                    f"You already fetched from {label} earlier in this conversation — use that "
                    "real result instead of calling it again."
                ))
            try:
                result = fn(args or {})
            except Exception as e:
                err_str = str(e)
                if verbose:
                    print(f"  [dispatch] {name} raised: {e}", file=sys.stderr)
                if state["tool_failures"].get(name) == err_str:
                    return ce.ToolOutcome(final={"error": err_str})
                state["tool_failures"][name] = err_str
                # Added 2026-09-06, fixing a real bug found live: this used to be a
                # bare {"error": err_str} with no guidance, unlike the "not connected"
                # tool_result above which explicitly says "still help with whatever
                # other connected apps are relevant." Without that instruction here,
                # the model was observed abandoning the whole request — including a
                # DIFFERENT app's real, already-fetched data — and answering in
                # ungrounded plain chat text instead (which no guardrail ever checks;
                # only render_view's dispatch branch runs find_fabricated_content/
                # find_fabricated_stats). This app passed its CONNECTED check but its
                # real call failed anyway (e.g. an expired/revoked OAuth token) — that
                # is a fact to report about THIS app, never a reason to stop covering
                # whichever other relevant apps are actually working.
                return ce.ToolOutcome(tool_result=json.dumps({
                    "error": err_str,
                    "instruction": (
                        f"{label}'s real fetch call failed just now (see 'error' above) — "
                        f"likely an expired/revoked token or a real upstream problem, not "
                        f"the same thing as 'not connected'. Do not call {name} again this "
                        "conversation. Tell the person plainly what failed and that they "
                        "may need to reconnect it from the Integrations page. If the "
                        "request also needs other apps that ARE connected and relevant, "
                        "still fetch from those and call render_view with their real "
                        "data — never skip render_view and describe a finding in plain "
                        "chat text instead, even a 'nothing urgent' finding. Note this "
                        "app's failure in the screen's 'meta' field, exactly as you would "
                        "for an app that was never connected at all."
                    ),
                }))
            state["sources_fetched"].add(app_key)
            state["fetched_data"] = True
            if verbose:
                count = len(result) if isinstance(result, list) else "?"
                print(f"  [dispatch] {name}({args}) — {count} item(s)", file=sys.stderr)
            return ce.ToolOutcome(tool_result=json.dumps(result))

        if name == "ask_user":
            if ce.ask_user_already_used(prior_messages):
                return ce.ToolOutcome(tool_result=ALREADY_ASKED_NUDGE)
            question = (args.get("question") or "").strip() or "Could you clarify what you're looking for?"
            original_request = ce.original_user_request(prior_messages)
            if _is_lazy_clarifying_question(question, original_request):
                return ce.ToolOutcome(tool_result=LAZY_CLARIFY_NUDGE)
            return ce.ToolOutcome(
                final={"clarify": question, "messages": ce.NEEDS_CONVERSATION, "fetched_data": state["fetched_data"]},
                tool_result=ce.ASK_USER_ACCEPTED_SENTINEL,
            )

        if name == "render_view":
            if first_turn:
                return ce.ToolOutcome(tool_result=FIRST_TURN_GATE_NUDGE)
            if not state["fetched_data"]:
                return ce.ToolOutcome(tool_result=NO_DATA_YET_NUDGE)
            problem = validate_view(args, UI_SCHEMA)
            if problem is not None:
                return ce.ToolOutcome(tool_result=problem)
            fabricated = (
                find_fabricated_content(args, prior_messages)
                + find_fabricated_stats(args, prior_messages)
                + _find_placeholder_labels(args)
            )
            if fabricated:
                if verbose:
                    print(f"  [dispatch] render_view REJECTED: {fabricated}", file=sys.stderr)
                return ce.ToolOutcome(tool_result=fabricated_content_nudge(fabricated))
            if verbose:
                print(f"  [dispatch] render_view accepted — heading={args.get('heading')!r}", file=sys.stderr)
            return ce.ToolOutcome(final={"render": args})

        return ce.ToolOutcome(tool_result="unknown tool")

    return dispatch


def _on_no_tool_call(text, stop_reason):
    """A plain-text reply is a normal, expected outcome here — both for real
    small talk AND for the 'nothing relevant is connected' case the system
    prompt asks the model to handle this way. See run_agent's
    force_tool_choice=False below."""
    if text:
        return ce.ToolOutcome(final={"text": text})
    return None


def system_snapshot():
    """A point-in-time system prompt, connection status baked in as of
    whenever this is called. studio.py uses this ONCE, at import time, as
    CONNECTORS['unified']['system'] — a static memory-seed anchor (see
    studio.py's _append_to_memory()), not the actual system prompt used at
    call time, which run_agent() below always rebuilds fresh via
    _build_system() so it reflects real, current connection status on every
    single request, not just whatever was true when the server started."""
    return _build_system(_connection_status())


def run_agent(user_request=None, max_steps=10, verbose=True, messages=None, fetched_data=False, first_turn=False, extra_system=""):
    """Same calling convention as every other connector (see agent_gmail.py's
    docstring for the full contract). `first_turn`, as of 2026-08-31, is
    ACTUALLY used (see this module's docstring's "FIRST_TURN" section for
    why that changed) — same DynamisOS-style bundled purpose+layout
    question the four single-app connectors ask on their own first turn,
    minus the "which app" half (this connector still decides that itself,
    from the request + connection status, never the person, even here).
    studio.py only ever passes first_turn=True on a workflow's genuine
    first request (see CONNECTORS['unified']['first_turn'] and
    _run_agent_for_workflow()'s own first_turn gating logic) — every later
    request in the same conversation gets first_turn=False as normal.

    max_steps defaults higher than the single-app connectors (10 vs 8) since
    a single request here can legitimately need several fetch calls (up to
    one per connected app) plus ask_user plus render_view."""
    status = _connection_status()
    dispatch = _make_dispatch(status, fetched_data, verbose, first_turn)
    return ce.run_claude_agent(
        dispatch,
        system=_build_system(status, first_turn, extra_system),
        tools=TOOLS,
        max_steps=max_steps,
        verbose=verbose,
        messages=messages,
        user_request=user_request,
        fetched_data=fetched_data,
        on_no_tool_call=_on_no_tool_call,
        force_tool_choice=False,
    )


if __name__ == "__main__":
    from pathlib import Path
    import webbrowser
    from renderer import render_html

    query = sys.argv[1] if len(sys.argv) > 1 else "what needs my attention today?"
    print(f"REQUEST: {query}\n", file=sys.stderr)
    print(f"connection status: {_connection_status()}\n", file=sys.stderr)

    result = run_agent(query)
    while "clarify" in result:
        print(f"\nCLARIFYING QUESTION: {result['clarify']}", file=sys.stderr)
        answer = input("your answer> ")
        resumed = result["messages"] + [{"role": "user", "content": answer}]
        result = run_agent(messages=resumed, fetched_data=result.get("fetched_data", False))

    print(json.dumps(result, indent=2, default=str))

    if "render" in result:
        out_path = Path(__file__).parent / "output.html"
        out_path.write_text(render_html(result["render"], query))
        print(f"\nrendered screen written to {out_path}", file=sys.stderr)
        webbrowser.open(out_path.as_uri())
