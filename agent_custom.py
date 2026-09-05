"""
Composition Engine for Pilant Studio's "build your own interface" mode —
added 2026-08-25 after the user showed a DynamisOS reference screenshot:
typing something as short as "clothing brand" into a brand-new workflow
should have the agent ask a short round of clarifying questions (purpose,
what data to show, style, which tools/apps it should connect to) and then
build a full custom page from the answers — for ANY app or brand the person
describes, not just the four real, already-wired connectors (Gmail, Slack,
GitHub, Helpdesk).

MIGRATED 2026-08-26 from NVIDIA's free-tier meta/llama-3.1-8b-instruct to
Claude (via the shared claude_engine.py — see that module's docstring for
the full "why" and what got deleted in the process). This was the pilot
connector for that migration: smallest of the six connectors carrying the
old reliability layer, no external fetch tool to worry about, and the one
with the freshest test coverage to migrate against. The original NVIDIA
version is kept at _nvidia_backup/agent_custom.py for reference.

What changed structurally: the entire JSON-repair stack (quote-escaping
recovery, truncated-JSON closing, missing-dict-closer insertion, surrogate/
unicode-escape scrubbing), the plain-text-tool-call recovery path
(_parse_tool_calls_from_text and friends), and the "only keep the first of
a multi-tool-call turn" workaround are all GONE — none of them apply to
Claude's Messages API, where a tool_use block's `.input` arrives as an
already-parsed, schema-validated dict, never a raw string to repair, and
multiple tool_use blocks in one turn are handled natively. What's still
here, unchanged in spirit, is every guardrail that was never actually about
NVIDIA/llama's unreliability specifically:
  - _request_too_vague_for_direct_render() — code-enforced "ask before you
    build" gate for a terse request, independent of model quality.
  - _find_placeholder_labels() — generic denylist catching a schema field's
    own name written as its value ("Label", "Item 1", "Lorem ipsum").
  - _is_lazy_clarifying_question() — catches a question that just echoes
    the person's own request instead of narrowing it.
  - _find_unfilled_placeholder() — catches the model copying ask_user's own
    example template verbatim (angle brackets and all) instead of writing
    real content; found live 2026-08-25, kept here as cheap insurance even
    though a stronger model is far less likely to need this crutch.
  - The "ask at most once" cap, now backed by claude_engine.ask_user_already_used()
    (ported from this file's own 2026-08-25 fix — only counts an ACTUALLY
    ACCEPTED ask_user call, not a rejected attempt, so a legitimate retry
    after a rejection is never wrongly blocked).

This remains deliberately NOT a fifth real data connector — no backend, no
OAuth, no fetch tool. Per the user's explicit choice, this mode builds with
realistic, invented SAMPLE data appropriate to whatever the person
described, the same way the DynamisOS reference demo does. No
guardrails.find_fabricated_content/find_fabricated_stats calls here for the
same reason as before — those check render_view's content against real
tool results, and this connector never produces any.

run_agent()'s calling convention (fresh call vs. messages=/fetched_data=
resume-after-clarify) is UNCHANGED from before and from every other
connector, so studio.py dispatches to this one exactly the same generic way
it dispatches to the other four — see studio.py's CONNECTORS registry and
_run_agent_for_workflow(). studio.py needed zero changes for this
migration.
"""

import re

from schema import UI_SCHEMA
from validation import validate_view
import claude_engine as ce

TOOLS = [
    {
        "name": "ask_user",
        "description": (
            "Ask the person ONE consolidated clarifying question before building anything, "
            "when their request is short or vague — e.g. just a business/app name or type "
            "with no detail, like 'clothing brand' or 'fitness app'. The single question "
            "should cover, together: (1) what this interface is mainly for, (2) what data "
            "or content it should show, (3) any style/layout preference, and (4) which "
            "tools or services it should appear to pull data from (Instagram, Google "
            "Sheets, Shopify, Mailchimp, or anything else — simulated/sample data is fine, "
            "there is no real backend).\n\n"
            "FORMAT the question string itself as markdown, exactly like this shape (the "
            "chat UI renders **bold** and numbered '1. '/'2. '/'3. '/'4. ' lines as real "
            "formatting — write it literally this way, not as one flat unformatted "
            "paragraph):\n\n"
            "\"I'd love to help you build an interface for a <what they described>! To make "
            "sure I build exactly what you need, could you clarify a few things:\\n\\n"
            "1. **What's the main purpose?** <two example options tailored to what they "
            "described>, or something else?\\n"
            "2. **What data should it show?** I can use realistic sample data, or you can "
            "describe what you have in mind.\\n"
            "3. **Any style or layout preference?** Minimalist, bold, grid-based, or "
            "something else?\\n"
            "4. **Which tools/apps should it appear to connect to?** <2-3 example tools "
            "that fit the domain they described> — simulated is fine.\\n\\n"
            "Once I have these, I'll build it for you!\"\n\n"
            "The angle-bracket parts above (<...>) are placeholders describing what to write "
            "in your OWN words — never copy them into your actual question literally. Tailor "
            "the opening sentence and each item's examples to what the person actually "
            "described (don't reuse 'clothing brand' unless that's what they said). Ask AT "
            "MOST ONE question per request: after the person answers, build directly from "
            "that answer, do not ask again. If the request already gives you enough to work "
            "with (a clear purpose and a sense of what it should show), skip ask_user "
            "entirely and call render_view directly."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "One consolidated, specific question."}
            },
            "required": ["question"],
        },
    },
    {
        "name": "render_view",
        "description": (
            "Emit the final generated screen for the interface the person described. Call "
            "this exactly once, as the last step, after either the original request already "
            "had enough detail or after their answer to your one clarifying question. There "
            "is no real backend — invent realistic, well-crafted SAMPLE data appropriate to "
            "what they described (real-sounding product names, prices, dates, values — "
            "internally consistent with each other), never a literal placeholder like "
            "'Item 1', 'Label', or 'Lorem ipsum'. Include ONLY the components that make "
            "sense for what was asked — no unrelated padding."
        ),
        "input_schema": UI_SCHEMA,
    },
]

SYSTEM = (
    "You are the Composition Engine inside Pilant Studio's 'build your own interface' mode. A "
    "person has described an app, brand, or workflow they want a live interface for — there is "
    "no real connected data source here, unlike Pilant's other modes (Gmail, Slack, GitHub, "
    "Helpdesk), which do have real backends. Your job is closer to a product designer building "
    "a realistic working mockup than a data-fetching agent: you invent plausible, well-crafted "
    "sample content appropriate to what was described, and build a screen from it using the "
    "fixed component schema (stat_grid, panel, list).\n\n"
    "If the request is short or vague — just a business/app name or type with no detail, like "
    "'clothing brand' or 'fitness app' — call ask_user ONCE with a single consolidated question "
    "covering: what the interface is mainly for, what data/content it should show, any style or "
    "layout preference, and which tools/services it should appear to connect to (Instagram, "
    "Google Sheets, Shopify, Mailchimp, or anything else — simulated is fine). Write that "
    "question as markdown — a short opening sentence, then a numbered '1.'/'2.'/'3.'/'4.' list "
    "with a **bold** short phrase starting each item — following the exact template in "
    "ask_user's own tool description, with every placeholder replaced by your own real words; "
    "the chat UI actually renders that formatting. If the request already has enough detail to "
    "work with, skip straight to render_view — do not ask a question just to ask one.\n\n"
    "Once you have enough to go on, call render_view exactly once. Populate it with realistic, "
    "internally-consistent invented sample data — real-sounding names, prices, dates, statuses — "
    "matched to what was described (a clothing brand gets products/collections, a fitness app "
    "gets workouts/progress stats, a support tool gets tickets, and so on). Never write a "
    "schema field's own name as its value (the literal words 'Label', 'Value', 'Name', 'Item 1', "
    "'Lorem ipsum', or similar placeholder/template text) — write the actual invented content "
    "itself. 'components' must be an actual JSON array of component objects, not a string.\n\n"
    "Memory: if this conversation already includes a line like 'Built \"...\"' describing "
    "something you built earlier, that's the real interface you already showed this person, not "
    "a blank slate — a short follow-up like 'make it more compact', 'now add a filter for size', "
    "or 'change the style to something bolder' means adjust or extend THAT interface, using it "
    "as context and staying consistent with what you already invented (the same brand/product "
    "names, the same tone), not build something unrelated from scratch. If a follow-up clearly "
    "changes the subject entirely (a different business, a different kind of app), treat it as a "
    "new request instead and, if it's too short/vague on its own to build from, ask_user again."
)


# ---- Guardrails — all model-agnostic, ported unchanged from the NVIDIA version ----

# Generic, domain-agnostic denylist (unlike Gmail's sender/subject or Helpdesk's
# status/priority ones) — catches the model writing a schema field's own NAME as its
# VALUE even though the values themselves are expected to be invented sample content.
_PLACEHOLDER_VALUES = {
    "name", "title", "label", "value", "text", "description", "field", "note",
    "item", "data", "placeholder", "lorem ipsum", "sample", "sample text",
    "your text here", "tbd", "todo",
}
_PLACEHOLDER_PATTERN = re.compile(r"^(item|row|field|entry|option|product|task)\s*\d+$", re.IGNORECASE)


def _find_placeholder_labels(view):
    """Connector-local safety net — same shape as every other connector's copy (Gmail's
    sender/subject denylist, Helpdesk's status/priority one), adapted to be domain-agnostic
    since this connector isn't scoped to one kind of data."""
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


_MIN_WORDS_FOR_DIRECT_RENDER = 6

NEEDS_CLARIFY_NUDGE = (
    "render_view was rejected: the request is too short/vague to build a specific interface "
    "from directly. Call ask_user ONCE with a single consolidated question covering: (1) what "
    "this interface is mainly for, (2) what data or content it should show, (3) any style or "
    "layout preference, and (4) which tools/services it should appear to pull data from "
    "(simulated is fine — there is no real backend). Only call render_view after that's "
    "answered, or if a later request already gives you enough detail on its own."
)

ALREADY_ASKED_NUDGE = (
    "ask_user was rejected: you already asked one clarifying question in this conversation "
    "and the person just answered it. Do not ask another question — use their answer to call "
    "render_view now."
)


def _request_too_vague_for_direct_render(messages):
    """Code-enforced gate — only blocks render_view before ask_user has been used once; after
    a clarifying round happens, render_view is always allowed through to this check regardless
    of how short the ORIGINAL request was."""
    original = ce.original_user_request(messages)
    return len(original.split()) < _MIN_WORDS_FOR_DIRECT_RENDER


def _is_lazy_clarifying_question(question, user_request):
    """A genuine clarifying question narrows the request; this catches one that just echoes
    it back instead."""
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
    "of narrowing it — that doesn't clarify anything. A genuine clarifying question names the "
    "specific things you need to know (purpose, what data to show, style, which tools it should "
    "connect to) — ask one real consolidated question covering those, or skip ask_user entirely "
    "if you already have enough to build from."
)


# Guardrail found live 2026-08-25: the model sometimes doesn't fill in ask_user's own
# tool-description EXAMPLE template — it copies the shape almost verbatim, angle brackets and
# all, e.g. asking "What's the main purpose? <what you described>, or something else?" instead
# of ever substituting real words. Kept here even post-migration as cheap insurance — a
# stronger model is far less likely to need this crutch, but the check costs nothing to keep.
_UNFILLED_PLACEHOLDER_RE = re.compile(r"<[^<>]{2,100}>")


def _find_unfilled_placeholder(question):
    """Returns the literal '<...>' span(s) still present in a clarifying question, if any —
    evidence the model copied ask_user's own example template instead of writing real,
    filled-in content. Empty list means the question looks clean."""
    return _UNFILLED_PLACEHOLDER_RE.findall(question or "")


TEMPLATE_PLACEHOLDER_NUDGE = (
    "ask_user was rejected: the question still contains literal, unfilled placeholder text "
    "like '<...>' copied straight from ask_user's own tool description — that bracketed text "
    "is an example SHAPE to follow, not something to paste into your actual question. Rewrite "
    "the entire question with every '<...>' replaced by your own real words tailored to what "
    "the person described (a real purpose, real example tools/apps, etc.) — no angle brackets "
    "anywhere in the final text."
)


def _dispatch(name, args, tc_id, prior_messages, fetched_data):
    """Per-tool handling for this connector — see claude_engine.run_claude_agent's docstring
    for the dispatch() contract. `args` is block.input, already a parsed dict matching the
    schema (Claude guarantees this; there is nothing to JSON-parse or repair here, unlike the
    old NVIDIA version)."""
    if name == "ask_user":
        if ce.ask_user_already_used(prior_messages):
            return ce.ToolOutcome(tool_result=ALREADY_ASKED_NUDGE)

        question = (args.get("question") or "").strip() or "Could you tell me more about what you'd like this interface to do?"
        original_request = ce.original_user_request(prior_messages)

        if _is_lazy_clarifying_question(question, original_request):
            return ce.ToolOutcome(tool_result=LAZY_CLARIFY_NUDGE)

        unfilled = _find_unfilled_placeholder(question)
        if unfilled:
            return ce.ToolOutcome(tool_result=TEMPLATE_PLACEHOLDER_NUDGE)

        return ce.ToolOutcome(
            final={"clarify": question, "messages": ce.NEEDS_CONVERSATION, "fetched_data": fetched_data},
            tool_result=ce.ASK_USER_ACCEPTED_SENTINEL,
        )

    if name == "render_view":
        if not ce.ask_user_already_used(prior_messages) and _request_too_vague_for_direct_render(prior_messages):
            return ce.ToolOutcome(tool_result=NEEDS_CLARIFY_NUDGE)

        problem = validate_view(args, UI_SCHEMA)
        if problem is not None:
            return ce.ToolOutcome(tool_result=problem)

        placeholder = _find_placeholder_labels(args)
        if placeholder:
            return ce.ToolOutcome(tool_result=(
                "render_view was rejected: it contains placeholder/template-looking "
                f"text instead of real invented content — {', '.join(repr(p) for p in placeholder)}. "
                "Write actual, specific sample content, not a field's own name as its value."
            ))

        return ce.ToolOutcome(final={"render": args})

    # No other tool exists in this connector's TOOLS list — dead in practice, kept only as a
    # harmless safety net matching every other connector's shape.
    return ce.ToolOutcome(tool_result="unknown tool")


def run_agent(user_request=None, max_steps=8, verbose=True, messages=None, fetched_data=False, extra_system=""):
    """Same calling convention as before and as every other connector — see this module's
    docstring and claude_engine.run_claude_agent's docstring for the full contract.

    extra_system: added 2026-08-30, defaults "" so every existing caller is unaffected — see
    agent_gmail.py's _build_system docstring for what studio.py passes here (a per-role usage
    hint from layout_usage.py)."""
    system = SYSTEM + "\n\n" + extra_system if extra_system else SYSTEM
    return ce.run_claude_agent(
        _dispatch,
        system=system,
        tools=TOOLS,
        max_steps=max_steps,
        verbose=verbose,
        messages=messages,
        user_request=user_request,
        fetched_data=fetched_data,
    )


if __name__ == "__main__":
    import json
    import sys
    import webbrowser
    from pathlib import Path
    from renderer import render_html

    query = sys.argv[1] if len(sys.argv) > 1 else "clothing brand"
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
