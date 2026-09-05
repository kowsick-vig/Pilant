"""
Composition Engine wired to connectors_healthcare.py — a REAL connector
against Epic's open FHIR sandbox (synthetic test patients, real OAuth2/FHIR
exchange — see that file's docstring for the full setup and the honest
caveats about sandbox data being sparse and non-PHI). Same schema, same
bounded tool-calling loop, same conversational ask_user flow as
agent_helpdesk.py and agent_github.py; only the domain changed, to a
clinic's front desk: today's appointments, no-shows, and check-in status.
No identity scoping here on purpose, same reasoning as agent_helpdesk.py —
this demo isn't re-proving the access-control story agent_github.py and
agent_customer.py already cover.

RELIABILITY LAYER: ported from agent_gmail.py / agent_slack.py 2026-08-25,
after this file was found to be running an older, less-hardened version of
this stack — no structural JSON repair (truncated/blanket-quoted
"components" strings), no recovery of a tool call the model wrote out as
plain text instead of a real function call, and no connector-local
placeholder-label denylist layered on top of the generic fabrication
guardrail. This connector already had the ask_user lazy-clarify checks and
the generic find_fabricated_content wiring, both of which are reused as-is
below rather than duplicated. See agent_gmail.py's module/function
docstrings for the full story of each fix; agent_slack.py is a second,
independent confirmation the same port applies cleanly to a different
connector.
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
from connectors_healthcare import get_appointments
from validation import validate_view
from renderer import render_html
from guardrails import find_fabricated_content, fabricated_content_nudge

env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=os.environ["NVIDIA_API_KEY"],
    # See agent_github.py for why this matters: without it, a stalled
    # connection can hang for 30+ minutes with zero output.
    timeout=45.0,
    # 0, not the SDK's own retry count — see agent_github.py's client
    # comment for the full reasoning: the SDK's internal retry compounds
    # with our own two retry layers and turns a single slow call into a
    # multi-minute wait instead of adding real resilience.
    max_retries=0,
)

MODEL = "meta/llama-3.1-8b-instruct"  # see agent_github.py's comment on this same line — gpt-oss-120b was hanging with zero bytes back on NVIDIA's backend as of 2026-08-22.

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_appointments",
            "description": (
                "Fetch appointments for the clinic's configured sandbox test patients, "
                "optionally filtered by status: booked (upcoming, not yet checked in), "
                "checked_in (arrived, waiting), fulfilled (visit completed), cancelled, "
                "or no_show."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["booked", "checked_in", "fulfilled", "cancelled", "no_show"],
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_user",
            "description": (
                "Ask the person a single clarifying question before doing anything else. Use "
                "this ONLY when the request is genuinely ambiguous in a way that would change "
                "what you'd fetch. The question must NAME the specific ambiguity and offer "
                "concrete options — it must add information the person doesn't already have, "
                "not just repeat their own words back as a question. For example, if they ask "
                "'what needs attention right now', a GOOD question is 'do you mean no-shows, "
                "cancellations, or patients who are checked in and waiting?' — a BAD question "
                "is 'what needs attention right now?' or any other rewording that doesn't name "
                "the actual options. Do NOT use this for requests you can reasonably interpret "
                "yourself. Ask AT MOST ONE question per request: after the person answers, "
                "proceed straight to fetching data and rendering."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "One short, specific question."}
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
                "Emit the final generated screen for the person's request. Call this exactly "
                "once, as the last step, after fetching whatever data you need. Include ONLY "
                "the components required to answer the request — no unrelated appointments, "
                "no default dashboard."
            ),
            "parameters": UI_SCHEMA,
        },
    },
]

SYSTEM = (
    "You are Pilant's Composition Engine, embedded as an AI Copilot in a clinic's front-desk "
    "software, backed by a real connected Epic sandbox. You're having a normal conversation with "
    "a staff member — some of what they say will be a real request for appointment data, and "
    "some of it will just be talk.\n\n"
    "If they're asking about today's appointments — who's waiting, no-shows, cancellations, a "
    "specific patient or provider, and so on — that's a data request: call get_appointments with "
    "whatever status filter answers it, then call render_view exactly once with a screen that "
    "shows ONLY what they asked for — no unrelated appointments, no default dashboard. Use "
    "judgment on badge tones: cancelled and no_show → tone 'critical'; booked (not yet checked "
    "in) → tone 'warning'; fulfilled → tone 'good'; checked_in → tone 'default'. Do not pad the "
    "screen with anything unrequested.\n\n"
    "If they're NOT asking for appointment data — a greeting, thanks, small talk, a question "
    "about what you can do, or anything else conversational — do not call any tool. Just reply "
    "normally in plain text, like a helpful, friendly assistant would. There's nothing to fetch "
    "or render for a message like that, and forcing a screen onto 'hi' would be broken and "
    "unhelpful. Keep these replies short and natural, and when it fits, mention you can pull up "
    "real appointment data if they want to see something specific.\n\n"
    "Only call ask_user first if a genuine data request is ambiguous in a way that would change "
    "what you'd fetch — and only once. After the person answers, proceed straight to "
    "get_appointments and render_view; do not ask a second question in the same request, and do "
    "not ask about things you could reasonably infer yourself. A clarifying question must name "
    "the actual options (e.g. 'no-shows, cancellations, or checked-in-and-waiting?') — never "
    "just repeat the person's own request back as a question; that isn't clarifying anything.\n\n"
    "Critical: render_view's fields must contain real, literal data — the actual appointments "
    "returned by get_appointments. NEVER invent a patient, provider, or time, and never write "
    "placeholder/template syntax such as {{get_appointments(...)}}. This connects to a sandbox "
    "with a small, fixed set of test patients, so it's normal and expected for a filtered query "
    "to come back with very few results, or none — render that honestly rather than padding it "
    "out. 'components' must be an actual JSON array of component objects, not a string."
)

TOOL_FUNCTIONS = {
    "get_appointments": get_appointments,
}


_LONE_SURROGATE_RE = re.compile("[\ud800-\udfff]")


def _scrub_surrogates(s):
    """Strips lone/unpaired surrogate codepoints (U+D800-U+DFFF) that can end up in a string
    after ast.literal_eval decodes a \\uXXXX escape that was actually one half of a UTF-16
    surrogate pair for an emoji or other rare character. CPython's str type will happily hold
    those, but Flask's UTF-8 response encoding can't — better to drop them here, once, than have
    an otherwise-valid recovered render blow up with a UnicodeEncodeError when the page actually
    renders. Ported verbatim from agent_gmail.py, where this was found live 2026-08-25 — see
    that file's copy for the full story."""
    return _LONE_SURROGATE_RE.sub("", s)


_LITERAL_UNICODE_ESCAPE_RE = re.compile(r"\\u([0-9a-fA-F]{4})")


def _unescape_literal_unicode_escapes(s):
    """Fixes a failure mode found live on agent_gmail.py 2026-08-25 (round 7) and ported here:
    a "components" argument is itself a JSON string VALUE nested inside the outer function-call
    JSON, so when the model needs an apostrophe inside it, the backslash in a normal '\\u0027'
    escape has to itself be escaped for the outer layer — '\\\\u0027'. When the model gets that
    doubling wrong, the raw literal 6 characters backslash-u-0-0-2-7 survive as CONTENT instead
    of decoding to a real apostrophe — visible directly in the rendered page ("you\\u0027re"
    instead of "you're"). None of the structural repairs above (bracket-closing, quote-repair)
    catch this, since they operate on JSON/Python structure, not on escape sequences sitting
    inertly inside an already-parsed string value.

    Runs a second, explicit unescape pass over every leaf string value normalize_args returns.
    A correctly-decoded string never contains this raw pattern, so this is safe to apply
    unconditionally. Must run BEFORE _scrub_surrogates() — see agent_gmail.py's copy of this
    function for why (a doubly-escaped surrogate-pair emoji needs the chance to get cleaned up
    by that pass after this one runs)."""
    return _LITERAL_UNICODE_ESCAPE_RE.sub(lambda m: chr(int(m.group(1), 16)), s)


_STRUCTURAL_QUOTE_RE = re.compile(r"(?<!\w)'|'(?!\w)")


def _repair_blanket_quoted_json(s):
    """Last-resort recovery, ported verbatim from agent_gmail.py (see that file's copy for the
    full reasoning and the real ground-truth verification it was validated against): the model
    sometimes writes a JSON string value as Python-dict-repr text using single quotes as
    delimiters, but doesn't escape genuine apostrophes inside real content — so the same
    character serves as both the structural delimiter and literal text, which neither json.loads
    nor ast.literal_eval can resolve on their own.

    Heuristic: a delimiter quote is (almost) always adjacent to punctuation, brackets, or
    whitespace — never sandwiched between two letters. A genuine English apostrophe
    (contraction/possessive) is, conversely, (almost) always flanked by a word character on both
    sides. So: promote every quote NOT flanked by \\w on both sides to a JSON double-quote, and
    leave every quote that IS flanked by \\w on both sides alone.

    Not a general solution — a possessive sitting right at the very edge of a value could still
    fool it — but it's a real improvement over failing outright, and it only ever runs as a last
    resort after both real JSON and Python-literal parsing have already failed."""
    return _STRUCTURAL_QUOTE_RE.sub('"', s)


def _close_truncated_json(s):
    """Recovery for a string value that's genuinely well-formed right up until it just... stops,
    missing the closing brackets/braces needed to finish the structure. Ported verbatim from
    agent_gmail.py — see that file's copy for the full story of the live failure this was written
    to fix (round 4, 2026-08-25).

    Walks the string tracking a stack of open '{'/'[' delimiters, correctly skipping over
    characters inside JSON string literals (so a literal bracket in real content isn't
    miscounted) and honoring '\\' escapes. If the string ends mid-literal or with unclosed
    delimiters, appends exactly what's needed to close them, innermost first. Returns None
    (don't touch it) if the brackets are already balanced, or if a closing delimiter appears that
    doesn't match what's on the stack — that's a different kind of malformed that this heuristic
    has no business guessing at."""
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


def _repair_candidates(s):
    """Every heuristic-repaired variant of a malformed JSON/Python-literal string worth trying
    with json.loads, in order. Shared by normalize_args() and _json_or_literal() below. Ported
    from agent_gmail.py 2026-08-25 (round 6 there): closing a truncated payload BEFORE repairing
    its blanket single-quoting can corrupt the result when the raw text has a literal, already
    backslash-escaped double quote embedded in real content (e.g. a display name written as
    \\"Some Name\\" inside the still-single-quoted string) — _close_truncated_json() assumes a
    bare '\"' always marks a real JSON string boundary, which is only true once the blanket
    single-quoting has already been repaired away. So both orders are tried: close-then-repair
    (the original fix) and repair-then-close (needed when the raw text has literal embedded
    quotes) — see agent_gmail.py's copy of this function for the full real-world reproduction."""
    closed = _close_truncated_json(s)
    repaired = _repair_blanket_quoted_json(s)
    repaired_then_closed = _close_truncated_json(repaired) if repaired else None
    return [c for c in (
        closed,
        repaired,
        _repair_blanket_quoted_json(closed) if closed else None,
        repaired_then_closed,
    ) if c is not None]


def normalize_args(obj):
    """Same recovery as agent_gmail.py/agent_slack.py (ported 2026-08-25): double-encoded JSON
    strings, "true"/"false" strings, and Python-literal-style strings this model sometimes emits
    instead of real JSON — e.g. "[{'type': 'panel', ...}]" (single-quoted, Python dict repr)
    rather than '[{"type": "panel", ...}]" (double-quoted, valid JSON). json.loads rejects the
    former since single quotes aren't valid JSON syntax; ast.literal_eval, which only ever
    evaluates Python literals (no code execution), handles it as a fallback. If those fail
    because the model blanket-used unescaped single quotes for both delimiters and genuine
    apostrophes in the content, or because the model's JSON just stops before it's finished, or
    both together in one payload, a heuristic repair runs as a last resort before giving up,
    trying every repair-order combination (see _repair_candidates() above).

    Every string this function returns — recovered or original — is passed through
    _unescape_literal_unicode_escapes() then _scrub_surrogates() (order matters — see that
    function's docstring above), so both fixes apply everywhere normalize_args is used (both real
    tool_call arguments and the plain-text-recovered calls from _parse_tool_calls_from_text()
    below), not just the one path that surfaced each bug."""
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
    """One-line shape summary of an ACCEPTED render_view call — heading, how many components,
    and the row count of each list component. Ported verbatim from agent_gmail.py: runs on every
    successful render (not just failures) so a render that passes every check but is thin/wrong
    in a way none of our checks catch is still diagnosable from the log alone."""
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
    """Try json.loads, then ast.literal_eval, on a string that should decode to a JSON/Python
    value, falling back to the same bracket-closing / blanket-quote repairs normalize_args() uses
    on a nested string. Returns the parsed value, or the original string unchanged if every parse
    fails — used by _parse_tool_calls_from_text() below, which needs this same recovery applied
    to a whole reply before it even knows whether there's a dict with "name"/"parameters" in it.
    Ported verbatim from agent_gmail.py."""
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


TOOL_NAMES = {"get_appointments", "ask_user", "render_view"}


def _parse_tool_calls_from_text(reply, verbose=False):
    """
    Recovers tool call(s) the model wrote out as plain assistant TEXT instead of using real
    function-calling — the dominant failure mode found live on agent_gmail.py 2026-08-25 (ported
    here verbatim, only the tool-name set differs via the module-level TOOL_NAMES above), in one
    of three shapes:

      1. JSON-ish, one call: {"name": "render_view", "parameters": {...}}
      2. Python-call syntax, one call: render_view(components=[...], heading="...")
      3. Python-call syntax, MULTIPLE calls chained one per line — e.g. writing
         get_appointments(...) and render_view(...) back to back in the same reply, as if
         narrating a plan instead of calling either for real.

    In every observed case (on Gmail), finish_reason was "stop", not "length" — the model
    believed it had finished a complete response, it just didn't put it where the API expects a
    tool call to go. That means the text isn't truncated, only misplaced, which is what makes
    recovering it reliable rather than a guess: nothing here infers missing content, it only
    reparses content the model already fully generated.

    ast.parse(text, mode="exec") (not "eval") is what makes shape 3 recoverable at all — "eval"
    mode only accepts a single expression and raises SyntaxError on anything with more than one
    top-level statement. "exec" mode parses a whole sequence of statements, so each recognized
    call is pulled out independently.

    Each call's arguments still have to ast.literal_eval cleanly, one call at a time — if one
    call's arguments reference something non-literal, only THAT call is skipped; every other call
    in the same reply still gets recovered. That keeps this from ever inventing data — it only
    ever reparses complete, literal data the model already wrote out in full, and silently drops
    anything it can't be sure about rather than guessing.

    Returns a list of (tool_name, args_dict) tuples, in the order they appeared in the reply.
    Empty list if nothing recognizable was found at all — the caller falls back to the
    pre-existing "please use real function calling" nudge in that case, so a failed recovery is
    never worse than the old behavior.
    """
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
        # A JSON-shaped {"name": ..., "parameters": ...} object that isn't
        # one of our tools, or has non-dict parameters, isn't a chained
        # multi-statement reply either — nothing more to try.
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
            # This one call's arguments aren't all literal (e.g. a nested,
            # unevaluated function call as a value) — skip just this call,
            # keep looking at the rest of the reply.
            continue
        calls.append((name, normalize_args(args)))
    return calls


_PLACEHOLDER_VALUES = {"patient", "provider", "status", "time", "reason", "date"}
_PLACEHOLDER_PATTERN = re.compile(r"^(patient|appointment|provider)\s*\d+$", re.IGNORECASE)


def _find_placeholder_labels(view):
    """Connector-local safety net, layered on top of guardrails.py's generic fabrication
    check: catches the model writing a field's own NAME as its VALUE (row name/note, or a
    field's value, literally "Patient", "Provider", "Patient 1", ...) instead of the real fetched
    text. guardrails.py's check deliberately skips single-word values, to avoid false-flagging
    real short UI text like "Booked" or "Critical" — which is exactly the gap these placeholders
    exploit, since each one is a single word. Adapted from agent_gmail.py/agent_slack.py's copy of
    this function for this connector's own field-naming conventions (patient/provider/status/time
    rather than subject/snippet/from, "Patient N"/"Appointment N" rather than "Email N"/
    "Message N")."""
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
    "Do not write JSON in your reply — call an actual tool (get_appointments, ask_user, or "
    "render_view) using function calling."
)

NO_DATA_YET_NUDGE = (
    "render_view was rejected: you haven't called get_appointments yet, so you have no real "
    "data to show. Call it first, wait for its real result, then call render_view again "
    "using those real values."
)

ALREADY_FETCHED_NUDGE = (
    "get_appointments was not run again: you already have its real result earlier in this "
    "conversation. Don't call it a second time — use those exact real values now in a "
    "render_view call, made through the actual tool-calling mechanism, not written out as text."
)

ALREADY_ASKED_NUDGE = (
    "ask_user was rejected: you already asked one clarifying question in this conversation "
    "and the person just answered it. Do not ask another question — use their answer to call "
    "get_appointments now, then render_view."
)


# The fabrication guardrail used to live here as a bespoke,
# appointments-only implementation (matching only row["name"]/field["value"]
# against a "patient"/"provider" name pool). It's now guardrails.py — a
# connector-agnostic version that also protects agent_github.py, which
# previously had no equivalent check at all. See that module's docstring
# for the full reasoning behind the generalization and its trade-offs.


def _original_user_request(messages):
    """The person's first message in this conversation — used to check a
    proposed ask_user question against what they actually asked, so a lazy
    restatement can be told apart from a real clarifying question."""
    for m in messages:
        if m.get("role") == "user":
            return m.get("content") or ""
    return ""


def _is_lazy_clarifying_question(question, user_request):
    """
    True if `question` is mostly just user_request's own words handed back
    rather than a genuinely narrowing question — the failure mode observed
    in testing, where the model asked 'what needs attention right now' in
    response to 'what appointments need attention right now?' instead of
    naming the actual ambiguity (no-shows vs cancellations vs
    checked-in-and-waiting). A real clarifying question almost always
    introduces at least a couple of words the person never used (the
    concrete options), so high word overlap with near-zero new vocabulary
    is a good, cheap signal of "just echoed it back".
    """
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
    "question names the specific ambiguity and offers concrete options, e.g. if asked "
    "'what needs attention', ask 'do you mean no-shows, cancellations, or patients who "
    "are checked in and waiting?' — not a reworded copy of their own question. Either "
    "ask a real, narrowing question with actual options in it, or skip ask_user "
    "entirely and call get_appointments with your best reasonable interpretation."
)


def _ask_user_already_used(messages):
    """
    Same hard cap as agent_github.py and agent_helpdesk.py — see either
    file's copy of this function for why this is enforced in code rather
    than trusted to the system prompt alone.
    """
    for m in messages:
        if m.get("role") != "assistant":
            continue
        for tc in (m.get("tool_calls") or []):
            fn = tc.get("function") if isinstance(tc, dict) else None
            if fn and fn.get("name") == "ask_user":
                return True
    return False


def run_agent(user_request=None, max_steps=8, verbose=True, messages=None, fetched_data=False):
    """
    Thin retry wrapper — see agent_github.py's run_agent() for the full
    explanation of the network-retry behavior and the resume-after-clarify
    calling convention (messages/fetched_data). No `user` argument here —
    this connector doesn't scope by identity.
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
            # "auto", not "required" — a genuine data request should still
            # always end up calling get_appointments/render_view (the SYSTEM
            # prompt is explicit about that), but forcing every single
            # message through a tool call is what made plain conversation
            # (e.g. "hi") impossible: the model had no way to just answer,
            # so it looped until max_steps. "auto" lets it reply in plain
            # text for anything that genuinely isn't a data request.
            resp = client.chat.completions.create(
                model=MODEL,
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
                # Raised from 700 to 3500 (2026-08-25 port) to match agent_gmail.py's tuned
                # value — the same "components" truncation failure mode Gmail hit at lower
                # budgets applies here too, since it's the same model generating the same shape
                # of JSON. See agent_gmail.py's comment on this same call for the fuller history
                # of how that number was arrived at.
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
            print(f"  [step {step+1}] model call took {time.time()-step_start:.1f}s", file=sys.stderr)

        msg = resp.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None)

        if not tool_calls:
            reply = (msg.content or "").strip()

            # Try to recover a real tool call the model wrote as plain text
            # before falling back to just nudging it to try again — see
            # _parse_tool_calls_from_text()'s docstring for why this exists
            # and why it's safe (the text is complete, just misplaced).
            recovered_calls = _parse_tool_calls_from_text(reply, verbose=verbose)

            if recovered_calls:
                if verbose:
                    names = [n for n, _ in recovered_calls]
                    print(f"  [step {step+1}] model wrote {len(recovered_calls)} call(s) as plain text instead of real tool calls ({names}) — recovering", file=sys.stderr)
                messages.append({"role": "assistant", "content": msg.content})

                # Processed in the order they appeared — this matters when
                # a reply chains get_appointments then render_view: the
                # fetch runs first, so fetched_data is already True by the
                # time the render_view entry below is checked, exactly
                # mirroring what would have happened with two real,
                # separate tool calls.
                fetch_note = None
                render_rejection = None

                for call_name, call_args in recovered_calls:
                    if call_name == "render_view":
                        if not fetched_data:
                            if verbose:
                                print(f"  [step {step+1}] recovered render_view called before any data fetch — REJECTED", file=sys.stderr)
                            render_rejection = NO_DATA_YET_NUDGE
                            continue
                        problem = validate_view(call_args, UI_SCHEMA)
                        if problem is not None:
                            if verbose:
                                print(f"  [step {step+1}] recovered render_view — REJECTED: {problem}", file=sys.stderr)
                            render_rejection = problem
                            continue
                        fabricated = find_fabricated_content(call_args, messages) + _find_placeholder_labels(call_args)
                        if fabricated:
                            if verbose:
                                print(f"  [step {step+1}] recovered render_view — REJECTED: fabricated content not grounded in real fetched data: {fabricated}", file=sys.stderr)
                            render_rejection = fabricated_content_nudge(fabricated)
                            continue
                        if verbose:
                            print(f"  [step {step+1}] recovered render_view — valid and content-verified, done", file=sys.stderr)
                            print(f"  [step {step+1}] accepted render shape: {_render_summary(call_args)}", file=sys.stderr)
                        return {"render": call_args}

                    elif call_name == "get_appointments":
                        if fetched_data:
                            if verbose:
                                print(f"  [step {step+1}] recovered get_appointments, but data was already fetched — skipping the redundant call", file=sys.stderr)
                            continue
                        try:
                            result = get_appointments(**call_args)
                            fetched_data = True
                            if verbose:
                                print(f"  [step {step+1}] recovered get_appointments({call_args}) — ran it for real", file=sys.stderr)
                            fetch_note = "Here is the real result of that call — use these exact values, then call render_view: " + json.dumps(result)
                        except Exception as e:
                            if verbose:
                                print(f"  [step {step+1}] recovered get_appointments raised: {e}", file=sys.stderr)
                            fetch_note = f"That call failed: {e}"

                    elif call_name == "ask_user":
                        if _ask_user_already_used(messages[:-1]):
                            if verbose:
                                print(f"  [step {step+1}] recovered ask_user called again after already asking once — REJECTED", file=sys.stderr)
                            continue
                        question = (call_args.get("question") or "").strip() or "Could you clarify what you're looking for?"
                        original_request = _original_user_request(messages)
                        if _is_lazy_clarifying_question(question, original_request):
                            if verbose:
                                print(f"  [step {step+1}] recovered ask_user: \"{question}\" — REJECTED, just echoes the request", file=sys.stderr)
                            continue
                        if verbose:
                            print(f"  [step {step+1}] recovered ask_user: {question}", file=sys.stderr)
                        return {"clarify": question, "messages": messages, "fetched_data": fetched_data}

                # Nothing above returned — build ONE consolidated nudge out
                # of whatever this batch actually accomplished, instead of
                # a generic "use function calling" nudge that gives the
                # model no new information to correct with. If we just
                # fetched real data, hand it over inline so the next
                # attempt has no excuse to fabricate placeholders again; if
                # a render_view attempt was rejected, say exactly why.
                nudge_parts = [p for p in (fetch_note, render_rejection) if p]
                if not nudge_parts:
                    nudge_parts = [ALREADY_FETCHED_NUDGE if fetched_data else NO_TOOL_CALL_NUDGE]
                messages.append({"role": "user", "content": " ".join(nudge_parts)})
                continue

            # A real conversational reply (greeting, small talk, "what can
            # you do") is now a valid, final result — see the SYSTEM prompt
            # change above. But the weak fallback model sometimes tries to
            # fake a tool call by just writing JSON as plain text instead of
            # using real function-calling; that's not a conversational
            # reply, so still nudge it back in that case rather than
            # returning the raw JSON as if it were a chat message. Only
            # reached once _parse_tool_calls_from_text() above found nothing
            # recoverable — a genuine conversational reply should still
            # return {"text": reply}, same as before.
            looks_like_malformed_tool_call = (
                not reply
                or reply.startswith("{")
                or reply.startswith("[")
                or "render_view(" in reply
                or "get_appointments(" in reply
                or '"components"' in reply
            )
            if looks_like_malformed_tool_call:
                if verbose:
                    print(f"  [step {step+1}] responded in plain text instead of calling a tool — nudging", file=sys.stderr)
                    print(f"  [step {step+1}] raw reply was: {reply!r}", file=sys.stderr)
                messages.append({"role": "assistant", "content": msg.content})
                messages.append({"role": "user", "content": NO_TOOL_CALL_NUDGE})
                continue
            if verbose:
                print(f"  [step {step+1}] plain-text conversational reply — done", file=sys.stderr)
            return {"text": reply}

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
                question = (args.get("question") or "").strip() or "Could you clarify what you're looking for?"
                original_request = _original_user_request(messages)
                if _is_lazy_clarifying_question(question, original_request):
                    if verbose:
                        print(f"  [step {step+1}] ask_user: \"{question}\" — REJECTED, just echoes the request", file=sys.stderr)
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": LAZY_CLARIFY_NUDGE})
                    continue
                if verbose:
                    print(f"  [step {step+1}] ask_user: {question}", file=sys.stderr)
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": "(waiting for the user's answer)"})
                return {"clarify": question, "messages": messages, "fetched_data": fetched_data}

            if name == "render_view":
                if not fetched_data:
                    if verbose:
                        print(f"  [step {step+1}] render_view called before any data fetch — REJECTED", file=sys.stderr)
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": NO_DATA_YET_NUDGE})
                    continue
                problem = validate_view(args, UI_SCHEMA)
                if problem is not None:
                    if verbose:
                        print(f"  [step {step+1}] render_view called — REJECTED: {problem}", file=sys.stderr)
                        # Diagnostic-only, ported from agent_gmail.py: prints the actual,
                        # unmodified wire content — tc.function.arguments straight from the API,
                        # before any parsing — plus exactly why normalize_args() couldn't recover
                        # it, so any new failure here is diagnosable from real ground truth
                        # instead of a guess.
                        comp = args.get("components") if isinstance(args, dict) else None
                        if isinstance(comp, str):
                            print(f"  [step {step+1}] raw tc.function.arguments was: {tc.function.arguments!r}", file=sys.stderr)
                            print(f"  [step {step+1}] 'components' after normalize_args is still a str: {comp!r}", file=sys.stderr)
                            try:
                                json.loads(comp)
                                print(f"  [step {step+1}] (unexpected: json.loads on it just now actually succeeded)", file=sys.stderr)
                            except json.JSONDecodeError as je:
                                print(f"  [step {step+1}] json.loads fails with: {je}", file=sys.stderr)
                            try:
                                ast.literal_eval(comp)
                                print(f"  [step {step+1}] (unexpected: ast.literal_eval on it just now actually succeeded)", file=sys.stderr)
                            except (ValueError, SyntaxError) as ae:
                                print(f"  [step {step+1}] ast.literal_eval fails with: {ae}", file=sys.stderr)
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": problem})
                    continue

                fabricated = find_fabricated_content(args, messages) + _find_placeholder_labels(args)
                if fabricated:
                    if verbose:
                        print(f"  [step {step+1}] render_view called — REJECTED: fabricated content not grounded in real fetched data: {fabricated}", file=sys.stderr)
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": fabricated_content_nudge(fabricated)})
                    continue

                if verbose:
                    print(f"  [step {step+1}] render_view called — valid and content-verified, done", file=sys.stderr)
                    print(f"  [step {step+1}] accepted render shape: {_render_summary(args)}", file=sys.stderr)
                return {"render": args}

            fn = TOOL_FUNCTIONS.get(name)
            if verbose:
                print(f"  [step {step+1}] tool call: {name}({args})", file=sys.stderr)
            try:
                result = fn(**args) if fn else {"error": "unknown tool"}
                ok = bool(fn)
            except Exception as e:
                result = {"error": str(e)}
                ok = False
                if verbose:
                    print(f"  [step {step+1}] {name} raised: {e}", file=sys.stderr)
            if fn and ok:
                fetched_data = True
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result),
            })

    return {"error": "hit max_steps without a valid render_view"}


if __name__ == "__main__":
    query = sys.argv[1] if len(sys.argv) > 1 else "what appointments need attention right now?"
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
