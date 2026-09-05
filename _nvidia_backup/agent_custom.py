"""
Composition Engine for Pilant Studio's "build your own interface" mode —
added 2026-08-25 after the user showed a DynamisOS reference screenshot:
typing something as short as "clothing brand" into a brand-new workflow
should have the agent ask a short round of clarifying questions (purpose,
what data to show, style, which tools/apps it should connect to) and then
build a full custom page from the answers — for ANY app or brand the person
describes, not just the four real, already-wired connectors (Gmail, Slack,
GitHub, Helpdesk).

This is deliberately NOT a fifth real data connector. There is no backend,
no OAuth, no fetch tool here at all — per the user's explicit choice, this
mode builds with realistic, invented SAMPLE data appropriate to whatever the
person described (a clothing brand's product catalog, a fitness app's
workout log, whatever), the same way the DynamisOS reference demo itself
clearly does. That is the one deliberate, structural difference from the
other four connectors' reliability layer this file otherwise ports
wholesale (same JSON-repair stack, same multi-tool-call fix, same
lazy-clarify-question detection, same single-tool-per-turn discipline):
  - No get_*_data tool, no TOOL_FUNCTIONS, no fetched_data gate on
    render_view — there's nothing to fetch.
  - No guardrails.find_fabricated_content / find_fabricated_stats calls —
    those check render_view's content against real tool results, and this
    connector never produces any (they'd be silent no-ops here anyway,
    since an empty record pool makes every content_is_grounded() check
    trivially pass — but importing and calling them would be misleading,
    so they're left out entirely).
  - A NEW code-enforced gate instead: _request_too_vague_for_direct_render()
    below requires at least one ask_user round before render_view when the
    person's original request was only a few words (a bare business/app
    name, no detail) — mirroring DynamisOS's own behavior of always asking
    first for something that terse. Same "don't just trust the system
    prompt" philosophy as NO_DATA_YET_NUDGE/ALREADY_ASKED_NUDGE elsewhere
    in this codebase: this small hosted model has not earned the benefit of
    the doubt on discipline it isn't code-enforced to keep.
  - _find_placeholder_labels() here is a generic, domain-agnostic denylist
    (not Gmail's sender/subject or Helpdesk's status/priority) — it still
    catches the model writing a schema field's own name as its value
    ("Label", "Item 1", "Lorem ipsum") even though the VALUES themselves are
    expected to be invented.

run_agent()'s calling convention (fresh call vs. messages=/fetched_data=
resume-after-clarify) is unchanged from every other connector, so studio.py
can dispatch to this one exactly the same generic way it dispatches to the
other four — see studio.py's CONNECTORS registry and _run_agent_for_workflow().
"""

import ast
import os
import json
import re
import sys
import time
import webbrowser
from pathlib import Path

from openai import OpenAI

from schema import UI_SCHEMA
from validation import validate_view
from renderer import render_html

env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=os.environ["NVIDIA_API_KEY"],
    timeout=45.0,
    max_retries=0,
)

MODEL = "meta/llama-3.1-8b-instruct"

TOOLS = [
    {
        "type": "function",
        "function": {
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
                "Tailor the opening sentence and each item's examples to what the person "
                "actually described (don't reuse 'clothing brand' unless that's what they "
                "said) — the shape above is a template, not literal text to copy. Ask AT MOST "
                "ONE question per request: after the person answers, build directly from that "
                "answer, do not ask again. If the request already gives you enough to work "
                "with (a clear purpose and a sense of what it should show), skip ask_user "
                "entirely and call render_view directly."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "One consolidated, specific question."}
                },
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
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
            "parameters": UI_SCHEMA,
        },
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
    "ask_user's own tool description; the chat UI actually renders that formatting. If the "
    "request already has enough detail to work with, skip straight to render_view — do not ask "
    "a question just to ask one.\n\n"
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

TOOL_FUNCTIONS = {}


_LONE_SURROGATE_RE = re.compile("[\ud800-\udfff]")


def _scrub_surrogates(s):
    """Strips lone/unpaired surrogate codepoints — see agent_gmail.py's copy of this function
    for the full story (found live 2026-08-25). Ported verbatim, domain-agnostic."""
    return _LONE_SURROGATE_RE.sub("", s)


_LITERAL_UNICODE_ESCAPE_RE = re.compile(r"\\u([0-9a-fA-F]{4})")


def _unescape_literal_unicode_escapes(s):
    """See agent_gmail.py's copy (round 7, 2026-08-25) for the full explanation — fixes a raw,
    literal six-character '\\uXXXX' sequence surviving as content instead of decoding to the
    real character it should represent. Ported verbatim."""
    return _LITERAL_UNICODE_ESCAPE_RE.sub(lambda m: chr(int(m.group(1), 16)), s)


_STRUCTURAL_QUOTE_RE = re.compile(r"(?<!\w)'|'(?!\w)")


def _repair_blanket_quoted_json(s):
    """See agent_gmail.py's copy for the full reasoning — promotes every quote NOT flanked by a
    word character on both sides to a real JSON double-quote, leaving genuine apostrophes alone.
    Ported verbatim."""
    return _STRUCTURAL_QUOTE_RE.sub('"', s)


def _close_truncated_json(s):
    """See agent_gmail.py's copy (round 4, 2026-08-25) for the full story — closes whatever
    brackets/braces are still open at the point a truncated payload just stops. Ported verbatim."""
    stack = []
    in_string = False
    escaped = False
    for ch in s:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if not stack:
                return None
            expected = "}" if stack[-1] == "{" else "]"
            if ch != expected:
                return None
            stack.pop()
    if not stack and not in_string:
        return None
    closer = '"' if in_string else ""
    closer += "".join("}" if c == "{" else "]" for c in reversed(stack))
    return s + closer


def _insert_missing_dict_closers(s):
    """See agent_gmail.py's copy (round 8, 2026-08-25) for the full reasoning and real-world
    reproduction — recovers a payload where a dict inside a list is missing its own closing '}'
    before the next dict's '{' begins. Ported verbatim."""
    out = []
    stack = []
    in_string = False
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if in_string:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(s[i + 1])
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == "{" or ch == "[":
            if ch == "{" and stack and stack[-1] == "{":
                j = len(out) - 1
                while j >= 0 and out[j] in (" ", "\t", "\n", "\r"):
                    j -= 1
                if j >= 0 and out[j] == ",":
                    out.insert(j, "}")
                    stack.pop()
            stack.append(ch)
            out.append(ch)
            i += 1
            continue
        if ch == "}" or ch == "]":
            if stack:
                stack.pop()
            out.append(ch)
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _repair_candidates(s):
    """Every heuristic-repaired variant worth trying with json.loads, in order. Ported verbatim
    from agent_gmail.py / agent_helpdesk.py — see those files for the full reasoning on repair
    ordering."""
    closed = _close_truncated_json(s)
    repaired = _repair_blanket_quoted_json(s)
    repaired_then_closed = _close_truncated_json(repaired) if repaired else None
    dict_closed = _insert_missing_dict_closers(repaired) if repaired else None
    dict_closed_then_closed = _close_truncated_json(dict_closed) if dict_closed else None
    return [c for c in (
        closed,
        repaired,
        _repair_blanket_quoted_json(closed) if closed else None,
        repaired_then_closed,
        dict_closed,
        dict_closed_then_closed,
    ) if c is not None]


def normalize_args(obj):
    """Same recovery as every other connector — see agent_gmail.py's copy for the full
    explanation. Ported verbatim (domain-agnostic)."""
    if isinstance(obj, str):
        s = obj.strip()
        if s.lower() == "true":
            return True
        if s.lower() == "false":
            return False
        if s[:1] in "[{":
            try:
                return normalize_args(json.loads(s))
            except (json.JSONDecodeError, ValueError):
                pass
            try:
                return normalize_args(ast.literal_eval(s))
            except (ValueError, SyntaxError):
                pass
            for candidate in _repair_candidates(s):
                try:
                    return normalize_args(json.loads(candidate))
                except (json.JSONDecodeError, ValueError):
                    continue
            return _scrub_surrogates(_unescape_literal_unicode_escapes(obj))
        return _scrub_surrogates(_unescape_literal_unicode_escapes(obj))
    if isinstance(obj, list):
        return [normalize_args(v) for v in obj]
    if isinstance(obj, dict):
        return {k: normalize_args(v) for k, v in obj.items()}
    return obj


def _render_summary(view):
    """One-line shape summary of an ACCEPTED render_view call. Ported verbatim."""
    if not isinstance(view, dict):
        return f"(unexpected render shape: {type(view).__name__})"
    heading = view.get("heading")
    comps = view.get("components")
    if not isinstance(comps, list):
        return f"heading={heading!r}, components={type(comps).__name__} (not a list)"
    parts = []
    for c in comps:
        if isinstance(c, dict) and c.get("type") == "list":
            parts.append(f"list×{len(c.get('rows', []))}")
        elif isinstance(c, dict):
            parts.append(str(c.get("type", "?")))
        else:
            parts.append("?")
    return f"heading={heading!r}, {len(comps)} component(s): {', '.join(parts) or '(none)'}"


def _json_or_literal(s):
    """Ported verbatim from agent_gmail.py / agent_helpdesk.py."""
    if not isinstance(s, str):
        return s
    s2 = s.strip()
    try:
        return json.loads(s2)
    except (json.JSONDecodeError, ValueError):
        pass
    try:
        return ast.literal_eval(s2)
    except (ValueError, SyntaxError):
        pass
    for candidate in _repair_candidates(s2):
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
    return s


TOOL_NAMES = {"ask_user", "render_view"}


def _parse_tool_calls_from_text(reply, verbose=False):
    """Recovers tool call(s) written as plain assistant TEXT instead of real function-calling.
    Ported verbatim from agent_gmail.py / agent_helpdesk.py — see those files' copies for the
    full explanation; only TOOL_NAMES differs (no data-fetch tool exists here)."""
    text = (reply or "").strip()
    if not text:
        return []

    parsed = _json_or_literal(text)
    if isinstance(parsed, dict) and isinstance(parsed.get("name"), str) and "parameters" in parsed:
        name = parsed["name"]
        params = parsed["parameters"]
        if isinstance(params, str):
            params = _json_or_literal(params)
        if name in TOOL_NAMES and isinstance(params, dict):
            return [(name, normalize_args(params))]
        return []

    try:
        tree = ast.parse(text, mode="exec")
    except SyntaxError as e:
        if verbose:
            print(f"  [_parse_tool_calls_from_text] ast.parse(mode='exec') failed: {e}", file=sys.stderr)
            print(f"  [_parse_tool_calls_from_text] full text was: {text!r}", file=sys.stderr)
        return []

    calls = []
    for stmt in tree.body:
        node = stmt.value if isinstance(stmt, ast.Expr) else None
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None)
        if name not in TOOL_NAMES:
            continue
        try:
            args = {kw.arg: ast.literal_eval(kw.value) for kw in node.keywords if kw.arg}
        except (ValueError, SyntaxError):
            continue
        calls.append((name, normalize_args(args)))
    return calls


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


NO_TOOL_CALL_NUDGE = (
    "You responded with plain text instead of using the tool-calling mechanism. "
    "Do not write JSON in your reply — call an actual tool (ask_user or render_view) "
    "using function calling."
)

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


def _original_user_request(messages):
    """The person's first message in this conversation. Ported verbatim."""
    for m in messages:
        if m.get("role") == "user":
            return m.get("content") or ""
    return ""


def _request_too_vague_for_direct_render(messages):
    """Code-enforced gate — see this module's docstring for why this isn't left to the system
    prompt alone. Only blocks render_view before ask_user has been used once; after a clarifying
    round happens, render_view is always allowed through to this check regardless of how short
    the ORIGINAL request was."""
    original = _original_user_request(messages)
    return len(original.split()) < _MIN_WORDS_FOR_DIRECT_RENDER


def _is_lazy_clarifying_question(question, user_request):
    """Same heuristic as every other connector — see agent_gmail.py's copy for the full
    reasoning. Ported verbatim."""
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


# Guardrail added 2026-08-25 after this was reported live: the small hosted model
# sometimes doesn't fill in ask_user's own tool-description EXAMPLE template — it
# copies the shape almost verbatim, angle brackets and all, e.g. asking "What's the
# main purpose? <what you described>, or something else?" and "Which tools/apps
# should it appear to connect to? <2-3 example tools that fit the domain you
# described> — simulated is fine." instead of ever substituting real words. The
# example in ask_user's own description literally contains '<...>' placeholders
# (by design, as a shape to imitate) — that's exactly the text a small model can
# fall back to reproducing when it's unsure what to write, since it's right there
# in its own instructions. Nothing else in this connector's reliability layer was
# checking ask_user's actual CONTENT quality — _is_lazy_clarifying_question only
# catches an echoed REQUEST, not a copied TEMPLATE. This mirrors
# _find_placeholder_labels' role for render_view (same "don't trust the system
# prompt alone" philosophy), just for the question text instead of row/field
# values.
_UNFILLED_PLACEHOLDER_RE = re.compile(r"<[^<>]{2,100}>")


def _find_unfilled_placeholder(question):
    """Returns the literal '<...>' span(s) still present in a clarifying question,
    if any — evidence the model copied ask_user's own example template instead of
    writing real, filled-in content. Empty list means the question looks clean."""
    return _UNFILLED_PLACEHOLDER_RE.findall(question or "")


TEMPLATE_PLACEHOLDER_NUDGE = (
    "ask_user was rejected: the question still contains literal, unfilled placeholder text "
    "like '<...>' copied straight from ask_user's own tool description — that bracketed text "
    "is an example SHAPE to follow, not something to paste into your actual question. Rewrite "
    "the entire question with every '<...>' replaced by your own real words tailored to what "
    "the person described (a real purpose, real example tools/apps, etc.) — no angle brackets "
    "anywhere in the final text."
)


# The exact tool-response content written below (search for it) only when an
# ask_user call is actually ACCEPTED and returned to the person as the real
# clarifying question — never for a rejected attempt. _ask_user_already_used
# uses this to tell the two apart; see its docstring for why that distinction
# matters.
_ASK_USER_ACCEPTED_SENTINEL = "(waiting for the user's answer)"


def _ask_user_already_used(messages):
    """True only if a PRIOR ask_user call in this conversation was actually
    ACCEPTED and shown to the person — not merely attempted. Bug fixed
    2026-08-25: the original version (still literally "ported verbatim" to
    every other connector) counted ANY assistant tool_call named ask_user,
    including one THIS reliability layer itself rejected (lazy-echo, or —
    once _find_unfilled_placeholder started catching the model copying its
    own tool-description template — the new placeholder rejection too). A
    rejected attempt's assistant tool_calls message still gets appended to
    `messages` before its per-call rejection is decided, so once anything
    rejected a first ask_user attempt, a legitimately-retried SECOND
    attempt (the model correctly fixing what got rejected) was wrongly
    flagged as "already asked" and rejected as well — derailing the whole
    conversation instead of ever reaching the person with a real question.
    Only counts an ask_user call whose paired tool-response message is the
    literal _ASK_USER_ACCEPTED_SENTINEL this file writes solely on
    acceptance (see the two `return {"clarify": ...}` sites below) — a
    rejected call's tool response is always one of the *_NUDGE strings
    instead, so it's correctly excluded here."""
    for i, m in enumerate(messages):
        if m.get("role") != "assistant":
            continue
        for tc in (m.get("tool_calls") or []):
            fn = tc.get("function") if isinstance(tc, dict) else None
            if not (fn and fn.get("name") == "ask_user"):
                continue
            tc_id = tc.get("id")
            for later in messages[i + 1:]:
                if later.get("role") == "tool" and later.get("tool_call_id") == tc_id:
                    if later.get("content") == _ASK_USER_ACCEPTED_SENTINEL:
                        return True
                    break
    return False


def run_agent(user_request=None, max_steps=8, verbose=True, messages=None, fetched_data=False):
    """
    Thin retry wrapper — same network-retry behavior and resume-after-clarify calling
    convention (messages/fetched_data) as every other connector, so studio.py's generic
    dispatch works unchanged. `fetched_data` is accepted and threaded through only for
    interface compatibility with that generic dispatch — this connector has no fetch step,
    so nothing here actually gates on its value.
    """
    result = _run_agent_once(user_request, max_steps, verbose, messages, fetched_data)
    if isinstance(result, dict) and result.get("_network_error"):
        if verbose:
            print("  [retry] first attempt failed on a network error — retrying the whole request once", file=sys.stderr)
        result = _run_agent_once(user_request, max_steps, verbose, messages, fetched_data)
    if isinstance(result, dict):
        result.pop("_network_error", None)
    return result


def _run_agent_once(user_request, max_steps, verbose, messages, fetched_data):
    if messages is None:
        messages = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": user_request},
        ]
    else:
        messages = list(messages)

    for step in range(max_steps):
        if verbose:
            print(f"  [step {step+1}] calling model...", file=sys.stderr)
        step_start = time.time()
        try:
            try:
                resp = client.chat.completions.create(
                    model=MODEL,
                    messages=messages,
                    tools=TOOLS,
                    tool_choice="required",
                    max_tokens=3500,
                )
            except Exception:
                resp = client.chat.completions.create(
                    model=MODEL,
                    messages=messages,
                    tools=TOOLS,
                    tool_choice="auto",
                    max_tokens=3500,
                )
        except Exception as e:
            elapsed = time.time() - step_start
            if verbose:
                print(f"  [step {step+1}] model call failed after {elapsed:.1f}s: {type(e).__name__}: {e}", file=sys.stderr)
            return {
                "_network_error": True,
                "error": (
                    f"couldn't reach the model ({type(e).__name__}) after {elapsed:.0f}s — "
                    "this is a network problem talking to NVIDIA's API, not the model taking "
                    "a long time to think. Check your connection and try again."
                ),
            }
        if verbose:
            finish_reason = getattr(resp.choices[0], "finish_reason", "?")
            print(f"  [step {step+1}] model call took {time.time()-step_start:.1f}s (finish_reason={finish_reason})", file=sys.stderr)

        msg = resp.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None)

        if tool_calls and len(tool_calls) > 1:
            # See agent_gmail.py's copy of this exact fix (2026-08-25) for the full story —
            # this hosted model's own chat template can't re-parse a history turn with more
            # than one tool_calls entry, so only the first of an over-eager multi-call turn
            # is ever kept, the rest dropped before anything is built into a message.
            if verbose:
                dropped = [tc.function.name for tc in tool_calls[1:]]
                print(f"  [step {step+1}] model returned {len(tool_calls)} tool_calls in one turn — keeping only the first ({tool_calls[0].function.name}), dropping {dropped}", file=sys.stderr)
            tool_calls = [tool_calls[0]]

        if not tool_calls:
            reply = (msg.content or "").strip()
            recovered_calls = _parse_tool_calls_from_text(reply, verbose=verbose)

            if recovered_calls:
                if verbose:
                    names = [n for n, _ in recovered_calls]
                    print(f"  [step {step+1}] model wrote {len(recovered_calls)} call(s) as plain text instead of real tool calls ({names}) — recovering", file=sys.stderr)
                messages.append({"role": "assistant", "content": msg.content})

                render_rejection = None

                for call_name, call_args in recovered_calls:
                    if call_name == "render_view":
                        if not _ask_user_already_used(messages[:-1]) and _request_too_vague_for_direct_render(messages):
                            if verbose:
                                print(f"  [step {step+1}] recovered render_view — REJECTED: request too vague, needs a clarifying round first", file=sys.stderr)
                            render_rejection = NEEDS_CLARIFY_NUDGE
                            continue
                        problem = validate_view(call_args, UI_SCHEMA)
                        if problem is not None:
                            if verbose:
                                print(f"  [step {step+1}] recovered render_view — REJECTED: {problem}", file=sys.stderr)
                            render_rejection = problem
                            continue
                        placeholder = _find_placeholder_labels(call_args)
                        if placeholder:
                            if verbose:
                                print(f"  [step {step+1}] recovered render_view — REJECTED: placeholder-looking content: {placeholder}", file=sys.stderr)
                            render_rejection = (
                                "render_view was rejected: it contains placeholder/template-looking "
                                f"text instead of real invented content — {', '.join(repr(p) for p in placeholder)}. "
                                "Write actual, specific sample content, not a field's own name as its value."
                            )
                            continue
                        if verbose:
                            print(f"  [step {step+1}] recovered render_view — valid, done", file=sys.stderr)
                            print(f"  [step {step+1}] accepted render shape: {_render_summary(call_args)}", file=sys.stderr)
                        return {"render": call_args}

                    elif call_name == "ask_user":
                        if _ask_user_already_used(messages[:-1]):
                            if verbose:
                                print(f"  [step {step+1}] recovered ask_user called again after already asking once — REJECTED", file=sys.stderr)
                            continue
                        question = (call_args.get("question") or "").strip() or "Could you tell me more about what you'd like this interface to do?"
                        original_request = _original_user_request(messages)
                        if _is_lazy_clarifying_question(question, original_request):
                            if verbose:
                                print(f"  [step {step+1}] recovered ask_user: \"{question}\" — REJECTED, just echoes the request", file=sys.stderr)
                            continue
                        unfilled = _find_unfilled_placeholder(question)
                        if unfilled:
                            if verbose:
                                print(f"  [step {step+1}] recovered ask_user: \"{question}\" — REJECTED, unfilled placeholder(s): {unfilled}", file=sys.stderr)
                            continue
                        if verbose:
                            print(f"  [step {step+1}] recovered ask_user: {question}", file=sys.stderr)
                        return {"clarify": question, "messages": messages, "fetched_data": fetched_data}

                nudge_parts = [p for p in (render_rejection,) if p]
                if not nudge_parts:
                    nudge_parts = [NO_TOOL_CALL_NUDGE]
                messages.append({"role": "user", "content": " ".join(nudge_parts)})
                continue

            if verbose:
                print(f"  [step {step+1}] responded in plain text instead of calling a tool — nudging", file=sys.stderr)
                print(f"  [step {step+1}] raw reply was: {reply!r}", file=sys.stderr)
            messages.append({"role": "assistant", "content": msg.content})
            messages.append({"role": "user", "content": NO_TOOL_CALL_NUDGE})
            continue

        messages.append({
            "role": "assistant",
            "content": msg.content,
            "tool_calls": [tc.model_dump() for tc in tool_calls],
        })

        for tc in tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except json.JSONDecodeError:
                args = {}
            args = normalize_args(args)

            if name == "ask_user":
                if _ask_user_already_used(messages[:-1]):
                    if verbose:
                        print(f"  [step {step+1}] ask_user called again after already asking once — REJECTED", file=sys.stderr)
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": ALREADY_ASKED_NUDGE})
                    continue
                question = (args.get("question") or "").strip() or "Could you tell me more about what you'd like this interface to do?"
                original_request = _original_user_request(messages)
                if _is_lazy_clarifying_question(question, original_request):
                    if verbose:
                        print(f"  [step {step+1}] ask_user: \"{question}\" — REJECTED, just echoes the request", file=sys.stderr)
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": LAZY_CLARIFY_NUDGE})
                    continue
                unfilled = _find_unfilled_placeholder(question)
                if unfilled:
                    if verbose:
                        print(f"  [step {step+1}] ask_user: \"{question}\" — REJECTED, unfilled placeholder(s): {unfilled}", file=sys.stderr)
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": TEMPLATE_PLACEHOLDER_NUDGE})
                    continue
                if verbose:
                    print(f"  [step {step+1}] ask_user: {question}", file=sys.stderr)
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": _ASK_USER_ACCEPTED_SENTINEL})
                return {"clarify": question, "messages": messages, "fetched_data": fetched_data}

            if name == "render_view":
                if not _ask_user_already_used(messages[:-1]) and _request_too_vague_for_direct_render(messages):
                    if verbose:
                        print(f"  [step {step+1}] render_view called — REJECTED: request too vague, needs a clarifying round first", file=sys.stderr)
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": NEEDS_CLARIFY_NUDGE})
                    continue
                problem = validate_view(args, UI_SCHEMA)
                if problem is not None:
                    if verbose:
                        print(f"  [step {step+1}] render_view called — REJECTED: {problem}", file=sys.stderr)
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": problem})
                    continue

                placeholder = _find_placeholder_labels(args)
                if placeholder:
                    if verbose:
                        print(f"  [step {step+1}] render_view called — REJECTED: placeholder-looking content: {placeholder}", file=sys.stderr)
                    messages.append({
                        "role": "tool", "tool_call_id": tc.id,
                        "content": (
                            "render_view was rejected: it contains placeholder/template-looking "
                            f"text instead of real invented content — {', '.join(repr(p) for p in placeholder)}. "
                            "Write actual, specific sample content, not a field's own name as its value."
                        ),
                    })
                    continue

                if verbose:
                    print(f"  [step {step+1}] render_view called — valid, done", file=sys.stderr)
                    print(f"  [step {step+1}] accepted render shape: {_render_summary(args)}", file=sys.stderr)
                return {"render": args}

            # No real data-fetch tool exists in this connector — any other
            # tool name here is unrecognized (dead in practice, kept only
            # as a harmless safety net matching the other connectors' shape).
            fn = TOOL_FUNCTIONS.get(name)
            result = fn(**args) if fn else {"error": "unknown tool"}
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result),
            })

    return {"error": "hit max_steps without a valid render_view"}


if __name__ == "__main__":
    query = sys.argv[1] if len(sys.argv) > 1 else "clothing brand"
    print(f"REQUEST: {query}\n", file=sys.stderr)

    result = run_agent(query)
    while "clarify" in result:
        print(f"\nCLARIFYING QUESTION: {result['clarify']}", file=sys.stderr)
        answer = input("your answer> ")
        messages = result["messages"] + [{"role": "user", "content": answer}]
        result = run_agent(messages=messages, fetched_data=result.get("fetched_data", False))

    print(json.dumps(result, indent=2))

    if "render" in result:
        out_path = Path(__file__).parent / "output.html"
        out_path.write_text(render_html(result["render"], query))
        print(f"\nrendered screen written to {out_path}", file=sys.stderr)
        webbrowser.open(out_path.as_uri())
