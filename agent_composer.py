"""
agent_composer.py — the Composer connector, added 2026-08-26: the actual
DynamisOS mechanic ("compose a personalized interface from typed
primitives, bound to real data") built on primitives.py's registry instead
of one connector's hand-written, single-source TOOLS list.

Every other connector in this app (agent_gmail.py, agent_slack.py,
agent_github.py) can only ever call ITS OWN connector's tools — a Gmail
workflow can search Gmail (plus the cross-source knowledge base), never
Slack or GitHub directly in the same request. This connector's TOOLS list
is built dynamically from EVERY primitive in primitives.REGISTRY, so one
request can genuinely pull from more than one real source at once — e.g.
"anything about the Q2 budget, from email and Slack both" can call
gmail_messages AND slack_messages in the same turn, then compose ONE
render_view from the combined real results. That's the "same primitives,
personalized composition" idea dynamisos.in's site describes, applied to
data this app already has real access to, not a new data model.

A THIRD thing added the same day, after live testing exposed a real gap:
the composed layout originally didn't depend on WHO was asking — two
different users, same primitives, same request, got the identical screen.
That's agent-COMPOSED, not agent-CUSTOMIZED, and DynamisOS's own pitch is
specifically the latter (their example: the same underlying app renders as
a task list for one person, a calendar for another). See user_style.py and
_build_system's `style` param below — run_agent() now reads the calling
user's own stored style preference fresh on every call and folds it into
SYSTEM as a real constraint, not decoration. Two users with different
style notes, identical primitives, identical data, should now end up with
visibly different compositions.

Two ways a request reaches this connector, both handled by the exact same
dispatch loop below — there is no separate "canvas mode" code path:
  - Pure natural language (typed into the Composer's request box): the
    model picks whichever primitives it needs from the full registry.
  - Canvas-assisted (primitive_ids passed in from studio.py's /composer
    route, built by the person dragging/clicking primitive cards in the
    visual canvas): those ids are named explicitly in SYSTEM as REQUIRED,
    and render_view is rejected until each one has actually been called at
    least once — the person's explicit selection is enforced, not left to
    the model's discretion, while the model still decides the actual
    layout and may call additional primitives too if the request clearly
    needs them.

Everything downstream of "real data came back" is completely unchanged
from every other connector: schema.py's UI_SCHEMA, validation.py's
validate_view, guardrails.py's find_fabricated_content/find_fabricated_stats
— same functions, same guarantees, zero special-casing for the fact that
this connector's data can now come from more than one source in one
render.
"""

import json
import sys

from schema import UI_SCHEMA
from validation import validate_view
from guardrails import (
    find_fabricated_content,
    find_fabricated_stats,
    find_ungrounded_suggestion_facts,
    fabricated_content_nudge,
)
from primitives import REGISTRY
from users import get_user, DEFAULT_USER
import user_style
import claude_engine as ce

ASK_USER_TOOL = {
    "name": "ask_user",
    "description": (
        "Ask the person a single clarifying question before doing anything else. Use "
        "this ONLY when the request is genuinely ambiguous in a way that would change "
        "what you'd fetch. The question must NAME the specific ambiguity and offer "
        "concrete options. Do NOT use this for requests you can reasonably interpret "
        "yourself, and never ask about which primitives to use if some were already "
        "explicitly selected for you (see SYSTEM) — only ask about their actual content, "
        "e.g. a missing date range or an unclear name."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"question": {"type": "string", "description": "One short, specific question."}},
        "required": ["question"],
    },
}

RENDER_VIEW_TOOL = {
    "name": "render_view",
    "description": (
        "Emit the final generated screen for the person's request. Call this exactly "
        "once, as the last step, after fetching whatever real data you need. Include "
        "ONLY the components required to answer the request — no unrelated data, no "
        "default dashboard. If data came from more than one source, either merge it "
        "into one coherent view (e.g. one list with each row's badge naming its "
        "source) or use separate components per source, whichever actually reads "
        "clearly — your call."
    ),
    "input_schema": UI_SCHEMA,
}


def _find_placeholder_labels(view):
    """Same connector-local safety net every other connector's dispatch
    already carries (see agent_gmail.py's copy of this exact function for
    the full reasoning) — catches the model writing a field's own NAME as
    its VALUE ("Subject", "From", "Result 1", ...) instead of real fetched
    text, a single-word failure mode guardrails.py's generic check
    deliberately doesn't cover."""
    placeholder_words = {"title", "snippet", "source", "result", "url", "query", "match", "name", "note"}
    bad = []
    for comp in (view.get("components") or []):
        if not isinstance(comp, dict):
            continue
        for row in (comp.get("rows") or []):
            if not isinstance(row, dict):
                continue
            for key in ("name", "note"):
                val = row.get(key)
                if isinstance(val, str) and val.strip().lower() in placeholder_words:
                    bad.append(val)
    return bad


NO_DATA_YET_NUDGE = (
    "render_view was rejected: you haven't called any real data primitive yet, so you have "
    "no real data to show. Call at least one first, wait for its real result, then call "
    "render_view again using those real values."
)


def _already_called_nudge(name):
    return (
        f"{name} was not run again: you already have its real result earlier in this "
        "conversation. Use those exact real values now instead of fetching again."
    )


ALREADY_ASKED_NUDGE = (
    "ask_user was rejected: you already asked one clarifying question in this conversation "
    "and the person just answered it. Do not ask another — use their answer to fetch data "
    "now, then render_view."
)


# Added 2026-08-26 — same DynamisOS-comparison reasoning as every other connector's copy of
# this constant (see agent_gmail.py's for the full history), but this one covers a bit more
# ground: unlike a single-source connector chat, the Composer genuinely doesn't know which
# data source(s) are relevant unless the person used the canvas — so when NO primitives were
# explicitly selected, the bundled question also asks which source(s) to pull from, matching
# DynamisOS's own "which tools/apps should it connect to?" question. When primitives WERE
# selected via the canvas, that part is already answered, so the question only covers purpose
# and layout/style, same shape as every other connector's version.
FIRST_TURN_GATE_NUDGE = (
    "Not yet: this is the very first request of a brand-new conversation, so call ask_user "
    "first with one bundled question covering purpose, layout/style, and (if no primitives "
    "were already selected for you) which data source(s) to use — before calling any "
    "primitive or render_view. See the instructions for exactly what to cover."
)


def _build_system(primitive_ids, style=None, first_turn=False):
    """SYSTEM prompt, built fresh per call since it names the actually-
    registered primitives (so adding one to primitives.py is automatically
    reflected here, no second place to update), names exactly which ones
    the person explicitly selected via the visual canvas (when given), and
    — added 2026-08-26, the piece that makes this genuinely
    "agent-CUSTOMIZED" per person rather than just "agent-composed" — the
    calling user's own stored interface-style preference (user_style.py),
    when they've set one. Without `style`, two different users with the
    identical primitives and identical request get the identical
    composition; that's the exact gap called out live in this session
    (see user_style.py's module docstring). This is what closes it: the
    style instruction below isn't decorative wording, it's a real
    constraint the model has to weigh against the request, same weight as
    everything else in this prompt."""
    catalog = "\n".join(
        f"- {p['id']} ({p['label']}, connector={p['connector'] or 'cross-source'}): {p['description']}"
        for p in REGISTRY.all()
    )
    base = (
        "You are Pilant's Composer — an AI Copilot that builds a personalized interface by "
        "pulling real data from whatever combination of connected sources a request actually "
        "needs, then composing ONE screen from it. Unlike a single connector's chat, you can "
        "call primitives from MORE THAN ONE source in the same request when that's genuinely "
        "what the person asked for (e.g. \"anything about X, across email and Slack\").\n\n"
        f"Available primitives:\n{catalog}\n\n"
        "Call whichever primitive(s) actually answer the request — don't call ones that aren't "
        "relevant just because they exist. Prefer knowledge_base_search over a live fetch (e.g. "
        "gmail_messages) when the request needs older history, a broad/fuzzy topic, or spans "
        "sources at once; prefer the live fetch for a quick recent/exact lookup. After fetching, "
        "call render_view exactly once with a screen that shows ONLY what was asked for — no "
        "unrelated data, no default dashboard."
    )
    if primitive_ids:
        names = ", ".join(primitive_ids)
        base += (
            f"\n\nThe person explicitly selected these primitives via the visual canvas: {names}. "
            "You MUST call each of them at least once — using reasonable default arguments unless "
            "the request specifies otherwise — before calling render_view. You may also call other "
            "primitives beyond these if the request clearly needs them."
        )
    if style:
        base += (
            f"\n\nThis specific person has told you how THEY like their interface shaped: "
            f"\"{style}\". This is a real preference, not a suggestion — let it genuinely drive your "
            "composition choices (which component types you reach for, how you group/order/title "
            "things, how dense or spacious the screen is) whenever it doesn't conflict with what's "
            "actually being asked or with the real data available. Two different people asking for "
            "the same thing should end up with visibly different screens if their stated preferences "
            "differ — that's the point of asking for it. Never let a style preference cause you to "
            "omit real data the request actually needs, or to invent content just to match the style."
        )
    if first_turn:
        if primitive_ids:
            covers = (
                "(a) what they actually want to see — if the request is broad, offer 2-3 "
                "concrete interpretations as options; skip this if they were already "
                "specific — and (b) how they'd like it laid out (a compact list, a stat "
                "summary up top, or something more detailed)"
            )
        else:
            covers = (
                "(a) what they actually want to see — if the request is broad, offer 2-3 "
                "concrete interpretations as options; skip this if they were already "
                "specific — (b) which data source(s) to pull from (name the real options from "
                "the catalog above), and (c) how they'd like it laid out (a compact list, a "
                "stat summary up top, or something more detailed)"
            )
        base += (
            "\n\nOne more thing, for right now only: this is the very first request in a "
            "brand-new conversation, so before calling any primitive or render_view, call "
            f"ask_user ONE time with a single bundled question covering {covers}. Ask all of "
            "this in ONE message, never separate questions. This replaces the ambiguity-only "
            "ask_user rule above for this one first request; every later request in this same "
            "conversation goes back to that normal rule — never ask this bundled question "
            "again."
        )
    return base


def _make_dispatch(seed_fetched_data, verbose, user, primitive_ids, first_turn=False):
    """Builds a fresh dispatch() closure for ONE run_agent() call — same
    per-call-state pattern every other connector's _make_dispatch already
    uses (see agent_gmail.py's docstring on why: a plain callback can't
    mutate a caller's local by reference). first_turn: see agent_gmail.py's
    copy of this docstring for the full reasoning — code-enforced backstop
    for _build_system's first_turn paragraph."""
    state = {"fetched_data": seed_fetched_data, "tool_failures": {}, "called": set()}
    required = set(primitive_ids or [])

    def dispatch(name, args, tc_id, prior_messages, _seed_fetched_data_unused):
        primitive = REGISTRY.get(name)
        if primitive is not None:
            if first_turn:
                return ce.ToolOutcome(tool_result=FIRST_TURN_GATE_NUDGE)
            if name in state["called"]:
                return ce.ToolOutcome(tool_result=_already_called_nudge(name))
            try:
                result = REGISTRY.call(name, args, user=user)
            except Exception as e:
                err_str = str(e)
                if verbose:
                    print(f"  [dispatch] {name} raised: {e}", file=sys.stderr)
                if state["tool_failures"].get(name) == err_str:
                    return ce.ToolOutcome(final={"error": err_str})
                state["tool_failures"][name] = err_str
                return ce.ToolOutcome(tool_result=json.dumps({"error": err_str}))
            state["fetched_data"] = True
            state["called"].add(name)
            if verbose:
                n = len(result) if isinstance(result, list) else "?"
                print(f"  [dispatch] {name}({args}) — {n} item(s)", file=sys.stderr)
            return ce.ToolOutcome(tool_result=json.dumps(result))

        if name == "ask_user":
            if ce.ask_user_already_used(prior_messages):
                return ce.ToolOutcome(tool_result=ALREADY_ASKED_NUDGE)
            question = (args.get("question") or "").strip() or "Could you clarify what you're looking for?"
            return ce.ToolOutcome(
                final={"clarify": question, "messages": ce.NEEDS_CONVERSATION, "fetched_data": state["fetched_data"]},
                tool_result=ce.ASK_USER_ACCEPTED_SENTINEL,
            )

        if name == "render_view":
            if first_turn:
                return ce.ToolOutcome(tool_result=FIRST_TURN_GATE_NUDGE)
            if not state["fetched_data"]:
                return ce.ToolOutcome(tool_result=NO_DATA_YET_NUDGE)
            missing = required - state["called"]
            if missing:
                return ce.ToolOutcome(tool_result=(
                    "render_view was rejected: you haven't called these selected primitives "
                    f"yet: {sorted(missing)}. Call each of them first."
                ))

            problem = validate_view(args, UI_SCHEMA)
            if problem is not None:
                return ce.ToolOutcome(tool_result=problem)

            fabricated = (
                find_fabricated_content(args, prior_messages)
                + find_fabricated_stats(args, prior_messages)
                + find_ungrounded_suggestion_facts(args, prior_messages)
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


def run_agent(user_request=None, user=None, primitive_ids=None, max_steps=8, verbose=True,
              messages=None, fetched_data=False, first_turn=False):
    """Same calling convention as every other connector's run_agent() (see
    claude_engine.run_claude_agent's docstring for the full contract), plus
    one new optional kwarg: primitive_ids, the explicit canvas selection
    (a list of primitive id strings, or None for pure-NL requests).

    Added 2026-08-26: reads THIS user's stored style preference
    (user_style.get_style) fresh on every call — including a resume after
    ask_user, and every reopen of a saved composition — and folds it into
    SYSTEM via _build_system's `style` param, not once at save time. If the
    person updates their style between saving a composition and reopening
    it, the reopen reflects the NEW style, same "never a frozen snapshot"
    philosophy saved_views.py's own docstring describes for the data
    itself, just applied to style too.

    first_turn: added 2026-08-26, defaults False so every existing caller
    (tests, composer_open's saved-view regeneration) is unaffected —
    studio.py passes True only for a genuinely brand-new Composer request
    with no pending clarify already in progress (see studio.py's
    _COMPOSER_PENDING and the composer() route). See agent_gmail.py's copy
    of this docstring for the general reasoning."""
    user = user or get_user(DEFAULT_USER)
    tools = [REGISTRY.as_tool_schema(p["id"]) for p in REGISTRY.all()] + [ASK_USER_TOOL, RENDER_VIEW_TOOL]
    style = user_style.get_style(user["username"])
    system = _build_system(primitive_ids, style=style, first_turn=first_turn)
    dispatch = _make_dispatch(fetched_data, verbose, user, primitive_ids, first_turn)
    return ce.run_claude_agent(
        dispatch,
        system=system,
        tools=tools,
        max_steps=max_steps,
        verbose=verbose,
        messages=messages,
        user_request=user_request,
        fetched_data=fetched_data,
        force_tool_choice=True,
    )
