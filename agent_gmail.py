"""
Composition Engine wired to connectors_gmail.py — a REAL connector against
Google's Gmail API via OAuth2 (see that file's docstring for the
Client ID/Secret/refresh-token setup, and its notes on real Gmail search +
real unread status).

MIGRATED 2026-08-26 from NVIDIA's free-tier meta/llama-3.1-8b-instruct to
Claude (via the shared claude_engine.py — see that module's docstring for
the full "why"). This is the second connector migrated, after agent_custom.py
(the pilot) — the biggest of the six, and the first with a real fetch tool
and the fabrication guardrail actually wired to real external data instead
of nothing.

FABRICATION GUARDRAIL: originally added 2026-08-24 after the model, once
past the old provider's schema/JSON issues, was observed inventing entirely
fake content ("Message 1" / "This is the first message") instead of using
the real fetched inbox data. find_fabricated_content()/find_fabricated_stats()
(guardrails.py) still run on every render_view attempt, unchanged in spirit
— guardrails.extract_tool_results() was extended (2026-08-26, same day as
this migration) to also recognize Claude's tool_result content-block shape
alongside the old OpenAI role="tool" shape, so this guardrail keeps working
across both message formats while the rest of the fleet migrates.

What changed structurally, same story as agent_custom.py's port: the entire
JSON-repair stack (quote-escaping recovery, truncated-JSON closing,
missing-dict-closer insertion, surrogate/unicode-escape scrubbing), the
plain-text-tool-call recovery path, the "drop every tool_call past the
first" workaround, and the "looks_like_malformed_tool_call" text-sniffing
heuristic are all GONE — none of them apply to Claude's Messages API. What's
new here vs. agent_custom.py's port:
  - A real fetch tool (get_gmail_messages) whose "was data already fetched
    this call" state has to be tracked across steps — done via a closure
    local to each run_agent() invocation (_dispatch_for_this_call() below),
    since a plain dispatch() callback can't mutate a caller's local by
    reference. Also tracks per-tool failure state the same way, to still
    fail fast on a repeated deterministic error (bad .env config, an
    expired refresh token) instead of burning all max_steps retrying
    something that can never succeed.
  - Genuine conversational replies ("thanks!", "what can you do?") are a
    real, expected outcome here, unlike agent_custom.py — so this connector
    calls claude_engine.run_claude_agent(..., force_tool_choice=False),
    letting Claude decide for itself whether to call a tool or just answer
    in text, via the on_no_tool_call hook below.

No identity scoping here on purpose, same reasoning as agent_helpdesk.py —
this demo isn't re-proving the access-control story agent_github.py already
covers (also moot here: a Gmail refresh token is already scoped to exactly
one inbox, the one that authorized it).

run_agent()'s calling convention (fresh call vs. messages=/fetched_data=
resume-after-clarify) is UNCHANGED, so studio.py dispatches to this one
exactly the same generic way it dispatches to every other connector — see
studio.py's CONNECTORS registry and _run_agent_for_workflow(). studio.py
needed zero changes for this migration.

draft_reply_text() and translate_to_search_query() below are for
gmail_site.py's older, separate UI (single-shot completions, no tools, no
loop) — migrated to the same Claude client directly, no claude_engine.py
loop machinery needed since there's nothing to retry/dispatch.
"""

import json
import re
import sys

from schema import UI_SCHEMA
from connectors_gmail import get_gmail_messages
from validation import validate_view
from guardrails import find_fabricated_content, find_fabricated_stats, fabricated_content_nudge
from skills import load_skill
import rag_index
import claude_engine as ce

TOOLS = [
    {
        "name": "get_gmail_messages",
        "description": (
            "Fetch recent real messages from the connected Gmail inbox, most recent first. "
            "`query` is REAL Gmail search syntax — the same operators you'd type in the "
            "Gmail search bar, e.g. 'from:someone@example.com', 'subject:invoice', "
            "'newer_than:3d', 'has:attachment'. `folder` scopes to one of Gmail's real "
            "built-in views — 'inbox', 'sent', 'spam', 'drafts', 'trash', 'starred', "
            "'important' — and combines with `query`/`unread_only` rather than replacing "
            "them; for any OTHER (custom) label the account has, use "
            "query=\"label:<name>\" instead. `unread_only`, if true, restricts to "
            "genuinely unread messages. `limit` caps how many messages come back (default "
            "10, max 50)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Real Gmail search syntax, e.g. 'from:boss@company.com' or 'subject:invoice'.",
                },
                "folder": {
                    "type": "string",
                    "enum": ["inbox", "sent", "spam", "drafts", "trash", "starred", "important"],
                    "description": "One of Gmail's real built-in folders/views.",
                },
                "unread_only": {"type": "boolean"},
                "limit": {"type": "integer", "description": "How many messages to fetch."},
            },
        },
    },
    {
        # Added 2026-08-26 for rag_index.py's local knowledge base — the
        # user's own follow-up after asking what RAG would get them: search
        # across MORE history than a single get_gmail_messages call can
        # return (it caps at 50, no pagination), or a broader/fuzzier
        # question that isn't real Gmail search syntax. This shares the
        # SAME "one fetch per turn" gate get_gmail_messages uses below
        # (state["fetched_data"]) — deliberately not both in one turn, same
        # anti-thrashing reasoning as the rest of this file's dispatch loop.
        "name": "search_knowledge_base",
        "description": (
            "Semantic search over a pre-built local knowledge base indexed from Gmail (and "
            "Slack/GitHub, if also connected) — use this INSTEAD OF get_gmail_messages when "
            "the request needs older history beyond a normal recent-mail fetch, a broad or "
            "fuzzy topic search, or something that spans multiple connected sources at once, "
            "rather than a quick recent/exact-match lookup. `query` is a natural-language "
            "description of what to find, NOT Gmail search syntax. `source`, if given, "
            "restricts results to 'gmail' only — omit it to also search Slack/GitHub if "
            "connected. Returns an empty list if nobody has synced the knowledge base yet "
            "(the Integrations page has a 'Sync knowledge base' action) — that's not an "
            "error, just nothing indexed to search."
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
    "You are Pilant's Composition Engine, embedded as an AI Copilot connected to a real Gmail "
    "inbox. You're having a normal conversation with a person — some of what they say will be a "
    "real request to see email, and some of it will just be talk.\n\n"
    "If they're asking about their inbox — unread mail, mail from someone, mail about a topic, "
    "recent mail, and so on — that's a data request: call get_gmail_messages with whatever "
    "query/folder/unread_only/limit answers it (query takes real Gmail search syntax — from:, "
    "subject:, newer_than:, has:attachment, etc. — use it, don't just fetch everything and "
    "filter mentally), then call render_view exactly once with a screen that shows ONLY what "
    "they asked for — no unrelated messages, no default dashboard. Use judgment on badge tones "
    "if a message needs one (e.g. unread → tone 'warning'; something urgent-sounding in the "
    "subject/snippet → tone 'critical'; otherwise 'default'). Do not pad the screen with "
    "anything unrequested.\n\n"
    "get_gmail_messages only reaches recent mail (it caps at 50 messages, no pagination) and "
    "only understands real Gmail search syntax. If the request needs older history, a broad or "
    "fuzzy topic ('anything about the Q2 budget', 'that thread about the office move'), or "
    "something that might span Slack/GitHub too, call search_knowledge_base instead — it's "
    "semantic search over a separately-synced local index, not live Gmail. Call ONE of the two "
    "fetch tools per request, never both — pick whichever actually fits what's being asked.\n\n"
    "Gmail's built-in folders are real and available — if they ask about spam, sent mail, "
    "drafts, trash, starred, or important messages, pass the matching folder value "
    "('spam'/'sent'/'drafts'/'trash'/'starred'/'important') to get_gmail_messages rather than "
    "trying to simulate it with query text. For a custom label the account has that isn't one "
    "of those, use query=\"label:<name>\" instead. For the sent and drafts folders "
    "specifically, every message's 'from' is just the connected account's own address — show "
    "who each message was sent/addressed TO instead (the real 'to' field get_gmail_messages "
    "returns), not who it's from, since that's the actually useful column there.\n\n"
    "Component choice for multiple messages, this matters: when the request returns more than "
    "one message, put them ALL in a SINGLE 'list' component — one row per message (row 'name' "
    "= the subject or a short from+subject combo, row 'note' = the snippet or sender, badge for "
    "unread if useful) — never one 'panel' component per message. Reserve 'panel' for when there "
    "is exactly ONE message to show in detail, or for a handful of aggregate stats — not for "
    "enumerating a list of messages. Do not add a second 'panel' component next to the list just "
    "to restate a count — the list itself already shows every message; a redundant summary panel "
    "is exactly the kind of unrequested padding to avoid.\n\n"
    "Counts and badges must match the real data, every time, not just the first item: if you "
    "called get_gmail_messages with unread_only=True, EVERY message it returned is genuinely "
    "unread — give every row the same unread badge, not just the first one. And any number you "
    "write anywhere (a heading like '3 unread emails', a subtitle, a count) must be the actual "
    "number of items get_gmail_messages returned this conversation — count the real list, never "
    "reuse a number from earlier in the conversation or guess one that sounds plausible.\n\n"
    + load_skill("ui_composition") + "\n\n" +
    "If they're NOT asking for email data — a greeting, thanks, small talk, a question about "
    "what you can do, or anything else conversational — do not call any tool. Just reply "
    "normally in plain text, like a helpful, friendly assistant would. There's nothing to fetch "
    "or render for a message like that. Keep these replies short and natural, and when it fits, "
    "mention you can pull up real inbox data if they want to see something specific.\n\n"
    + load_skill("render_dont_narrate") + "\n\n" +
    "Only call ask_user first if a genuine data request is ambiguous in a way that would change "
    "what you'd fetch — and only once. After the person answers, proceed straight to "
    "get_gmail_messages and render_view; do not ask a second question in the same request, and "
    "do not ask about things you could reasonably infer yourself. A clarifying question must "
    "name the actual options — never just repeat the person's own request back as a question; "
    "that isn't clarifying anything.\n\n"
    "Critical: render_view's fields must contain real, literal data — the actual messages "
    "returned by get_gmail_messages (real senders, real subjects, real snippets). NEVER invent "
    "a sender or a message, and never write placeholder/template syntax such as "
    "{{get_gmail_messages(...)}}. This also means never writing the NAME of a field as its "
    "VALUE — a row's 'name'/'note', or a field's 'value', must be the actual sender/subject/"
    "snippet text itself, never the literal words 'Subject', 'Snippet', 'From', 'Sender', "
    "'Email 1', 'Email 2', or similar column-header-style placeholders standing in for it. If "
    "you're not looking at the real string get_gmail_messages returned, don't write it. It's "
    "normal and expected for a filtered query to come back with very few results, or none — "
    "render that honestly rather than padding it out. "
    "'components' must be an actual JSON array of component objects, not a string.\n\n"
    "Memory: if this conversation already includes a line like 'Built \"...\"' describing "
    "something you built earlier, that's a real screen you already showed this person, not a "
    "blank slate — a short follow-up like 'make it more compact', 'now add the sender', or "
    "'just show the unread ones' means adjust or narrow THAT, using it as context, not answer "
    "some unrelated interpretation of the words alone. Still call get_gmail_messages again to "
    "get current data (the inbox can change between requests), but keep the same intent in mind "
    "when deciding what to fetch and how to shape the screen."
)


# Added 2026-08-26 — DynamisOS asks a proactive round of clarifying
# questions (purpose / data / style / tools) before building anything, every
# time, even for a request that already sounds clear (per its own reference
# screenshots the user showed live, compared directly against this chat's
# static "Connected to Gmail. Try ..." greeting). SYSTEM above only ever
# asks ask_user REACTIVELY — only if the request itself is ambiguous —
# which is the right call for every request AFTER the first (this is a live
# inbox; re-asking "what's this for" on every follow-up would be
# exhausting), but it meant a brand-new conversation's very first request
# never got any DynamisOS-style up-front check-in at all.
# FIRST_TURN_CLARIFY_PARAGRAPH is appended to SYSTEM by _build_system()
# below ONLY for that one, first request of a fresh conversation — studio.py
# decides when that is (see _run_agent_for_workflow's first_turn kwarg) and
# passes first_turn=True only then, never on a resumed/follow-up call.
# Unlike DynamisOS's fixed 4-item numbered checklist, this is ONE bundled,
# options-based question: "which data source" is already answered by this
# being the Gmail chat specifically, so there's only purpose and
# layout/style left worth asking — cramming both into one ask_user call
# (this tool already caps at one question per conversation) keeps the same
# low-friction, single-question feel the rest of this file's ask_user usage
# already has, rather than adopting DynamisOS's longer format wholesale.
FIRST_TURN_CLARIFY_PARAGRAPH = (
    "\n\nOne more thing, for right now only: this is the very first request in a brand-new "
    "conversation, so before calling get_gmail_messages, search_knowledge_base, or "
    "render_view, call ask_user ONE time with a single bundled question covering both (a) "
    "what they actually want to see — if the request is broad ('show my inbox'), offer 2-3 "
    "concrete options tailored to it (e.g. 'everything unread, mail from someone specific, "
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
    """SYSTEM (module-level, above) is the normal, every-request prompt, kept as a plain
    string so anything that reads agent_gmail.SYSTEM directly still works unchanged. Pass
    first_turn=True only for a brand-new conversation's very first request — studio.py
    decides this, never the model — to also append FIRST_TURN_CLARIFY_PARAGRAPH.

    extra_system, added 2026-08-30: an optional, pre-built string studio.py passes in — a
    per-role usage hint from layout_usage.summarize_usage() (see that module's docstring).
    Appended last, after any first-turn paragraph, exactly the same "additive signal, never
    a silent override" posture user_style.py's preference instruction already takes in
    agent_composer.py's _build_system. Empty string (the default) changes nothing."""
    base = SYSTEM + FIRST_TURN_CLARIFY_PARAGRAPH if first_turn else SYSTEM
    return base + "\n\n" + extra_system if extra_system else base


# ---- Guardrails — all model-agnostic, ported unchanged from the NVIDIA version ----

_PLACEHOLDER_VALUES = {"subject", "snippet", "from", "sender", "date", "recipient", "to"}
_PLACEHOLDER_PATTERN = re.compile(r"^(email|message)\s*\d+$", re.IGNORECASE)


def _find_placeholder_labels(view):
    """Connector-local safety net, layered on top of guardrails.py's generic fabrication
    check: catches the model writing a field's own NAME as its VALUE (row name/note, or a
    field's value, literally "Subject", "From", "Email 1", ...) instead of the real fetched
    text. guardrails.py's check deliberately skips single-word values (to avoid false-flagging
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
    "render_view was rejected: you haven't called get_gmail_messages yet, so you have no real "
    "data to show. Call it first, wait for its real result, then call render_view again "
    "using those real values."
)

ALREADY_FETCHED_NUDGE = (
    "get_gmail_messages was not run again: you already have its real result earlier in this "
    "conversation. Use those exact real values now in a render_view call instead of fetching "
    "again."
)

# Shared between get_gmail_messages and search_knowledge_base (added
# 2026-08-26) — either one satisfies state["fetched_data"], and once either
# has run, calling EITHER again gets this same nudge, not just the one
# already used. Deliberately generic (doesn't name a specific tool) since
# it fires for whichever of the two gets called second.
ALREADY_FETCHED_NUDGE_GENERIC = (
    "No fetch tool was run again: you already have real fetched data earlier in this "
    "conversation (from get_gmail_messages or search_knowledge_base). Use those exact real "
    "values now in a render_view call instead of fetching again."
)

ALREADY_ASKED_NUDGE = (
    "ask_user was rejected: you already asked one clarifying question in this conversation "
    "and the person just answered it. Do not ask another question — use their answer to call "
    "get_gmail_messages now, then render_view."
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
    "get_gmail_messages with your best reasonable interpretation."
)


def _make_dispatch(seed_fetched_data, verbose, first_turn=False):
    """Builds a fresh dispatch() closure for ONE run_agent() call, with its own private
    fetched-data and tool-failure state — mirrors the old NVIDIA version's per-call local
    variables (`fetched_data`, `tool_failures` inside _run_agent_once), since a plain
    dispatch() callback can't mutate a caller's local by reference in Python, and each
    run_agent() call needs independent state anyway (a resume-after-clarify call seeds its
    state from the `fetched_data` the earlier call left off with).

    first_turn: code-enforced backstop for FIRST_TURN_CLARIFY_PARAGRAPH above — if the model
    ignores that instruction and tries to fetch/render anyway on a brand-new conversation's
    first request, every one of those attempts is rejected with FIRST_TURN_GATE_NUDGE instead
    of silently letting it through. A fixed closure value, not conversation state: an accepted
    ask_user call always ends the run immediately (see ToolOutcome's `final`), so this can
    never need to flip from True to False mid-call — it's True for the whole call or not at
    all, decided once by studio.py before run_agent() is even invoked."""
    state = {"fetched_data": seed_fetched_data, "tool_failures": {}}

    def dispatch(name, args, tc_id, prior_messages, _seed_fetched_data_unused):
        if name == "get_gmail_messages":
            if first_turn:
                return ce.ToolOutcome(tool_result=FIRST_TURN_GATE_NUDGE)
            if state["fetched_data"]:
                return ce.ToolOutcome(tool_result=ALREADY_FETCHED_NUDGE)
            try:
                result = get_gmail_messages(**args)
            except Exception as e:
                err_str = str(e)
                if verbose:
                    print(f"  [dispatch] get_gmail_messages raised: {e}", file=sys.stderr)
                if state["tool_failures"].get(name) == err_str:
                    # Same tool just failed with the exact same error it did last time —
                    # a deterministic failure (bad .env config, a revoked refresh token),
                    # not a transient one a retry could fix. Stop burning steps on it.
                    if verbose:
                        print("  [dispatch] failed identically twice in a row — not retryable, stopping early", file=sys.stderr)
                    return ce.ToolOutcome(final={"error": err_str})
                state["tool_failures"][name] = err_str
                return ce.ToolOutcome(tool_result=json.dumps({"error": err_str}))
            state["fetched_data"] = True
            # Remembered so render_view's own final result (below) can carry
            # exactly which real folder/query/unread_only this turn actually
            # fetched with — added 2026-08-29 so studio.py's embedded Gmail
            # panel can scope itself to the SAME real fetch a "Built it — see
            # the live preview on the right" message describes, instead of a
            # second, separately-guessed translation of the chat text that
            # could disagree with what was actually rendered. At most one
            # get_gmail_messages call succeeds per run (the "already fetched"
            # gate above), so there's never an ambiguous "which call" here.
            state["last_fetch_args"] = dict(args)
            if verbose:
                print(f"  [dispatch] get_gmail_messages({args}) — {len(result) if isinstance(result, list) else '?'} message(s)", file=sys.stderr)
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
            # fetch_args (added 2026-08-29): the real get_gmail_messages
            # kwargs this turn actually fetched with, when a call happened in
            # THIS run — studio.py uses this to scope the embedded Gmail
            # panel to the exact same real folder/query/unread_only this
            # render describes, instead of a second, separately-guessed
            # translation of the chat text that could disagree with what was
            # actually rendered. Usually set (state["fetched_data"] being
            # True almost always means get_gmail_messages just ran in this
            # same dispatch closure), but can legitimately be None: resuming
            # an ask_user clarification can seed fetched_data=True from an
            # EARLIER run's fetch (see run_agent's seed_fetched_data param),
            # in which case this fresh closure never saw that call itself.
            # studio.py treats a None fetch_args as "leave the panel's
            # current scoping alone" rather than resetting it, which is the
            # correct behavior for that case.
            return ce.ToolOutcome(final={"render": args, "fetch_args": state.get("last_fetch_args")})

        return ce.ToolOutcome(tool_result="unknown tool")

    return dispatch


def _on_no_tool_call(text, stop_reason):
    """A genuine plain-text reply is a normal, expected outcome for this connector (small
    talk, thanks, "what can you do?") — see SYSTEM's own instruction to reply in plain text
    for non-data-request messages, and force_tool_choice=False below which lets Claude
    actually choose to do this. Any non-empty text here IS the real answer; there is no
    NVIDIA-style ambiguity about whether it's a dodged tool call, since Claude's tool_use
    blocks are structurally distinct from plain text turns."""
    if text:
        return ce.ToolOutcome(final={"text": text})
    return None


def run_agent(user_request=None, max_steps=8, verbose=True, messages=None, fetched_data=False, first_turn=False, extra_system=""):
    """Same calling convention as before and as every other connector — see this module's
    docstring and claude_engine.run_claude_agent's docstring for the full contract.

    first_turn: added 2026-08-26, defaults False so every existing caller (tests, the CLI demo
    below) is unaffected. studio.py passes True only for a brand-new workflow's very first
    request (see _run_agent_for_workflow) — see FIRST_TURN_CLARIFY_PARAGRAPH/_build_system
    above for what it actually changes.

    extra_system: added 2026-08-30, defaults "" so every existing caller is unaffected — see
    _build_system's docstring above for what studio.py passes here."""
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
        on_no_tool_call=_on_no_tool_call,
        force_tool_choice=False,
    )


def draft_reply_text(original, sender_name, instruction=None, verbose=True):
    """
    For gmail_site.py's reply interface — a single, plain completion (no tools, no schema, no
    multi-step loop) that writes a suggested reply body to one real fetched email. This is only
    ever a suggested starting point — gmail_site.py puts the result in an editable textarea and
    never sends it without a person confirming first (see connectors_gmail.send_reply's
    docstring).

    `original` is a get_message_full() result — real fetched data, so there's nothing here to
    fabricate content ABOUT (unlike render_view, there's no separate guardrail on this output: a
    bad draft is just a bad first draft the person edits or discards).

    Returns "" (never raises) on any model failure, so a draft failure degrades to an empty
    textarea instead of blocking the reply feature.
    """
    prompt = (
        f"Write a short, polite reply to this email, on behalf of {sender_name}.\n\n"
        f"From: {original.get('from', '')}\n"
        f"Subject: {original.get('subject', '')}\n\n"
        f"{(original.get('body') or '')[:4000]}\n\n"
    )
    if instruction:
        prompt += f"Specific instruction for this reply: {instruction}\n\n"
    prompt += (
        "Write ONLY the reply body text — no subject line, no headers, no signature block, "
        "just the message itself. Keep it brief and natural."
    )
    try:
        resp = ce.client.messages.create(
            model=ce.DEFAULT_MODEL,
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in resp.content if b.type == "text").strip()
    except Exception as e:
        if verbose:
            print(f"  [draft_reply_text] model call failed: {type(e).__name__}: {e}", file=sys.stderr)
        return ""


def translate_to_search_query(text, verbose=True):
    """
    For gmail_site.py's single merged search box — one single-shot completion, no tools, no
    schema, no loop: given whatever a person typed into the search box, returns real Gmail
    search syntax (or "" on failure), which gmail_site.py then hands to get_gmail_messages()
    the same deterministic way a typed `from:` query already works. There's nothing here to
    fabricate — the output is a search query, not content shown as if it were real fetched
    data — so this doesn't need a guardrails.py-style grounding check.

    Returns "" (never raises) on any model failure — gmail_site.py's caller falls back to
    treating the raw typed text as a plain keyword search.
    """
    prompt = (
        "Translate this request into REAL Gmail search syntax — the exact operators typed into "
        "Gmail's own search bar (from:, to:, subject:, is:unread, is:read, has:attachment, "
        "label:, newer_than:Nd, older_than:Nd, after:YYYY/MM/DD, before:YYYY/MM/DD, in:, cc:, "
        "bcc:, filename:). Combine multiple operators with spaces when the request needs more "
        "than one (e.g. \"unread from paypal this week\" -> \"is:unread from:paypal newer_than:7d\").\n\n"
        f"Request: {text}\n\n"
        "Reply with ONLY the search query itself — no explanation, no quotes around it, no "
        "leading/trailing text. If the request has no real search-narrowing content at all (pure "
        "small talk, not about email), reply with exactly: NONE"
    )
    try:
        resp = ce.client.messages.create(
            model=ce.DEFAULT_MODEL,
            max_tokens=60,
            messages=[{"role": "user", "content": prompt}],
        )
        out = "".join(b.text for b in resp.content if b.type == "text").strip()
        if len(out) >= 2 and out[0] == out[-1] and out[0] in "\"'":
            out = out[1:-1].strip()
        if not out or out.upper() == "NONE":
            return ""
        return out
    except Exception as e:
        if verbose:
            print(f"  [translate_to_search_query] model call failed: {type(e).__name__}: {e}", file=sys.stderr)
        return ""


if __name__ == "__main__":
    from pathlib import Path
    import webbrowser
    from renderer import render_html

    query = sys.argv[1] if len(sys.argv) > 1 else "what unread emails do I have?"
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
