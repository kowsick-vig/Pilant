"""
Composition Engine wired to connectors_helpdesk.py — the fully static/dummy
connector (see its docstring). No identity scoping here on purpose (any
logged-in staff member sees all tickets) — this demo isn't re-proving the
access-control story agent_github.py already covers.

MIGRATED 2026-08-26 from NVIDIA's free-tier meta/llama-3.1-8b-instruct to
Claude (via the shared claude_engine.py — see that module's docstring for
the full "why"). Fifth and last connector migrated, after agent_custom.py
(the pilot), agent_gmail.py, agent_slack.py, and agent_github.py.

What's GONE, same reasoning as every other port this same day: the entire
JSON-repair stack (_close_truncated_json, _repair_blanket_quoted_json,
_insert_missing_dict_closers, normalize_args, _repair_candidates,
_scrub_surrogates, _unescape_literal_unicode_escapes), the
_parse_tool_calls_from_text() plain-text-tool-call recovery path, and the
"drop every tool_call past the first" single-tool-call workaround — none of
it applies to Claude's Messages API, where tool_use blocks arrive as an
already-parsed, schema-validated dict (block.input).

Like agent_github.py (and UNLIKE agent_gmail.py/agent_slack.py), this
connector never accepts a plain conversational `{"text": ...}` reply — the
SYSTEM prompt says it must always call render_view before finishing, so
run_agent uses force_tool_choice=True.

One deliberate NON-change worth calling out: the pre-migration version's
tool-call handling never tracked repeated-identical-failure state for
get_tickets the way agent_gmail.py/agent_slack.py/agent_github.py's dispatch
does for their own fetch tools (no "failed identically twice in a row, stop
early" short-circuit here) — since get_tickets is a fully static/dummy
connector (see connectors_helpdesk.py's docstring), it can't fail the way a
real network-backed API call can, so there was never anything for that
guardrail to catch. This port preserves that as-is rather than adding a
guardrail the original never had.

run_agent()'s calling convention (fresh call vs. messages=/fetched_data=
resume-after-clarify) is UNCHANGED, so studio.py dispatches to this one
exactly the same generic way it dispatches to every other connector — see
studio.py's CONNECTORS registry and _run_agent_for_workflow(). studio.py
needed zero changes for this migration.
"""

import json
import re
import sys

from schema import UI_SCHEMA
from connectors_helpdesk import get_tickets
from validation import validate_view
from guardrails import find_fabricated_content, find_fabricated_stats, fabricated_content_nudge
import claude_engine as ce

TOOLS = [
    {
        "name": "get_tickets",
        "description": (
            "Fetch support tickets, optionally filtered by status "
            "(open/in_progress/resolved/escalated), priority "
            "(low/medium/high/critical), and/or category "
            "(account/billing/bug/feature_request)."
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
        "name": "ask_user",
        "description": (
            "Ask the person a single clarifying question before doing anything else. Use "
            "this ONLY when the request is genuinely ambiguous in a way that would change "
            "what you'd fetch — e.g. 'what needs attention' could mean escalated tickets, "
            "open tickets, or something else. The question must NAME the specific ambiguity "
            "and offer concrete options — it must add information the person doesn't already "
            "have, not just repeat their own words back as a question. Do NOT use this for "
            "requests you can reasonably interpret yourself. Ask AT MOST ONE question per "
            "request: after the person answers, proceed straight to fetching data and "
            "rendering."
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
            "the components required to answer the request — no unrelated tickets, no "
            "default dashboard."
        ),
        "input_schema": UI_SCHEMA,
    },
]

SYSTEM = (
    "You are the Composition Engine inside Pilant, embedded in a software company's internal "
    "support-desk tool. A staff member has typed a request about support tickets. Most "
    "requests are clear enough to act on directly: call get_tickets with whatever filters "
    "answer it, then call render_view exactly once with a screen that shows ONLY what they "
    "asked for — no unrelated tickets, no default dashboard. Use judgment on badge tones: "
    "escalated tickets and critical priority → tone 'critical'; open tickets and high priority "
    "→ tone 'warning'; resolved tickets → tone 'good'; anything else → tone 'default'. Do not "
    "pad the screen with anything unrequested. You must call render_view before finishing — "
    "do not answer in plain text.\n\n"
    "Component choice for multiple tickets, this matters: when the request returns more than "
    "one ticket, put them ALL in a SINGLE 'list' component — one row per ticket (row 'name' = "
    "the ticket subject or a short id+subject combo, row 'note' = the requester or a short "
    "status/priority summary, badge for status/priority if useful) — never one 'panel' "
    "component per ticket. A panel-per-ticket wastes an entire nested fields array on every "
    "single ticket and will run you out of output budget before you finish the JSON, which is "
    "exactly the malformed-output failure you must avoid. Reserve 'panel' for when there is "
    "exactly ONE ticket to show in detail, or for a handful of aggregate stats — not for "
    "enumerating a list of tickets. Do not add a second 'panel' component next to the list just "
    "to restate a count — the list itself already shows every ticket; a redundant summary panel "
    "is exactly the kind of unrequested padding to avoid.\n\n"
    "Only call ask_user first if the request is genuinely ambiguous in a way that would "
    "change what you'd fetch — and only once. After the person answers, proceed straight to "
    "get_tickets and render_view; do not ask a second question in the same request, and do "
    "not ask about things you could reasonably infer yourself. A clarifying question must name "
    "the actual options — never just repeat the person's own request back as a question; that "
    "isn't clarifying anything.\n\n"
    "Critical: render_view's fields must contain real, literal data — the actual tickets "
    "returned by get_tickets (real subjects, real requesters, real status/priority/category "
    "values). NEVER invent a ticket or write placeholder/template syntax such as "
    "{{get_tickets(...)}}. This also means never writing the NAME of a field as its VALUE — a "
    "row's 'name'/'note', or a field's 'value', must be the actual ticket subject/requester/"
    "status/priority/category text itself, never the literal words 'Subject', 'Status', "
    "'Priority', 'Category', 'Ticket 1', 'Ticket 2', or similar column-header-style "
    "placeholders standing in for it. If you're not looking at the real value get_tickets "
    "returned, don't write it. It's normal and expected for a filtered query to come back with "
    "very few results, or none — render that honestly rather than padding it out. "
    "'components' must be an actual JSON array of component objects, not a string.\n\n"
    "Memory: if this conversation already includes a line like 'Built \"...\"' describing "
    "something you built earlier, that's a real screen you already showed this person, not a "
    "blank slate — a short follow-up like 'now just the escalated ones' or 'add the requester' "
    "means adjust or narrow THAT, using it as context, not answer some unrelated interpretation "
    "of the words alone. Still call get_tickets again to get current data (tickets can change "
    "between requests), but keep the same intent in mind when deciding what to fetch and how to "
    "shape the screen."
)


# Added 2026-08-26 — see agent_gmail.py's copy of this constant/function for the full
# reasoning. "Which data source" is already answered by this being the Helpdesk tool, so the
# bundled question below only needs to cover purpose and layout/style.
FIRST_TURN_CLARIFY_PARAGRAPH = (
    "\n\nOne more thing, for right now only: this is the very first request in a brand-new "
    "conversation, so before calling get_tickets or render_view, call ask_user ONE time with "
    "a single bundled question covering both (a) what they actually want to see — if the "
    "request is broad ('what needs attention'), offer 2-3 concrete options tailored to it "
    "(e.g. 'escalated tickets, high-priority tickets, or something else?'); skip this half if "
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
    extra_system (added 2026-08-30, layout_usage.py's per-role usage hint)."""
    base = SYSTEM + FIRST_TURN_CLARIFY_PARAGRAPH if first_turn else SYSTEM
    return base + "\n\n" + extra_system if extra_system else base


# ---- Guardrails — all model-agnostic, ported unchanged from the NVIDIA version ----

_PLACEHOLDER_VALUES = {"subject", "status", "priority", "category", "requester", "id", "ticket", "description"}
_PLACEHOLDER_PATTERN = re.compile(r"^ticket\s*\d+$", re.IGNORECASE)


def _find_placeholder_labels(view):
    """Connector-local safety net, layered on top of guardrails.py's generic fabrication
    check: catches the model writing a field's own NAME as its VALUE (row name/note, or a
    field's value, literally "Subject", "Status", "Ticket 1", ...) instead of the real fetched
    ticket text. guardrails.py's check deliberately skips single-word values, to avoid
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
    "render_view was rejected: you haven't called get_tickets yet, so you have no real "
    "data to show. Call it first, wait for its real result, then call render_view again "
    "using those real values."
)

ALREADY_FETCHED_NUDGE = (
    "get_tickets was not run again: you already have its real result earlier in this "
    "conversation. Use those exact real values now in a render_view call instead of fetching "
    "again."
)

ALREADY_ASKED_NUDGE = (
    "ask_user was rejected: you already asked one clarifying question in this conversation "
    "and the person just answered it. Do not ask another question — use their answer to call "
    "get_tickets now, then render_view."
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
    "question names the specific ambiguity and offers concrete options (e.g. escalated "
    "tickets, high-priority tickets, or something else?). Either ask a real, narrowing "
    "question with actual options in it, or skip ask_user entirely and call get_tickets "
    "with your best reasonable interpretation."
)


def _make_dispatch(seed_fetched_data, verbose, first_turn=False):
    """Builds a fresh dispatch() closure for ONE run_agent() call, with its own private
    fetched-data state — see agent_gmail.py's copy of this function for the full reasoning. No
    tool_failures tracking here — see this module's docstring on why that guardrail (present in
    agent_gmail.py/agent_slack.py/agent_github.py) was never needed for a static/dummy connector
    like this one. first_turn: see agent_gmail.py's copy of this docstring for the full
    reasoning — code-enforced backstop for FIRST_TURN_CLARIFY_PARAGRAPH."""
    state = {"fetched_data": seed_fetched_data}

    def dispatch(name, args, tc_id, prior_messages, _seed_fetched_data_unused):
        if name == "get_tickets":
            if first_turn:
                return ce.ToolOutcome(tool_result=FIRST_TURN_GATE_NUDGE)
            if state["fetched_data"]:
                return ce.ToolOutcome(tool_result=ALREADY_FETCHED_NUDGE)
            try:
                result = get_tickets(**args)
            except Exception as e:
                if verbose:
                    print(f"  [dispatch] get_tickets raised: {e}", file=sys.stderr)
                return ce.ToolOutcome(tool_result=json.dumps({"error": str(e)}))
            state["fetched_data"] = True
            if verbose:
                print(f"  [dispatch] get_tickets({args}) — {len(result) if isinstance(result, list) else '?'} ticket(s)", file=sys.stderr)
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


def run_agent(user_request=None, max_steps=8, verbose=True, messages=None, fetched_data=False, first_turn=False, extra_system=""):
    """Same calling convention as before and as every other connector — see this module's
    docstring and claude_engine.run_claude_agent's docstring for the full contract. No `user`
    argument here — this connector doesn't scope by identity. first_turn: see agent_gmail.py's
    copy of this docstring for the full reasoning; defaults False so every existing caller is
    unaffected. extra_system: see agent_gmail.py's copy; defaults ""."""
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

    query = sys.argv[1] if len(sys.argv) > 1 else "what tickets need attention right now?"
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
