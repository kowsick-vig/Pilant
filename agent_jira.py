"""
Composition Engine wired to connectors_jira.py — the fully static/dummy
"crowded Jira" connector (see its docstring). Same static-connector
reasoning as agent_helpdesk.py: no identity scoping (any logged-in staff
member sees every issue across both projects) and no tool_failures
tracking (get_issues can't fail the way a real network-backed API call
can, so there's nothing for that guardrail to catch).

The whole point of this connector, per the user's own request ("fake Jira
with a crowded interface"), is the contrast: connectors_jira.py's ISSUES
carry a dozen-plus fields each (project, type, status, priority, assignee,
reporter, sprint, epic, story points, labels, components, fix version, due
date, watchers, comments) — that's deliberately the crowded, everything-
at-once density a real Jira board shows. This SYSTEM prompt's job is to
make sure the generated screen does the OPPOSITE: show only the fields
that matter for what was actually asked, never all of them by default.
That's the live, honest version of Pilant's "cuts through the clutter"
pitch — not asserted in a deck, demonstrated against real (if fictional)
crowded source data.

Built by copying agent_helpdesk.py's structure exactly (TOOLS shape,
_build_system/FIRST_TURN pattern, _find_placeholder_labels, dispatch
closure, run_agent calling convention) so studio.py's CONNECTORS registry
and _run_agent_for_workflow() dispatch to this one exactly the same
generic way they dispatch to every other connector — no changes needed
there beyond registering the entry itself.
"""

import json
import re
import sys

from schema import UI_SCHEMA
from connectors_jira import get_issues
from validation import validate_view
from guardrails import (
    find_fabricated_content,
    find_fabricated_stats,
    find_ungrounded_alert_claims,
    fabricated_content_nudge,
)
import claude_engine as ce

TOOLS = [
    {
        "name": "get_issues",
        "description": (
            "Fetch Jira-style issues, optionally filtered by project (ENG/DES), status "
            "(To Do/In Progress/In Review/Blocked/Done), priority (Lowest/Low/Medium/High/"
            "Highest — pass an ARRAY of these when the request means more than one, e.g. a "
            "broad 'urgent'/'high priority' request means BOTH High and Highest: pass "
            "[\"High\", \"Highest\"] in one call rather than picking just one), issue_type "
            "(Story/Bug/Task/Epic/Sub-task), sprint (e.g. \"Sprint 24\", or \"backlog\" for "
            "issues with no sprint), assignee (name substring, or \"unassigned\"), and/or "
            "label."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "enum": ["ENG", "DES"]},
                "status": {"type": "string", "enum": ["To Do", "In Progress", "In Review", "Blocked", "Done"]},
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
                "sprint": {"type": "string", "description": "e.g. \"Sprint 24\", or \"backlog\" for no sprint."},
                "assignee": {"type": "string", "description": "Name substring, or \"unassigned\"."},
                "label": {"type": "string"},
            },
        },
    },
    {
        "name": "ask_user",
        "description": (
            "Ask the person a single clarifying question before doing anything else. Use "
            "this ONLY when the request is genuinely ambiguous in a way that would change "
            "what you'd fetch — e.g. 'what needs attention' could mean blocked issues, "
            "highest-priority issues, issues due soon, or something else. The question must "
            "NAME the specific ambiguity and offer concrete options — it must add "
            "information the person doesn't already have, not just repeat their own words "
            "back as a question. Do NOT use this for requests you can reasonably interpret "
            "yourself. Ask AT MOST ONE question per request: after the person answers, "
            "proceed straight to fetching data and rendering."
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
            "Emit the final generated screen for the person's request. Call this exactly "
            "once, as the last step, after fetching whatever data you need. Include ONLY "
            "the fields and issues required to answer the request — never the full crowded "
            "field set (project/type/status/priority/assignee/reporter/sprint/epic/story "
            "points/labels/components/fix version/due date/watchers/comments) by default."
        ),
        "input_schema": UI_SCHEMA,
    },
]

SYSTEM = (
    "You are the Composition Engine inside Pilant, embedded in a software company's Jira-style "
    "issue tracker, covering two projects: ENG (engineering) and DES (design). A staff member "
    "has typed a request about issues. Most requests are clear enough to act on directly: call "
    "get_issues with whatever filters answer it, then call render_view exactly once with a "
    "screen that shows ONLY what they asked for.\n\n"
    "Priority filtering: when a request names or implies more than one priority level — "
    "'urgent'/'high priority' means BOTH High and Highest, for instance — pass the priority "
    "argument as an array covering all of them (e.g. [\"High\", \"Highest\"]) in that ONE "
    "get_issues call. Do not arbitrarily pick just one of the levels the person meant and "
    "do not call get_issues twice to cover them separately; the array form exists exactly "
    "so a single call can cover a broad priority request completely.\n\n"
    "The real Jira this is standing in for crams project, type, status, priority, assignee, "
    "reporter, sprint, epic, story points, labels, components, fix version, due date, watcher "
    "count, and comment count onto every single issue row, all at once — that density is "
    "exactly what makes a real Jira board exhausting to scan. Your job is the opposite: never "
    "dump every field onto the screen by default. Pick the 2-4 fields that actually answer THIS "
    "request (e.g. 'what's blocked' needs summary + assignee + how long it's been blocked, not "
    "also story points and watcher counts; 'what's Priya working on' needs summary + status + "
    "priority, not sprint/epic/labels/components too) and leave the rest out. Use judgment on "
    "badge tones: Blocked status and Highest priority → tone 'critical'; In Progress and High "
    "priority → tone 'warning'; Done status → tone 'good'; anything else → tone 'default'. Do "
    "not pad the screen with anything unrequested. You must call render_view before finishing — "
    "do not answer in plain text.\n\n"
    "Component choice for multiple issues, this matters: when the request returns more than one "
    "issue, put them ALL in a SINGLE 'list' component — one row per issue (row 'name' = the "
    "issue key + summary, e.g. 'ENG-482 — Checkout fails on Safari...', row 'note' = whichever 1-2 "
    "of the crowded fields above actually matter for this request, badge for status/priority if "
    "useful) — never one 'panel' component per issue. A panel-per-issue wastes an entire nested "
    "fields array on every single issue and will run you out of output budget before you finish "
    "the JSON, which is exactly the malformed-output failure you must avoid. Reserve 'panel' for "
    "when there is exactly ONE issue to show in detail (there, showing more of its fields is "
    "appropriate), or for a handful of aggregate stats — not for enumerating a list of issues. Do "
    "not add a second 'panel' component next to the list just to restate a count — the list "
    "itself already shows every issue; a redundant summary panel is exactly the kind of "
    "unrequested padding to avoid.\n\n"
    "Three more component types exist beyond stat_grid/panel/list/suggestions — reach for "
    "whichever actually fits the shape of the request, don't default to 'list' out of habit:\n"
    "- 'metric': when the real answer is exactly ONE number (e.g. 'how many issues are "
    "blocked right now?', 'how many are unassigned?') — a single big emphasized figure reads "
    "far better than a one-card stat_grid or a list of one row. Put exactly one entry in "
    "'stats', a real count of what get_issues actually returned.\n"
    "- 'timeline': for requests that are naturally about recency/order over a SET of issues — "
    "'what's changed recently', 'what's been updated this week', 'newest bugs first'. Sort the "
    "real issues by their real 'created' or 'updated' date and put them in 'rows' in that order "
    "(name = issue key + summary, note = the real date, e.g. 'Updated 2026-08-30'). This "
    "connector has no per-issue changelog/history — never invent a fake sequence of status "
    "changes for a single issue; timeline here means several real issues in real chronological "
    "order, nothing else.\n"
    "- 'chart': when a request is about how issues split up/compare across categories — 'break "
    "down open bugs by priority', 'how are ENG issues distributed by status'. Put one 'stats' "
    "entry per category (label = the category, e.g. 'Highest'/'High'/'Medium'; value = the real "
    "count of issues in it as a plain number string, e.g. '4') — never a stat_grid for this "
    "shape of request, a chart's bars make the comparison visible in a way separate cards don't.\n"
    "- 'alert': when the real answer IS a single urgent condition worth calling out on its own "
    "— 'is anything blocked and overdue', 'anything I should know about right now'. Set 'title' "
    "to the real, derived claim (e.g. '3 issues are blocked and past their due date'), 'badge' "
    "tone to 'critical'/'warning' to match its urgency, and 'subtitle' to one more sentence of "
    "real context if useful. Only use this when there's one real, specific thing worth flagging "
    "— never invent an alert when the honest answer is 'nothing urgent right now' (say that in "
    "the heading/meta instead).\n"
    "- 'task_queue': when a request is about outstanding work/to-dos rather than a general list "
    "— 'what's overdue', 'what does Priya still need to do'. Same row shape as 'list' (name = "
    "issue key + summary, note = assignee and/or real due date, e.g. 'Due 2026-09-02'), rendered "
    "as a checklist instead — use it when the request frames things as work to get done, use "
    "plain 'list' when it's just asking what exists.\n"
    "- 'data_table': when a request needs several real fields compared side by side across "
    "multiple issues — e.g. 'show me open bugs with their assignee and due date' — use real "
    "columns (2-5 of them, e.g. ['Key', 'Summary', 'Assignee', 'Due']) instead of cramming "
    "extra facts into a list row's one 'note' string. Each table_rows entry's 'values' must "
    "have one real value per column, same order. This is often a better fit than 'list' "
    "specifically when the request names more than two fields it cares about.\n\n"
    "Only call ask_user first if the request is genuinely ambiguous in a way that would change "
    "what you'd fetch — and only once. After the person answers, proceed straight to get_issues "
    "and render_view; do not ask a second question in the same request, and do not ask about "
    "things you could reasonably infer yourself. A clarifying question must name the actual "
    "options — never just repeat the person's own request back as a question; that isn't "
    "clarifying anything.\n\n"
    "Critical: render_view's fields must contain real, literal data — the actual issues "
    "returned by get_issues (real keys, real summaries, real assignees/statuses/priorities/etc). "
    "NEVER invent an issue or write placeholder/template syntax such as {{get_issues(...)}}. This "
    "also means never writing the NAME of a field as its VALUE — a row's 'name'/'note', or a "
    "field's 'value', must be the actual issue key/summary/status/priority/assignee text itself, "
    "never the literal words 'Summary', 'Status', 'Priority', 'Assignee', 'Issue 1', 'Issue 2', "
    "'ENG-1', or similar column-header-style placeholders standing in for it. If you're not "
    "looking at the real value get_issues returned, don't write it. It's normal and expected for "
    "a filtered query to come back with very few results, or none — render that honestly rather "
    "than padding it out. 'components' must be an actual JSON array of component objects, not a "
    "string.\n\n"
    "Memory: if this conversation already includes a line like 'Built \"...\"' describing "
    "something you built earlier, that's a real screen you already showed this person, not a "
    "blank slate — a short follow-up like 'now just the blocked ones' or 'add story points' "
    "means adjust or narrow THAT, using it as context, not answer some unrelated interpretation "
    "of the words alone. Still call get_issues again to get current data (issues can change "
    "between requests), but keep the same intent in mind when deciding what to fetch and how to "
    "shape the screen."
)


# See agent_gmail.py's copy of this constant/function for the full reasoning.
# "Which data source" is already answered by this being the Jira tool, so the
# bundled question below only needs to cover purpose and layout/style.
FIRST_TURN_CLARIFY_PARAGRAPH = (
    "\n\nOne more thing, for right now only: this is the very first request in a brand-new "
    "conversation, so before calling get_issues or render_view, call ask_user ONE time with "
    "a single bundled question covering both (a) what they actually want to see — if the "
    "request is broad ('what needs attention'), offer 2-3 concrete options tailored to it "
    "(e.g. 'blocked issues, highest-priority issues, or something else?'); skip this half if "
    "they were already specific — and (b) how they'd like it laid out (a compact list, a "
    "stat summary up top, or something more detailed). Ask both in ONE message, never two "
    "separate questions. This replaces the ambiguity-only ask_user rule above for this one "
    "first request; every later request in this same conversation goes back to that normal "
    "rule — never ask this bundled question again."
)

FIRST_TURN_GATE_NUDGE = (
    "Not yet: this is the very first request of a brand-new conversation, so call ask_user "
    "first with one bundled question covering purpose and layout/style before fetching or "
    "rendering anything — see the instructions for exactly what to cover."
)


def _build_system(first_turn=False, extra_system=""):
    """See agent_gmail.py's copy of this function for the full reasoning, including
    extra_system (layout_usage.py's per-role usage hint)."""
    base = SYSTEM + FIRST_TURN_CLARIFY_PARAGRAPH if first_turn else SYSTEM
    return base + "\n\n" + extra_system if extra_system else base


# ---- Guardrails — all model-agnostic, same pattern as agent_helpdesk.py ----

_PLACEHOLDER_VALUES = {
    "summary", "status", "priority", "assignee", "reporter", "sprint", "epic",
    "type", "key", "project", "label", "labels", "component", "components",
}
_PLACEHOLDER_PATTERN = re.compile(r"^(issue|eng|des)[\s-]*\d+$", re.IGNORECASE)


def _find_placeholder_labels(view):
    """Connector-local safety net, layered on top of guardrails.py's generic fabrication
    check: catches the model writing a field's own NAME as its VALUE (row name/note, or a
    field's value, literally "Status", "Assignee", "Issue 1", "ENG-1", ...) instead of the
    real fetched issue text. guardrails.py's check deliberately skips single-word values, to
    avoid false-flagging real short UI text like "Blocked" or "Done" — which is exactly the
    gap these placeholders exploit, since each one is a single word."""
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
        for field in (comp.get("fields") or []):
            if not isinstance(field, dict):
                continue
            val = field.get("value")
            if isinstance(val, str):
                v = val.strip().lower()
                if v in _PLACEHOLDER_VALUES or _PLACEHOLDER_PATTERN.match(v):
                    bad.append(val)
    seen = set()
    return [b for b in bad if not (b in seen or seen.add(b))]


NO_DATA_YET_NUDGE = (
    "render_view was rejected: you haven't called get_issues yet, so you have no real "
    "data to show. Call it first, wait for its real result, then call render_view again "
    "using those real values."
)

ALREADY_FETCHED_NUDGE = (
    "get_issues was not run again: you already have its real result earlier in this "
    "conversation. Use those exact real values now in a render_view call instead of fetching "
    "again."
)

ALREADY_ASKED_NUDGE = (
    "ask_user was rejected: you already asked one clarifying question in this conversation "
    "and the person just answered it. Do not ask another question — use their answer to call "
    "get_issues now, then render_view."
)


def _is_lazy_clarifying_question(question, user_request):
    """High word overlap with the original request and almost no new vocabulary means the
    question is just echoing the request back instead of actually narrowing it."""
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
    "ask_user was rejected: the question just restates the person's original request "
    "instead of narrowing it — that doesn't clarify anything. A genuine clarifying "
    "question names the specific ambiguity and offers concrete options (e.g. blocked "
    "issues, highest-priority issues, or something else?). Either ask a real, narrowing "
    "question with actual options in it, or skip ask_user entirely and call get_issues "
    "with your best reasonable interpretation."
)


def _make_dispatch(seed_fetched_data, verbose, first_turn=False):
    """Builds a fresh dispatch() closure for ONE run_agent() call, with its own private
    fetched-data state — see agent_helpdesk.py's copy of this function for the full
    reasoning (same static-connector shape, no tool_failures tracking)."""
    state = {"fetched_data": seed_fetched_data}

    def dispatch(name, args, tc_id, prior_messages, _seed_fetched_data_unused):
        if name == "get_issues":
            if first_turn:
                return ce.ToolOutcome(tool_result=FIRST_TURN_GATE_NUDGE)
            if state["fetched_data"]:
                return ce.ToolOutcome(tool_result=ALREADY_FETCHED_NUDGE)
            try:
                result = get_issues(**args)
            except Exception as e:
                if verbose:
                    print(f"  [dispatch] get_issues raised: {e}", file=sys.stderr)
                return ce.ToolOutcome(tool_result=json.dumps({"error": str(e)}))
            state["fetched_data"] = True
            if verbose:
                print(f"  [dispatch] get_issues({args}) — {len(result) if isinstance(result, list) else '?'} issue(s)", file=sys.stderr)
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
                + find_ungrounded_alert_claims(args, prior_messages)
                + _find_placeholder_labels(args)
            )
            if fabricated:
                if verbose:
                    print(f"  [dispatch] render_view REJECTED: fabricated content not grounded in real fetched data: {fabricated}", file=sys.stderr)
                return ce.ToolOutcome(tool_result=fabricated_content_nudge(fabricated))

            if verbose:
                print(f"  [dispatch] render_view accepted — heading={args.get('heading')!r}", file=sys.stderr)
            return ce.ToolOutcome(final={"render": args})

        return ce.ToolOutcome(tool_result="unknown tool")

    return dispatch


def run_agent(user_request=None, max_steps=8, verbose=True, messages=None, fetched_data=False, first_turn=False, extra_system=""):
    """Same calling convention as every other connector — see this module's docstring and
    claude_engine.run_claude_agent's docstring for the full contract. No `user` argument here —
    this connector doesn't scope by identity."""
    dispatch = _make_dispatch(fetched_data, verbose, first_turn)
    return ce.run_claude_agent(
        dispatch,
        system=_build_system(first_turn, extra_system),
        tools=TOOLS,
        max_steps=max_steps,
        verbose=verbose,
        messages=messages,
        user_request=user_request,
        fetched_data=fetched_data,
        force_tool_choice=True,
    )


if __name__ == "__main__":
    from pathlib import Path
    import webbrowser
    from renderer import render_html

    query = sys.argv[1] if len(sys.argv) > 1 else "what's blocked right now?"
    print(f"REQUEST: {query}\n", file=sys.stderr)

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
