"""
Composition Engine wired to connectors_slack.py — a REAL connector against
Slack's Web API (see that file's docstring for the token/scope setup and
its honest scope limits: real channel history + a client-side text filter,
not real Slack search, not per-person "unread").

MIGRATED 2026-08-26 from NVIDIA's free-tier meta/llama-3.1-8b-instruct to
Claude (via the shared claude_engine.py — see that module's docstring for
the full "why"). Third connector migrated, after agent_custom.py (the
pilot) and agent_gmail.py — this one is structurally almost a clone of
agent_gmail.py's PRE-migration NVIDIA version (its own now-deleted docstring
said as much: the whole reliability layer was "copied over wholesale" from
Gmail), so this port follows agent_gmail.py's Claude port exactly, just with
Slack's tool/data shape substituted in.

What's GONE, same reasoning as agent_gmail.py's port: the entire JSON-repair
stack (_close_truncated_json, _repair_blanket_quoted_json,
_insert_missing_dict_closers, normalize_args, _repair_candidates,
_scrub_surrogates, _unescape_literal_unicode_escapes), the
_parse_tool_calls_from_text() plain-text-tool-call recovery path, the
"drop every tool_call past the first" single-tool-call workaround, and the
"looks_like_malformed_tool_call" text-sniffing heuristic — none of it
applies to Claude's Messages API, where tool_use blocks arrive as an
already-parsed, schema-validated dict (block.input), not a raw string to
repair or a heuristic guess about whether text was a dodged tool call.

What's the SAME as agent_gmail.py's port: real-fetch "was data already
fetched" state tracked via a closure local to each run_agent() call
(_make_dispatch below), the fabrication guardrail
(guardrails.find_fabricated_content/find_fabricated_stats) plus the
connector-local placeholder-label denylist (_find_placeholder_labels), and
force_tool_choice=False — genuine conversational replies ("thanks!", "what's
new?") are a real, expected outcome here too, so Claude decides for itself
whether to call a tool or just answer in text, via the on_no_tool_call hook.

No identity scoping here on purpose, same reasoning as agent_helpdesk.py —
this demo isn't re-proving the access-control story agent_github.py already
covers.

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
from connectors_slack import get_slack_messages
from validation import validate_view
from guardrails import find_fabricated_content, find_fabricated_stats, fabricated_content_nudge
from skills import load_skill
import rag_index
import claude_engine as ce

TOOLS = [
    {
        "name": "get_slack_messages",
        "description": (
            "Fetch recent real messages from the configured Slack channel, most recent "
            "first. Optionally filter to messages containing a specific word or phrase "
            "(a simple substring match, not full Slack search). `limit` caps how many "
            "recent messages to pull before filtering (default 20, max 100)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "contains": {
                    "type": "string",
                    "description": "Only include messages containing this word/phrase (case-insensitive).",
                },
                "limit": {"type": "integer", "description": "How many recent messages to fetch before filtering."},
            },
        },
    },
    {
        # Added 2026-08-26 — see agent_gmail.py's copy of this tool entry
        # for the full reasoning. Shares get_slack_messages' "one fetch per
        # turn" gate (state["fetched_data"]).
        "name": "search_knowledge_base",
        "description": (
            "Semantic search over a pre-built local knowledge base indexed from Slack (and "
            "Gmail/GitHub, if also connected) — use this INSTEAD OF get_slack_messages when "
            "the request needs older channel history beyond a normal recent-message fetch, a "
            "broad or fuzzy topic search, or something that spans multiple connected sources "
            "at once, rather than a quick recent/substring lookup. `query` is a "
            "natural-language description of what to find. `source`, if given, restricts "
            "results to 'slack' only — omit it to also search Gmail/GitHub if connected. "
            "Returns an empty list if nobody has synced the knowledge base yet — that's not "
            "an error, just nothing indexed to search."
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
            "what you'd fetch. The question must NAME the specific ambiguity and offer "
            "concrete options — it must add information the person doesn't already have, "
            "not just repeat their own words back as a question. Do NOT use this for "
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
            "the components required to answer the request — no unrelated messages, no "
            "default dashboard."
        ),
        "input_schema": UI_SCHEMA,
    },
]

SYSTEM = (
    "You are Pilant's Composition Engine, embedded as an AI Copilot connected to a real Slack "
    "channel. You're having a normal conversation with a person — some of what they say will be "
    "a real request to see channel messages, and some of it will just be talk.\n\n"
    "If they're asking about what's happening in the channel — recent messages, anything "
    "mentioning a specific word or topic, who said what — that's a data request: call "
    "get_slack_messages with whatever filter answers it, then call render_view exactly once "
    "with a screen that shows ONLY what they asked for — no unrelated messages, no default "
    "dashboard. Use judgment on badge tones if a message needs one (e.g. urgent/blocking "
    "language → tone 'critical'; a resolved/positive update → tone 'good'; otherwise 'default'). "
    "Do not pad the screen with anything unrequested.\n\n"
    "get_slack_messages only reaches recent channel history (it caps at 100 messages, no "
    "pagination) with a simple substring filter. If the request needs older history, a broad "
    "or fuzzy topic, or something that might span Gmail/GitHub too, call search_knowledge_base "
    "instead — it's semantic search over a separately-synced local index, not live Slack. Call "
    "ONE of the two fetch tools per request, never both — pick whichever actually fits what's "
    "being asked.\n\n"
    "Component choice for multiple messages, this matters: when the request returns more than "
    "one message, put them ALL in a SINGLE 'list' component — one row per message (row 'name' "
    "= the sender or a short sender+text combo, row 'note' = the message text) — never one "
    "'panel' component per message. A panel-per-message wastes an entire nested fields array on "
    "every single message and will run you out of output budget before you finish the JSON, "
    "which is exactly the malformed-output failure you must avoid. Reserve 'panel' for when "
    "there is exactly ONE message to show in detail, or for a handful of aggregate stats — not "
    "for enumerating a list of messages. Do not add a second 'panel' component next to the list "
    "just to restate a count — the list itself already shows every message; a redundant summary "
    "panel is exactly the kind of unrequested padding to avoid.\n\n"
    + load_skill("ui_composition") + "\n\n" +
    "If they're NOT asking for Slack data — a greeting, thanks, small talk, a question about "
    "what you can do, or anything else conversational — do not call any tool. Just reply "
    "normally in plain text, like a helpful, friendly assistant would. There's nothing to fetch "
    "or render for a message like that. Keep these replies short and natural, and when it fits, "
    "mention you can pull up real channel messages if they want to see something specific.\n\n"
    + load_skill("render_dont_narrate") + "\n\n" +
    "Only call ask_user first if a genuine data request is ambiguous in a way that would change "
    "what you'd fetch — and only once. After the person answers, proceed straight to "
    "get_slack_messages and render_view; do not ask a second question in the same request, and "
    "do not ask about things you could reasonably infer yourself. A clarifying question must "
    "name the actual options — never just repeat the person's own request back as a question; "
    "that isn't clarifying anything.\n\n"
    "Critical: render_view's fields must contain real, literal data — the actual messages "
    "returned by get_slack_messages (real sender names, real text). NEVER invent a sender or a "
    "message, and never write placeholder/template syntax such as {{get_slack_messages(...)}}. "
    "This also means never writing the NAME of a field as its VALUE — a row's 'name'/'note', or "
    "a field's 'value', must be the actual sender/message text itself, never the literal words "
    "'Sender', 'Message 1', 'Message 2', or similar column-header-style placeholders standing in "
    "for it. If you're not looking at the real string get_slack_messages returned, don't write "
    "it. It's normal and expected for a filtered query to come back with very few results, or "
    "none — render that honestly rather than padding it out. 'components' must be an actual "
    "JSON array of component objects, not a string.\n\n"
    "Memory: if this conversation already includes a line like 'Built \"...\"' describing "
    "something you built earlier, that's a real screen you already showed this person, not a "
    "blank slate — a short follow-up like 'make it more compact', 'now only the ones from "
    "Sarah', or 'add who they were replying to' means adjust or narrow THAT, using it as "
    "context, not answer some unrelated interpretation of the words alone. Still call "
    "get_slack_messages again to get current data (the channel can change between requests), "
    "but keep the same intent in mind when deciding what to fetch and how to shape the screen."
)


# Added 2026-08-26 — see agent_gmail.py's copy of this constant/function for the full
# reasoning (DynamisOS's up-front clarifying round vs. this file's reactive-only ask_user
# rule). "Which data source" is already answered by this being the Slack chat, so the bundled
# question below only needs to cover purpose and layout/style.
FIRST_TURN_CLARIFY_PARAGRAPH = (
    "\n\nOne more thing, for right now only: this is the very first request in a brand-new "
    "conversation, so before calling get_slack_messages, search_knowledge_base, or "
    "render_view, call ask_user ONE time with a single bundled question covering both (a) "
    "what they actually want to see — if the request is broad ('what's happening in the "
    "channel'), offer 2-3 concrete options tailored to it (e.g. 'recent activity, messages "
    "from someone specific, or something about a particular topic?'); skip this half if "
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

_PLACEHOLDER_VALUES = {"subject", "snippet", "from", "sender", "date", "recipient", "to"}
_PLACEHOLDER_PATTERN = re.compile(r"^(email|message)\s*\d+$", re.IGNORECASE)


def _find_placeholder_labels(view):
    """Connector-local safety net, layered on top of guardrails.py's generic fabrication
    check: catches the model writing a field's own NAME as its VALUE (row name/note, or a
    field's value, literally "Sender", "Message 1", ...) instead of the real fetched text.
    guardrails.py's check deliberately skips single-word values (to avoid false-flagging
    real short UI text like "Open" or "Critical"), which is exactly the gap these placeholders
    exploit, since each one is a single word."""
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
    "render_view was rejected: you haven't called get_slack_messages yet, so you have no real "
    "data to show. Call it first, wait for its real result, then call render_view again "
    "using those real values."
)

ALREADY_FETCHED_NUDGE = (
    "get_slack_messages was not run again: you already have its real result earlier in this "
    "conversation. Use those exact real values now in a render_view call instead of fetching "
    "again."
)

# Shared between get_slack_messages and search_knowledge_base — see
# agent_gmail.py's copy of this constant for the full reasoning.
ALREADY_FETCHED_NUDGE_GENERIC = (
    "No fetch tool was run again: you already have real fetched data earlier in this "
    "conversation (from get_slack_messages or search_knowledge_base). Use those exact real "
    "values now in a render_view call instead of fetching again."
)

ALREADY_ASKED_NUDGE = (
    "ask_user was rejected: you already asked one clarifying question in this conversation "
    "and the person just answered it. Do not ask another question — use their answer to call "
    "get_slack_messages now, then render_view."
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
    "question names the specific ambiguity and offers concrete options. Either ask a real, "
    "narrowing question with actual options in it, or skip ask_user entirely and call "
    "get_slack_messages with your best reasonable interpretation."
)


def _make_dispatch(seed_fetched_data, verbose, first_turn=False):
    """Builds a fresh dispatch() closure for ONE run_agent() call, with its own private
    fetched-data and tool-failure state — see agent_gmail.py's copy of this function for the
    full reasoning (a plain dispatch() callback can't mutate a caller's local by reference in
    Python, and each run_agent() call needs independent state anyway). first_turn: see
    agent_gmail.py's copy of this docstring for the full reasoning — a fixed closure value,
    code-enforced backstop for FIRST_TURN_CLARIFY_PARAGRAPH."""
    state = {"fetched_data": seed_fetched_data, "tool_failures": {}}

    def dispatch(name, args, tc_id, prior_messages, _seed_fetched_data_unused):
        if name == "get_slack_messages":
            if first_turn:
                return ce.ToolOutcome(tool_result=FIRST_TURN_GATE_NUDGE)
            if state["fetched_data"]:
                return ce.ToolOutcome(tool_result=ALREADY_FETCHED_NUDGE)
            try:
                result = get_slack_messages(**args)
            except Exception as e:
                err_str = str(e)
                if verbose:
                    print(f"  [dispatch] get_slack_messages raised: {e}", file=sys.stderr)
                if state["tool_failures"].get(name) == err_str:
                    if verbose:
                        print("  [dispatch] failed identically twice in a row — not retryable, stopping early", file=sys.stderr)
                    return ce.ToolOutcome(final={"error": err_str})
                state["tool_failures"][name] = err_str
                return ce.ToolOutcome(tool_result=json.dumps({"error": err_str}))
            state["fetched_data"] = True
            if verbose:
                print(f"  [dispatch] get_slack_messages({args}) — {len(result) if isinstance(result, list) else '?'} message(s)", file=sys.stderr)
            return ce.ToolOutcome(tool_result=json.dumps(result))

        if name == "search_knowledge_base":
            if first_turn:
                return ce.ToolOutcome(tool_result=FIRST_TURN_GATE_NUDGE)
            if state["fetched_data"]:
                return ce.ToolOutcome(tool_result=ALREADY_FETCHED_NUDGE_GENERIC)
            try:
                result = rag_index.search(args.get("query", ""), source=args.get("source"))
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

    # Exposed so run_agent can build a no-tool-call handler that knows
    # whether real data was actually fetched this turn — see
    # _make_on_no_tool_call's docstring.
    dispatch.state = state
    return dispatch


RENDER_DONT_NARRATE_NUDGE = (
    "You already fetched real data this turn but stopped with a plain-text reply instead of "
    "calling render_view — that's not allowed once real data is in hand (see the "
    "render_dont_narrate guidance above). Call render_view now with a real screen built from "
    "what you actually fetched. If part of the request doesn't fit cleanly, note that in the "
    "screen's 'meta' field, but still render whatever real findings you do have."
)


def _make_on_no_tool_call(state):
    """Builds run_agent's on_no_tool_call callback, closed over this call's own dispatch
    state (see _make_dispatch's `dispatch.state` above). See agent_gmail.py's copy of this
    function for the full reasoning — a plain-text reply is fine for real small talk, but not
    once state['fetched_data'] is True (a real fetch actually succeeded THIS turn), which
    would be the same 'narrate instead of render' bug found live in agent_unified.py
    2026-09-06 (reproduced there 5/5 in testing)."""
    def _on_no_tool_call(text, stop_reason):
        if text and state["fetched_data"]:
            return ce.ToolOutcome(tool_result=RENDER_DONT_NARRATE_NUDGE)
        if text:
            return ce.ToolOutcome(final={"text": text})
        return None
    return _on_no_tool_call


def run_agent(user_request=None, max_steps=8, verbose=True, messages=None, fetched_data=False, first_turn=False, extra_system=""):
    """Same calling convention as before and as every other connector — see this module's
    docstring and claude_engine.run_claude_agent's docstring for the full contract. first_turn:
    see agent_gmail.py's copy of this docstring for the full reasoning; defaults False so
    every existing caller is unaffected. extra_system: see agent_gmail.py's copy; defaults ""."""
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
        on_no_tool_call=_make_on_no_tool_call(dispatch.state),
        force_tool_choice=False,
    )


if __name__ == "__main__":
    from pathlib import Path
    import webbrowser
    from renderer import render_html

    query = sys.argv[1] if len(sys.argv) > 1 else "what's been happening in the channel?"
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
