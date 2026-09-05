"""
Composition Engine wired to connectors_github.py — a REAL connector against
a live GitHub repository's issue tracker (see that file's docstring for the
token/repo setup).

MIGRATED 2026-08-26 from NVIDIA's free-tier meta/llama-3.1-8b-instruct to
Claude (via the shared claude_engine.py — see that module's docstring for
the full "why"). Fourth connector migrated, after agent_custom.py (the
pilot), agent_gmail.py, and agent_slack.py.

What's GONE, same reasoning as every other port this same day: the entire
JSON-repair stack (_close_truncated_json, _repair_blanket_quoted_json,
_insert_missing_dict_closers, normalize_args, _repair_candidates,
_scrub_surrogates, _unescape_literal_unicode_escapes), the
_parse_tool_calls_from_text() plain-text-tool-call recovery path, and the
"drop every tool_call past the first" single-tool-call workaround — none of
it applies to Claude's Messages API, where tool_use blocks arrive as an
already-parsed, schema-validated dict (block.input).

What's DIFFERENT here vs. agent_gmail.py/agent_slack.py's ports, both
preserved exactly:
  - Identity scoping: get_github_issues only ever returns issues the
    requesting user is authorized to see (users.py's scope_issues(), keyed
    off user['label_scope']) — applied after EVERY successful fetch, same
    as the NVIDIA version did on both its real-tool-call path and its
    now-deleted recovered-text path. run_agent() keeps its extra `user`
    kwarg (see studio.py's CONNECTORS['github']['needs_user'] flag — the
    only connector that gets one) and _make_dispatch's closure captures it
    alongside the usual fetched_data/tool_failures state.
  - No conversational-reply mode: like agent_custom.py (and UNLIKE
    agent_gmail.py/agent_slack.py), this connector's SYSTEM prompt says
    "You must call render_view before finishing — do not answer in plain
    text", so run_agent calls claude_engine.run_claude_agent with
    force_tool_choice=True (tool_choice={"type":"any"} every step) instead
    of letting Claude choose text vs. tool freely.

No on_no_tool_call hook is needed here for the same reason agent_custom.py
doesn't have one — force_tool_choice=True means every step must produce a
tool_use block, so the "no tool call" branch in claude_engine.py's loop
only fires as a genuine, rare violation of that constraint, which the
engine's default nudge already handles.

run_agent()'s calling convention (fresh call vs. messages=/fetched_data=
resume-after-clarify, plus the `user` kwarg) is UNCHANGED, so studio.py
dispatches to this one exactly the same way it dispatches to every other
connector — see studio.py's CONNECTORS registry and
_run_agent_for_workflow(). studio.py needed zero changes for this
migration.
"""

import json
import re
import sys

from schema import UI_SCHEMA
from connectors_github import get_github_issues
from validation import validate_view
from users import get_user, scope_issues, DEFAULT_USER
from guardrails import find_fabricated_content, find_fabricated_stats, fabricated_content_nudge
import rag_index
from rag_scope import scope_knowledge_base_results as _scope_knowledge_base_results
import claude_engine as ce

TOOLS = [
    {
        "name": "get_github_issues",
        "description": (
            "Fetch real issues from the configured GitHub repository (set via GITHUB_REPO "
            "in .env), optionally filtered by state and by a single label."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "state": {"type": "string", "enum": ["open", "closed", "all"]},
                "label": {
                    "type": "string",
                    "description": "Filter to issues with this exact label name, e.g. 'bug' or 'critical'.",
                },
            },
        },
    },
    {
        # Added 2026-08-26 — see agent_gmail.py's copy of this tool entry
        # for the general reasoning. UNLIKE that copy, this one's dispatch
        # handler re-applies users.scope_issues() to github-sourced results
        # (see below) — the whole reason this connector exists is to prove
        # identity scoping holds on every real fetch path, and semantic
        # search over a pre-built index is still a fetch path.
        "name": "search_knowledge_base",
        "description": (
            "Semantic search over a pre-built local knowledge base indexed from this repo's "
            "issues (and Gmail/Slack, if also connected) — use this INSTEAD OF "
            "get_github_issues when the request needs older/closed-issue history, a broad or "
            "fuzzy topic search, or something that spans multiple connected sources at once, "
            "rather than a state/label filter. `query` is a natural-language description of "
            "what to find. `source`, if given, restricts results to 'github' only — omit it "
            "to also search Gmail/Slack if connected. Same identity scoping as "
            "get_github_issues applies here too: results you're not authorized to see are "
            "already filtered out before you see them. Returns an empty list if nobody has "
            "synced the knowledge base yet — that's not an error, just nothing indexed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to find, in plain language."},
                "source": {"type": "string", "enum": ["gmail", "slack", "github"]},
            },
            "required": ["query"],
        },
    },
    {
        "name": "ask_user",
        "description": (
            "Ask the person a single clarifying question before doing anything else. Use "
            "this ONLY when the request is genuinely ambiguous in a way that would change "
            "what you'd fetch or how you'd filter it — e.g. 'what needs attention' could "
            "mean open issues, issues waiting on review, or something else entirely, and "
            "guessing wrong would mean showing the wrong screen. Do NOT use this for "
            "requests you can reasonably interpret yourself (most requests are this kind — "
            "just fetch data and render). Ask AT MOST ONE question per request: once the "
            "person answers, proceed straight to fetching data and rendering — never ask a "
            "second clarifying question in the same request."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "One short, specific question — not a list of questions.",
                }
            },
            "required": ["question"],
        },
    },
    {
        "name": "render_view",
        "description": (
            "Emit the final generated screen for the user's request. Call this exactly once, "
            "as the last step, after fetching whatever data you need. Include ONLY the "
            "components required to answer the request — no unrelated data, no default dashboard."
        ),
        "input_schema": UI_SCHEMA,
    },
]

SYSTEM = (
    "You are the Composition Engine inside Pilant, connected to a live GitHub repository's "
    "issue tracker. A person has typed a request about what's going on in this repo. Most "
    "requests are clear enough to act on directly: call get_github_issues with whatever "
    "filters answer it, then call render_view exactly once with a screen that shows ONLY what "
    "they asked for — no unrelated issues, no default dashboard. Use judgment on labels and "
    "titles to decide tone for badges: issues labeled 'bug', 'critical', or similarly urgent → "
    "tone 'critical'; 'enhancement' or clearly minor → tone 'good'; anything else → tone "
    "'default'. Do not pad the screen with anything unrequested. You must call render_view "
    "before finishing — do not answer in plain text.\n\n"
    "Only call ask_user first if the request is genuinely ambiguous in a way that would "
    "change what you'd fetch — and only once. After the person answers your question, proceed "
    "straight to get_github_issues and render_view; do not ask a second question in the same "
    "request, and do not ask ask_user questions about things you could reasonably infer "
    "yourself (default to acting, not asking).\n\n"
    "get_github_issues only reaches this repo's current issues (state/label filters only, no "
    "pagination). If the request needs older/closed-issue history that's easier to describe than "
    "to filter for, a broad or fuzzy topic search, or something that might span Gmail/Slack too, "
    "call search_knowledge_base instead — it's semantic search over a separately-synced local "
    "index, not a live GitHub API call. Call ONE of the two fetch tools per request, never both "
    "— pick whichever actually fits what's being asked. search_knowledge_base results go through "
    "the same identity scoping as get_github_issues before you ever see them.\n\n"
    "Critical: render_view's fields must contain real, literal data — the actual issues "
    "returned by get_github_issues. NEVER invent an issue or write placeholder/template "
    "syntax such as {{get_github_issues(...)}}. If you need data, call the tool first, wait "
    "for its real result in the conversation, then copy the real values into render_view. "
    "'components' must be an actual JSON array of component objects, not a string.\n\n"
    "get_github_issues only ever returns issues the requesting user is authorized to see — "
    "treat whatever it returns as the complete real dataset available to you. Don't mention "
    "or speculate about restricted or hidden issues.\n\n"
    "Memory: if this conversation already includes a line like 'Built \"...\"' describing "
    "something you built earlier, that's a real screen you already showed this person, not a "
    "blank slate — a short follow-up like 'now just the critical ones' or 'add who opened each "
    "one' means adjust or narrow THAT, using it as context, not answer some unrelated "
    "interpretation of the words alone. Still call get_github_issues again to get current data "
    "(issues can change between requests), but keep the same intent in mind when deciding what "
    "to fetch and how to shape the screen."
)


# Added 2026-08-26 — see agent_gmail.py's copy of this constant/function for the full
# reasoning. "Which data source" is already answered by this being the GitHub chat, so the
# bundled question below only needs to cover purpose and layout/style.
FIRST_TURN_CLARIFY_PARAGRAPH = (
    "\n\nOne more thing, for right now only: this is the very first request in a brand-new "
    "conversation, so before calling get_github_issues, search_knowledge_base, or "
    "render_view, call ask_user ONE time with a single bundled question covering both (a) "
    "what they actually want to see — if the request is broad ('what needs attention'), "
    "offer 2-3 concrete options tailored to it (e.g. 'open issues, issues waiting on review, "
    "or something else?'); skip this half if they were already specific — and (b) how "
    "they'd like it laid out (a compact list, a stat summary up top, or something more "
    "detailed). Ask both in ONE message, never two separate questions. This replaces the "
    "ambiguity-only ask_user rule above for this one first request; every later request in "
    "this same conversation goes back to that normal rule — never ask this bundled question "
    "again."
)

FIRST_TURN_GATE_NUDGE = (
    "Not yet: this is the very first request of a brand-new conversation, so call ask_user "
    "first with one bundled question covering purpose and layout/style before fetching or "
    "rendering anything — see the instructions for exactly what to cover."
)


def _build_system(first_turn=False, extra_system=""):
    """See agent_gmail.py's copy of this function for the full reasoning, including
    extra_system (added 2026-08-30, layout_usage.py's per-role usage hint)."""
    base = SYSTEM + FIRST_TURN_CLARIFY_PARAGRAPH if first_turn else SYSTEM
    return base + "\n\n" + extra_system if extra_system else base


# ---- Guardrails — all model-agnostic, ported unchanged from the NVIDIA version ----

_PLACEHOLDER_VALUES = {"title", "label", "labels", "author", "state", "status", "assignee", "issue", "number"}
_PLACEHOLDER_PATTERN = re.compile(r"^issue\s*\d+$", re.IGNORECASE)


def _find_placeholder_labels(view):
    """Connector-local safety net, layered on top of guardrails.py's generic fabrication
    check: catches the model writing a field's own NAME as its VALUE (row name/note, or a
    field's value, literally "Title", "Label", "Author", "Issue 1", ...) instead of the real
    fetched issue text. guardrails.py's check deliberately skips single-word values, to avoid
    false-flagging real short UI text like "Open" or "Critical" — which is exactly the gap these
    placeholders exploit, since each one is a single word."""
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
    "render_view was rejected: you haven't called get_github_issues yet, so you have no "
    "real data to show. Call it first, wait for its real result, then call render_view "
    "again using those real values."
)

ALREADY_FETCHED_NUDGE = (
    "get_github_issues was not run again: you already have its real result earlier in this "
    "conversation. Use those exact real values now in a render_view call instead of fetching "
    "again."
)

# Shared between get_github_issues and search_knowledge_base — see
# agent_gmail.py's copy of this constant for the full reasoning.
ALREADY_FETCHED_NUDGE_GENERIC = (
    "No fetch tool was run again: you already have real fetched data earlier in this "
    "conversation (from get_github_issues or search_knowledge_base). Use those exact real "
    "values now in a render_view call instead of fetching again."
)

ALREADY_ASKED_NUDGE = (
    "ask_user was rejected: you already asked one clarifying question in this conversation "
    "and the person just answered it. Do not ask another question — use their answer to call "
    "get_github_issues now, then render_view."
)


def _is_lazy_clarifying_question(question, user_request):
    """High word overlap with the original request and almost no new vocabulary means the
    question is just echoing the request back instead of actually narrowing it — e.g. asked
    "what issues do you want to see?" in response to "what needs attention in this repo?" is
    lazy; "do you mean open issues, or issues that are open but waiting on review?" is a real
    clarifying question because it names concrete options the person didn't already give."""
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
    "question names the specific ambiguity and offers concrete options (e.g. 'do you mean "
    "open issues, or issues that are open but waiting on review?'). Either ask a real, "
    "narrowing question with actual options in it, or skip ask_user entirely and call "
    "get_github_issues with your best reasonable interpretation."
)


def _make_dispatch(seed_fetched_data, verbose, user, first_turn=False):
    """Builds a fresh dispatch() closure for ONE run_agent() call, with its own private
    fetched-data and tool-failure state — see agent_gmail.py's copy of this function for the
    full reasoning. Also captures `user` for identity scoping (scope_issues()), the one thing
    this connector's dispatch needs that agent_gmail.py's/agent_slack.py's didn't. first_turn:
    see agent_gmail.py's copy of this docstring for the full reasoning — code-enforced
    backstop for FIRST_TURN_CLARIFY_PARAGRAPH."""
    state = {"fetched_data": seed_fetched_data, "tool_failures": {}}

    def dispatch(name, args, tc_id, prior_messages, _seed_fetched_data_unused):
        if name == "get_github_issues":
            if first_turn:
                return ce.ToolOutcome(tool_result=FIRST_TURN_GATE_NUDGE)
            if state["fetched_data"]:
                return ce.ToolOutcome(tool_result=ALREADY_FETCHED_NUDGE)
            try:
                result = get_github_issues(**args)
                before = len(result)
                result = scope_issues(result, user)
                if verbose and len(result) != before:
                    print(f"  [dispatch] identity scoping: {before} fetched -> {len(result)} authorized for {user['username']}", file=sys.stderr)
            except Exception as e:
                err_str = str(e)
                if verbose:
                    print(f"  [dispatch] get_github_issues raised: {e}", file=sys.stderr)
                if state["tool_failures"].get(name) == err_str:
                    if verbose:
                        print("  [dispatch] failed identically twice in a row — not retryable, stopping early", file=sys.stderr)
                    return ce.ToolOutcome(final={"error": err_str})
                state["tool_failures"][name] = err_str
                return ce.ToolOutcome(tool_result=json.dumps({"error": err_str}))
            state["fetched_data"] = True
            if verbose:
                print(f"  [dispatch] get_github_issues({args}) — {len(result) if isinstance(result, list) else '?'} issue(s)", file=sys.stderr)
            return ce.ToolOutcome(tool_result=json.dumps(result))

        if name == "search_knowledge_base":
            if first_turn:
                return ce.ToolOutcome(tool_result=FIRST_TURN_GATE_NUDGE)
            if state["fetched_data"]:
                return ce.ToolOutcome(tool_result=ALREADY_FETCHED_NUDGE_GENERIC)
            try:
                result = rag_index.search(args.get("query", ""), source=args.get("source"))
                before = len(result)
                result = _scope_knowledge_base_results(result, user)
                if verbose and len(result) != before:
                    print(f"  [dispatch] identity scoping: {before} found -> {len(result)} authorized for {user['username']}", file=sys.stderr)
            except RuntimeError as e:
                err_str = str(e)
                if verbose:
                    print(f"  [dispatch] search_knowledge_base raised: {e}", file=sys.stderr)
                return ce.ToolOutcome(tool_result=json.dumps({"error": err_str}))
            state["fetched_data"] = True
            if verbose:
                print(f"  [dispatch] search_knowledge_base({args}) — {len(result)} result(s)", file=sys.stderr)
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
                    print(f"  [dispatch] render_view REJECTED: fabricated content not grounded in real fetched data: {fabricated}", file=sys.stderr)
                return ce.ToolOutcome(tool_result=fabricated_content_nudge(fabricated))

            if verbose:
                print(f"  [dispatch] render_view accepted — heading={args.get('heading')!r}", file=sys.stderr)
            return ce.ToolOutcome(final={"render": args})

        return ce.ToolOutcome(tool_result="unknown tool")

    return dispatch


def run_agent(user_request=None, user=None, max_steps=8, verbose=True, messages=None, fetched_data=False, first_turn=False, extra_system=""):
    """
    Same calling convention as before and as every other connector — see this module's
    docstring and claude_engine.run_claude_agent's docstring for the full contract — PLUS the
    `user` kwarg (required, for scope_issues() identity filtering): studio.py always passes it
    for this connector specifically (see CONNECTORS['github']['needs_user']).

    Two ways to call this:
    - Starting a fresh request: pass user_request and user. messages should be left as None.
    - Resuming after a clarifying question: pass user (still required), plus messages and
      fetched_data taken verbatim from a previous {"clarify": ...} result, with the person's
      answer already appended to messages as a {"role": "user", ...} turn. user_request is
      ignored in this case.

    first_turn: see agent_gmail.py's copy of this docstring for the full reasoning; defaults
    False so every existing caller is unaffected. extra_system: see agent_gmail.py's copy;
    defaults "".
    """
    dispatch = _make_dispatch(fetched_data, verbose, user, first_turn)
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

    query = sys.argv[1] if len(sys.argv) > 1 else "what open issues need attention right now?"
    username = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_USER
    user = get_user(username)

    scope_label = user["label_scope"] if user["label_scope"] is not None else "unrestricted"
    print(f"IDENTITY: {user['name']} (role: {user['role']}, scope: {scope_label})", file=sys.stderr)
    print(f"REQUEST: {query}\n", file=sys.stderr)

    result = run_agent(query, user)
    while "clarify" in result:
        print(f"\nCLARIFYING QUESTION: {result['clarify']}", file=sys.stderr)
        answer = input("your answer> ")
        resumed = result["messages"] + [{"role": "user", "content": answer}]
        result = run_agent(user=user, messages=resumed, fetched_data=result.get("fetched_data", False))

    print(json.dumps(result, indent=2, default=str))

    if "render" in result:
        out_path = Path(__file__).parent / "output.html"
        out_path.write_text(render_html(result["render"], query))
        print(f"\nrendered screen written to {out_path}", file=sys.stderr)
        webbrowser.open(out_path.as_uri())
