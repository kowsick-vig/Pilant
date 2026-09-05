"""
Composition Engine wired to connectors_gmail.py — a REAL connector against
Google's Gmail API via OAuth2 (see that file's docstring for the
Client ID/Secret/refresh-token setup, and its notes on real Gmail search +
real unread status, genuinely different capabilities than the Slack
connector had). Same schema, same bounded tool-calling loop, same
conversational reply support as agent_healthcare.py (the most current
version of this architecture); only the connector changed, from a Slack
channel to a Gmail inbox.

FABRICATION GUARDRAIL: added 2026-08-24, same placement/wiring as
agent_healthcare.py / agent_github.py — find_fabricated_content() runs
right after validate_view() passes in the render_view branch below, and
rejects (nudges, doesn't crash) any row/field value that doesn't trace
back to a real get_gmail_messages result so far in this conversation.
This was added after the model, once past the schema/JSON issues below,
was observed inventing entirely fake content ("Message 1" / "This is the
first message") instead of using the real fetched inbox data — exactly
the failure mode this guardrail exists to catch.

No identity scoping here on purpose, same reasoning as agent_helpdesk.py —
this demo isn't re-proving the access-control story agent_github.py already
covers (also moot here: a Gmail refresh token is already scoped to exactly
one inbox, the one that authorized it).
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
from connectors_gmail import get_gmail_messages
from validation import validate_view
from renderer import render_html
from guardrails import find_fabricated_content, find_fabricated_stats, fabricated_content_nudge

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
    # connection can hang for 30+ minutes with zero output. Back to 45.0 — the 180.0 bump was for
    # minimax-m3's reasoning latency, moot now that MODEL is 8B again, which doesn't need it.
    timeout=45.0,
    # 0, not the SDK's own retry count — see agent_github.py's client
    # comment for the full reasoning: the SDK's internal retry compounds
    # with our own two retry layers and turns a single slow call into a
    # multi-minute wait instead of adding real resilience.
    max_retries=0,
)

MODEL = "meta/llama-3.1-8b-instruct"  # reverted from minimaxai/minimax-m3 2026-08-25 after its
# SECOND attempt the same day. The max_tokens/timeout raise (3500->12000, 45s->180s) genuinely
# fixed the empty-content problem — step 2 came back with finish_reason="tool_calls" and real,
# non-empty content this time. But two things still blocked it: (1) content quality — it wrote
# paraphrased/summarized row labels ("Second verification reminder", "Sign-in notification —
# confirm it was you") instead of the literal real sender/subject/snippet text, correctly
# rejected by the fabrication guardrail; this model needs different prompt tuning than llama did,
# not just more tokens. (2) the exact same 429 RateLimitError as the first attempt, immediately
# after 2 real calls — now reproduced twice in a row, confirming this is a hard, repeatable quota
# ceiling on the free "Prototype" tier, not a one-off. Two-for-two on the rate limit means this
# model isn't practically usable for Studio's multi-call loop on the free tier regardless of how
# well the content-quality issue gets tuned — that's the real blocker, and it needs a paid
# NVIDIA tier (or a different provider) to actually resolve, not more code changes here.
#
# Reverting to 8B because it's the only model in this whole investigation that reliably
# responds, isn't rate-limited, and doesn't burn its budget on invisible reasoning tokens.
# Full history of what's been tried and why, in order: gpt-oss-120b (2026-08-24) handled the
# JSON content BETTER than llama-3.1-8b but hung with zero bytes back (34s+ per call,
# NVIDIA-side). meta/llama-3.3-70b-instruct (2026-08-25) hit the identical hanging pattern.
# deepseek-ai/deepseek-r1 (2026-08-25) wasn't a valid model ID on this account. minimaxai/
# minimax-m3 (2026-08-25, two attempts) was valid, eventually produced real content once given
# enough token headroom, but hit a hard rate limit both times regardless.

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_gmail_messages",
            "description": (
                "Fetch recent real messages from the connected Gmail inbox, most recent first. "
                "`query` is REAL Gmail search syntax — the same operators you'd type in the "
                "Gmail search bar, e.g. 'from:someone@example.com', 'subject:invoice', "
                "'newer_than:3d', 'has:attachment'. `unread_only`, if true, restricts to "
                "genuinely unread messages. `limit` caps how many messages come back (default "
                "10, max 50)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Real Gmail search syntax, e.g. 'from:boss@company.com' or 'subject:invoice'.",
                    },
                    "unread_only": {"type": "boolean"},
                    "limit": {"type": "integer", "description": "How many messages to fetch."},
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
                "not just repeat their own words back as a question. Do NOT use this for "
                "requests you can reasonably interpret yourself. Ask AT MOST ONE question per "
                "request: after the person answers, proceed straight to fetching data and "
                "rendering."
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
                "the components required to answer the request — no unrelated messages, no "
                "default dashboard."
            ),
            "parameters": UI_SCHEMA,
        },
    },
]

SYSTEM = (
    "You are Pilant's Composition Engine, embedded as an AI Copilot connected to a real Gmail "
    "inbox. You're having a normal conversation with a person — some of what they say will be a "
    "real request to see email, and some of it will just be talk.\n\n"
    "If they're asking about their inbox — unread mail, mail from someone, mail about a topic, "
    "recent mail, and so on — that's a data request: call get_gmail_messages with whatever "
    "query/unread_only/limit answers it (query takes real Gmail search syntax — from:, "
    "subject:, newer_than:, has:attachment, etc. — use it, don't just fetch everything and "
    "filter mentally), then call render_view exactly once with a screen that shows ONLY what "
    "they asked for — no unrelated messages, no default dashboard. Use judgment on badge tones "
    "if a message needs one (e.g. unread → tone 'warning'; something urgent-sounding in the "
    "subject/snippet → tone 'critical'; otherwise 'default'). Do not pad the screen with "
    "anything unrequested.\n\n"
    "Component choice for multiple messages, this matters: when the request returns more than "
    "one message, put them ALL in a SINGLE 'list' component — one row per message (row 'name' "
    "= the subject or a short from+subject combo, row 'note' = the snippet or sender, badge for "
    "unread if useful) — never one 'panel' component per message. A panel-per-message wastes an "
    "entire nested fields array on every single email and will run you out of output budget "
    "before you finish the JSON, which is exactly the malformed-output failure you must avoid. "
    "Reserve 'panel' for when there is exactly ONE message to show in detail, or for a handful "
    "of aggregate stats — not for enumerating a list of messages. Do not add a second 'panel' "
    "component next to the list just to restate a count — the list itself already shows every "
    "message; a redundant summary panel is exactly the kind of unrequested padding to avoid.\n\n"
    "Counts and badges must match the real data, every time, not just the first item: if you "
    "called get_gmail_messages with unread_only=True, EVERY message it returned is genuinely "
    "unread — give every row the same unread badge, not just the first one. And any number you "
    "write anywhere (a heading like '3 unread emails', a subtitle, a count) must be the actual "
    "number of items get_gmail_messages returned this conversation — count the real list, never "
    "reuse a number from earlier in the conversation or guess one that sounds plausible.\n\n"
    "If they're NOT asking for email data — a greeting, thanks, small talk, a question about "
    "what you can do, or anything else conversational — do not call any tool. Just reply "
    "normally in plain text, like a helpful, friendly assistant would. There's nothing to fetch "
    "or render for a message like that. Keep these replies short and natural, and when it fits, "
    "mention you can pull up real inbox data if they want to see something specific.\n\n"
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

TOOL_FUNCTIONS = {
    "get_gmail_messages": get_gmail_messages,
}


_LONE_SURROGATE_RE = re.compile("[\ud800-\udfff]")


def _scrub_surrogates(s):
    """Strips lone/unpaired surrogate codepoints (U+D800-U+DFFF) that can end up in a string
    after ast.literal_eval decodes a \\uXXXX escape that was actually one half of a UTF-16
    surrogate pair for an emoji or other rare character — found live 2026-08-25 while adding
    _parse_tool_call_text() below: a real email subject line containing an emoji, once the model
    wrote it out as Python-literal text using \\uXXXX escapes instead of the real character,
    decoded into two lone surrogate codepoints. CPython's str type will happily hold those, but
    Flask's UTF-8 response encoding can't — better to drop them here, once, than have an
    otherwise-valid recovered render blow up with a UnicodeEncodeError when the page actually
    renders."""
    return _LONE_SURROGATE_RE.sub("", s)


_LITERAL_UNICODE_ESCAPE_RE = re.compile(r"\\u([0-9a-fA-F]{4})")


def _unescape_literal_unicode_escapes(s):
    """Fixes a failure mode found live 2026-08-25 (round 7, Studio testing): a real render
    showed literal text like "you\\u0027re eligible" and "McDonald\\u0027s" instead of real
    apostrophes — visible directly in the rendered screen, not just the logs. Root cause: this
    "components" argument is itself a JSON string VALUE nested inside the outer function-call
    JSON, so when the model needs an apostrophe inside it, the backslash in a normal '\\u0027'
    escape has to itself be escaped for the outer layer — '\\\\u0027'. The model got that
    doubling wrong (under-escaped, or the outer json.loads/ast.literal_eval pass only unwound
    one layer when two were needed), so after normal parsing the text still contains the raw,
    literal 6 characters backslash-u-0-0-2-7 as CONTENT rather than a decoded apostrophe — every
    repair in this file's chain (bracket-closing, quote-repair) operates on JSON/Python
    STRUCTURE, none of them re-interpret escape sequences sitting inertly inside an already-
    parsed string value, so this survived untouched all the way to the rendered page.

    This runs a second, explicit unescape pass over every leaf string value normalize_args
    returns, converting any surviving literal \\uXXXX sequence to its real character. A
    correctly-decoded string never contains this raw pattern (a real backslash followed by
    literal 'u' and four hex digits is not something normal prose writes), so this is safe to
    apply unconditionally rather than only when something looks broken.

    Must run BEFORE _scrub_surrogates(), not after: converting a doubly-escaped surrogate-pair
    escape (an emoji written as two adjacent \\uXXXX halves) this way produces two lone surrogate
    codepoints, exactly the malformed state _scrub_surrogates() exists to clean up — so it needs
    the chance to run on whatever this function produces."""
    return _LITERAL_UNICODE_ESCAPE_RE.sub(lambda m: chr(int(m.group(1), 16)), s)


_STRUCTURAL_QUOTE_RE = re.compile(r"(?<!\w)'|'(?!\w)")


def _repair_blanket_quoted_json(s):
    """Last-resort recovery for a failure mode found live 2026-08-25 (round 3, Studio testing):
    the model writes the "components" value as Python-dict-repr text using single quotes as
    delimiters, but doesn't escape genuine apostrophes inside real email content ("McDonald's",
    "Here's the uncomfortable truth", "That's because", "aren't just any leads", "they're") — so
    the same character serves as both the structural delimiter and literal text. That's
    genuinely ambiguous to a syntactic parser: both json.loads and ast.literal_eval fail on it
    (the latter with "unterminated string literal"), because nothing in the string itself marks
    which quote is which.

    Heuristic: a delimiter quote is (almost) always adjacent to punctuation, brackets, or
    whitespace — never sandwiched between two letters. A genuine English apostrophe
    (contraction/possessive) is, conversely, (almost) always flanked by a word character on
    both sides. So: promote every quote NOT flanked by \\w on both sides to a JSON double-quote,
    and leave every quote that IS flanked by \\w on both sides alone.

    Verified 2026-08-25 against the real ground-truth "components" string from the user's
    terminal log (6 real email rows, including "McDonald's", "Here's the uncomfortable truth",
    "That's because", "aren't just any leads", "they're") — every structural delimiter was
    correctly promoted and every genuine apostrophe was correctly preserved, and the result
    parsed cleanly with json.loads (see /tmp/test_quote_repair.py for the standalone check).

    Not a general solution — a possessive sitting right at the very edge of a value could still
    fool it — but it's a real improvement over failing outright, and it only ever runs as a last
    resort after both real JSON and Python-literal parsing have already failed."""
    return _STRUCTURAL_QUOTE_RE.sub('"', s)


def _close_truncated_json(s):
    """Recovery for a DIFFERENT failure mode found live 2026-08-25 (round 4, right after the
    round-3 quote fix shipped): the model's "components" string value is genuinely well-formed
    right up until it just... stops, missing the closing brackets/braces needed to finish the
    outer array — e.g. ending "...}}]" when the real structure needed "...}}}]" (one more level:
    close the {"type": "list", "rows": [...]} component object, THEN close the outer components
    array). finish_reason was "tool_calls" (not "length"), so this isn't the API-level truncation
    the max_tokens comment above already covers — the model itself just forgot to finish. Verified
    live: appending the two missing characters by hand made the exact real string in that log
    parse cleanly (all 10 rows, mid-content unescaped apostrophes like "McDonald's" included —
    those are already valid, unescaped JSON, so this bug is orthogonal to round 3's).

    Walks the string tracking a stack of open '{'/'[' delimiters, correctly skipping over
    characters inside JSON string literals (so a literal bracket in real content, like the
    subject line "[Action Required] Verify your ...", isn't miscounted) and honoring '\\'
    escapes. If the string ends mid-literal or with unclosed delimiters, appends exactly what's
    needed to close them, innermost first. Returns None (don't touch it) if the brackets are
    already balanced, or if a closing delimiter appears that doesn't match what's on the stack —
    that's a different kind of malformed that this heuristic has no business guessing at."""
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
    """Recovery for a DIFFERENT structural failure found live 2026-08-25 (round 8): every row
    dict in a "components" list except the very last one was missing its own closing '}' before
    the next row's '{' began — e.g. "...'badge': {'text': '', 'tone': 'warning'}, {'name': ..."
    where a real, correctly-closed row would read "...'warning'}}, {'name': ...". This isn't
    end-of-string truncation (round 4's bug, which _close_truncated_json already fixes) — the
    missing closers are scattered THROUGHOUT the middle of the string, one per row, so simply
    appending closers at the very end can't fix it. It also isn't the round-3 apostrophe/quote
    ambiguity — it's a distinct defect in the STRUCTURE, one level up.

    The repair is principled, not a one-off patch for this exact payload: in both JSON and a
    Python dict literal, a dict's entries are always "key": value pairs — a bare, unlabeled
    '{...}' can NEVER legally follow a comma while still inside a dict (it's only ever valid
    directly inside a LIST, or as the value half of "key": {...}). So walking the string with the
    same bracket-tracking stack _close_truncated_json uses, any time an opening '{' is about to
    be pushed onto the stack while the current top of stack is ALSO '{' (i.e. we're still nested
    inside a dict, not a list) AND the last non-whitespace character emitted so far is a bare
    ',' (not a ':' — which would mean this really is a valid "key": {...} value) — that dict was
    never actually closed. The only sensible reading is that the model meant to end it right
    there: insert a synthetic '}' before that comma (closing the dict, popping it off the
    tracking stack) before processing the new '{'. A dict that already has 'badge': {...} as a
    real, valid nested value is never touched by this, since that '{' follows ':', not a bare ','.

    MUST run on already real-double-quoted text (i.e. after _repair_blanket_quoted_json, never
    on still-single-quoted raw text) — same reasoning as _close_truncated_json's own ordering
    requirement in _repair_candidates() below: only real '"' delimiters (with '\\' escape
    awareness) reliably mark string boundaries; a literal apostrophe like "you've" would desync
    naive quote-tracking on the raw single-quoted text the same way it would for
    _close_truncated_json. Verified 2026-08-25 against the real ground-truth "components" string
    from the user's terminal log (10 real email rows, 9 of them missing their row-closing brace) —
    combined with _close_truncated_json for the payload's ALSO-missing final ']' (this one real
    log had both defects at once), all 10 rows recovered correctly, every apostrophe and the
    literal '[Action Required]' subject-line bracket intact (see /tmp/test_round8_recovery.py)."""
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
    """Every heuristic-repaired variant of a malformed JSON/Python-literal string worth trying
    with json.loads, in order. Shared by normalize_args() and _json_or_literal() below so both
    get the same coverage.

    Round 6 (2026-08-25) found a real string where close-then-repair AND repair-alone both
    failed, even though the payload was honestly recoverable: a genuine end-of-generation
    truncation (round 4's bug — missing closing brackets) landed in the SAME payload as a real
    sender display name containing literal quote marks, written as e.g.
    '\\"McDonald\\'s\\" <no-reply@...>' inside the still-single-quoted "components" string.
    _close_truncated_json() assumes a bare '\"' always marks a real JSON string boundary — true
    once blanket single-quoting has already been repaired away, but NOT true when it's run on
    the still-single-quoted raw text: there, that literal embedded \\" is just content, and
    treating it as a string delimiter desyncs the bracket-tracking for everything after it,
    producing a corrupted "closed" candidate that no further repair can recover (see
    /tmp/test_round6_recovery.py for the exact reproduction — a single misread \\" made the
    closer swallow six more rows' worth of real brackets as "inside a string").

    The fix is ordering: repair the blanket single-quoting FIRST. Once real content is inside
    real double-quoted JSON strings, an embedded \\" is a legitimately escaped double-quote
    character — exactly what _close_truncated_json is designed to skip over correctly — so
    closing THAT text finds the true, correct bracket stack. Both orders are tried (closing
    the raw text first, same as before, in case that path is the one that happens to work for a
    different payload) since a cheap extra json.loads attempt costs nothing once the input is
    already broken.

    Round 8 (2026-08-25) added dict_closed/dict_closed_then_closed: repair-then-close alone
    isn't enough when the payload ALSO has every row dict but the last missing its own closing
    brace (see _insert_missing_dict_closers() above) — that's a structural defect neither the
    quote-repair nor the end-of-string bracket-closer was designed to catch on their own, so it
    needs its own pass, run after quote-repair (same ordering requirement as everything else
    here) and typically followed by one more close-truncated-json pass for whatever trailing
    truncation, if any, is still left over."""
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
    """Same recovery as agent_healthcare.py: double-encoded JSON strings, "true"/"false" strings.
    Also recovers Python-literal-style strings this model sometimes emits instead of real JSON —
    e.g. "[{'type': 'panel', ...}]" (single-quoted, Python dict repr) rather than
    '[{"type": "panel", ...}]" (double-quoted, valid JSON). json.loads rejects the former since
    single quotes aren't valid JSON syntax; ast.literal_eval, which only ever evaluates Python
    literals (no code execution), handles it as a fallback. If those fail because the model
    blanket-used unescaped single quotes for both delimiters and genuine apostrophes in the
    content (round 3's bug — see _repair_blanket_quoted_json() above), or because the model's
    JSON just stops before it's finished (round 4's bug — see _close_truncated_json() above), or
    both together in one payload (round 6's bug — see _repair_candidates() above), a heuristic
    repair runs as a last resort before giving up, trying every repair-order combination.

    Every string this function returns — recovered or original — is passed through
    _unescape_literal_unicode_escapes() then _scrub_surrogates() (see round 7's fix on
    _unescape_literal_unicode_escapes() above for why that order matters), so both fixes apply
    everywhere normalize_args is used (both real tool_call arguments and the plain-text-recovered
    calls from _parse_tool_call_text() below), not just the one path that surfaced each bug."""
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
    and the row count of each list component. Added 2026-08-25 after a run that logged
    "valid and content-verified, done" (no error at all — schema-valid, guardrail-verified real
    content) but the actual live preview showed only 1 row where 10 were expected: the existing
    diagnostic logging only fires on REJECTION, so a render that passes every check but is
    thin/wrong in a way none of our checks catch was previously invisible in the log. This runs
    on every successful render (not just failures) so the next occurrence is diagnosable from
    the log alone instead of needing another back-and-forth just to see the accepted payload."""
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
    value. Returns the parsed value, or the original string unchanged if neither parse
    succeeds — used by _parse_tool_call_text() below, which needs this same recovery applied to
    a whole reply before it even knows whether there's a dict with "name"/"parameters" in it.

    Extended 2026-08-25 (round 5) to also try the same bracket-closing / blanket-quote repairs
    normalize_args() uses on the nested "components" string — found live when a whole reply
    shaped like {"name": "render_view", "parameters": {...}} failed BOTH plain parses here (for
    the same reasons a nested components string can: truncated brackets or blanket-quoted
    apostrophes), fell through to the ast.parse(mode="exec") path below as a bare unrecognized
    dict-literal EXPRESSION (not an ast.Call, since it's JSON-object shaped, not a function
    call), and was silently dropped — the exact repairs already proven to work one level deeper
    were never tried at this outer level. Kept as best-effort: still returns the original string
    unchanged if every repair also fails, same contract as before."""
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


TOOL_NAMES = {"get_gmail_messages", "ask_user", "render_view"}


def _parse_tool_calls_from_text(reply, verbose=False):
    """
    Recovers tool call(s) the model wrote out as plain assistant TEXT instead of using real
    function-calling — the dominant failure mode observed live 2026-08-25 (see run_agent's
    docstring below and studio.py's bug reports): llama-3.1-8b-instruct, once a real tool result
    is sitting in the conversation (or sometimes even before making one at all), stops emitting
    real tool_calls and instead writes out the call(s) it wants to make as text, in one of three
    shapes seen so far:

      1. JSON-ish, one call: {"name": "render_view", "parameters": {...}}
      2. Python-call syntax, one call: render_view(components=[...], heading="...")
      3. Python-call syntax, MULTIPLE calls chained one per line — e.g. writing
         get_gmail_messages(...) and render_view(...) back to back in the same reply, as if
         narrating a plan instead of calling either for real (round two of this bug: the first
         fix here only handled a single recovered call, which meant this exact pattern silently
         fell through to the old generic nudge every single step).

    In every observed case, finish_reason was "stop", not "length" — the model believed it had
    finished a complete response, it just didn't put it where the API expects a tool call to go.
    That means the text isn't truncated, only misplaced, which is what makes recovering it
    reliable rather than a guess: nothing here infers missing content, it only reparses content
    the model already fully generated.

    ast.parse(text, mode="exec") (not "eval") is what makes shape 3 recoverable at all — "eval"
    mode only accepts a single expression and raises SyntaxError on anything with more than one
    top-level statement, which is exactly why a same-day-earlier version of this function (single
    "eval" parse) never recovered a chained reply. "exec" mode parses a whole sequence of
    statements, so each recognized call is pulled out independently.

    Each call's arguments still have to ast.literal_eval cleanly, one call at a time — if one
    call's arguments reference something non-literal (e.g. a "rows" value written as a nested,
    unevaluated get_gmail_messages(...) call rather than real data), only THAT call is skipped;
    every other call in the same reply still gets recovered. That keeps this from ever inventing
    data — it only ever reparses complete, literal data the model already wrote out in full, and
    silently drops anything it can't be sure about rather than guessing.

    Handles the fact that the model's own "JSON" is often not quite valid JSON — values inside
    come out Python-repr style (single-quoted strings, embedded apostrophes) even when the outer
    shape uses double quotes, and a nested value like "components" can arrive as an
    already-stringified array rather than a real nested one. _json_or_literal() plus the
    recursive normalize_args() call on each recovered call's args absorb both of those.

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


_PLACEHOLDER_VALUES = {"subject", "snippet", "from", "sender", "date", "recipient", "to"}
_PLACEHOLDER_PATTERN = re.compile(r"^(email|message)\s*\d+$", re.IGNORECASE)


def _find_placeholder_labels(view):
    """Connector-local safety net, layered on top of guardrails.py's generic fabrication
    check: catches the specific failure mode observed live on 2026-08-24 — the model writing
    a field's own NAME as its VALUE (row name/note, or a field's value, literally "Subject",
    "From", "Email 1", ...) instead of the real fetched text. guardrails.py's check
    deliberately skips single-word values, to avoid false-flagging real short UI text like
    "Open" or "Critical" (see that module's docstring) — which is exactly the gap these
    placeholders exploit, since each one is a single word. This is a small, explicit denylist
    scoped to this connector rather than a change to the shared, generic checker the
    healthcare/GitHub connectors also rely on."""
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
    "Do not write JSON in your reply — call an actual tool (get_gmail_messages, ask_user, or "
    "render_view) using function calling."
)

NO_DATA_YET_NUDGE = (
    "render_view was rejected: you haven't called get_gmail_messages yet, so you have no real "
    "data to show. Call it first, wait for its real result, then call render_view again "
    "using those real values."
)

ALREADY_FETCHED_NUDGE = (
    "get_gmail_messages was not run again: you already have its real result earlier in this "
    "conversation. Don't call it a second time — use those exact real values now in a "
    "render_view call, made through the actual tool-calling mechanism, not written out as text."
)

ALREADY_ASKED_NUDGE = (
    "ask_user was rejected: you already asked one clarifying question in this conversation "
    "and the person just answered it. Do not ask another question — use their answer to call "
    "get_gmail_messages now, then render_view."
)


def _original_user_request(messages):
    """The person's first message in this conversation — used to check a
    proposed ask_user question against what they actually asked, so a lazy
    restatement can be told apart from a real clarifying question. Same as
    agent_healthcare.py's copy of this function."""
    for m in messages:
        if m.get("role") == "user":
            return m.get("content") or ""
    return ""


def _is_lazy_clarifying_question(question, user_request):
    """Same heuristic as agent_healthcare.py — see that file's copy for the
    full reasoning. High word overlap with the original request and almost
    no new vocabulary means the question is just echoing the request back
    instead of actually narrowing it."""
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


def _ask_user_already_used(messages):
    """Same hard cap as the other connectors — see agent_github.py's copy
    of this function for why it's enforced in code, not just prompt text."""
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
    this connector doesn't scope by identity (a Gmail refresh token is
    already scoped to exactly one inbox).
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

    # Tracks the most recent error string each tool raised, so a
    # deterministic failure (bad .env config, an expired/revoked refresh
    # token) can be told apart from a transient one, instead of burning all
    # max_steps retrying something that can never succeed — see
    # agent_slack.py for the live bug this was written to fix.
    tool_failures = {}

    for step in range(max_steps):
        if verbose:
            print(f"  [step {step+1}] calling model...", file=sys.stderr)
        step_start = time.time()
        try:
            resp = client.chat.completions.create(
                model=MODEL,
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
                # Was 700 (same as every other real-connector agent in this repo), raised to
                # 1500 after truncated-JSON failures, then to 2000 (matching agent.py's own
                # budget) after real inbox data — full sender/subject/date strings, not the
                # blank placeholders from the metadataHeaders bug — pushed a 5-email screen
                # over 1500 again, especially when the model picked one 'panel' per email
                # instead of a single compact 'list' (see the system prompt's explicit
                # steering against that above). Raised to 3500 after a real "show my last 20
                # emails" request hit finish_reason="length" mid-\u escape at 2000. Was
                # temporarily 12000 for minimaxai/minimax-m3's invisible reasoning tokens (see
                # MODEL's history note below) — back to 3500 now that MODEL is 8B again, the
                # value 8B has actually been tested against; 8B doesn't reason internally the
                # way minimax-m3 does, so it doesn't need the extra headroom.
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
            # 2026-08-25: this hosted model's OWN chat template chokes on the very
            # NEXT completion request if the message history it's handed contains
            # an assistant turn with more than one tool_calls entry — it fails
            # instantly (before generation even starts) with "Failed to apply
            # prompt template: invalid operation: This model only supports
            # single tool-calls at once!". Seen live: the model bundled a real
            # get_gmail_messages fetch AND a render_view attempt into ONE turn
            # (no separate "calling model" step in between), which got echoed
            # back into messages as one assistant entry with 2 tool_calls, and
            # the model then couldn't re-parse its own prior turn on the next
            # call. Rather than trust this provider/model to honor a
            # parallel-tool-calls-off request flag (not verified to exist for
            # this endpoint), apply this reliability layer's existing "one step
            # = one action" philosophy here too: keep only the first tool_call
            # from an over-eager turn and drop the rest before it's ever built
            # into a message, so the history sent back to the model never has
            # more than one tool_calls entry per assistant turn.
            if verbose:
                dropped = [tc.function.name for tc in tool_calls[1:]]
                print(f"  [step {step+1}] model returned {len(tool_calls)} tool_calls in one turn — keeping only the first ({tool_calls[0].function.name}), dropping {dropped}", file=sys.stderr)
            tool_calls = [tool_calls[0]]

        if not tool_calls:
            reply = (msg.content or "").strip()

            # Try to recover a real tool call the model wrote as plain text
            # before falling back to just nudging it to try again — see
            # _parse_tool_call_text()'s docstring for why this exists and
            # why it's safe (the text is complete, just misplaced).
            recovered_calls = _parse_tool_calls_from_text(reply, verbose=verbose)

            if recovered_calls:
                if verbose:
                    names = [n for n, _ in recovered_calls]
                    print(f"  [step {step+1}] model wrote {len(recovered_calls)} call(s) as plain text instead of real tool calls ({names}) — recovering", file=sys.stderr)
                messages.append({"role": "assistant", "content": msg.content})

                # Processed in the order they appeared — this matters when
                # a reply chains get_gmail_messages then render_view: the
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
                        fabricated = find_fabricated_content(call_args, messages) + find_fabricated_stats(call_args, messages) + _find_placeholder_labels(call_args)
                        if fabricated:
                            if verbose:
                                print(f"  [step {step+1}] recovered render_view — REJECTED: fabricated content not grounded in real fetched data: {fabricated}", file=sys.stderr)
                            render_rejection = fabricated_content_nudge(fabricated)
                            continue
                        if verbose:
                            print(f"  [step {step+1}] recovered render_view — valid and content-verified, done", file=sys.stderr)
                            print(f"  [step {step+1}] accepted render shape: {_render_summary(call_args)}", file=sys.stderr)
                        return {"render": call_args}

                    elif call_name == "get_gmail_messages":
                        if fetched_data:
                            if verbose:
                                print(f"  [step {step+1}] recovered get_gmail_messages, but data was already fetched — skipping the redundant call", file=sys.stderr)
                            continue
                        try:
                            result = get_gmail_messages(**call_args)
                            fetched_data = True
                            if verbose:
                                print(f"  [step {step+1}] recovered get_gmail_messages({call_args}) — ran it for real", file=sys.stderr)
                            fetch_note = "Here is the real result of that call — use these exact values, then call render_view: " + json.dumps(result)
                        except Exception as e:
                            if verbose:
                                print(f"  [step {step+1}] recovered get_gmail_messages raised: {e}", file=sys.stderr)
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
                # the old single generic "use function calling" nudge that
                # gave the model no new information to correct with. If we
                # just fetched real data, hand it over inline so the next
                # attempt has no excuse to fabricate placeholders again; if
                # a render_view attempt was rejected, say exactly why.
                nudge_parts = [p for p in (fetch_note, render_rejection) if p]
                if not nudge_parts:
                    nudge_parts = [ALREADY_FETCHED_NUDGE if fetched_data else NO_TOOL_CALL_NUDGE]
                messages.append({"role": "user", "content": " ".join(nudge_parts)})
                continue

            looks_like_malformed_tool_call = (
                not reply
                or reply.startswith("{")
                or reply.startswith("[")
                or "render_view(" in reply
                or "get_gmail_messages(" in reply
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
                        # Diagnostic-only, added 2026-08-25 after a third live failure whose
                        # rejected 'components' string didn't reproduce when hand-reconstructed
                        # from the (already-formatted-by-jsonschema) rejection message alone —
                        # meaning the true raw text differs from what that message shows in some
                        # way not visible in the terminal. This prints the actual, unmodified
                        # wire content — tc.function.arguments straight from the API, before any
                        # parsing — plus exactly why normalize_args() couldn't recover it, so the
                        # next failure is diagnosable from real ground truth instead of a guess.
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

                fabricated = find_fabricated_content(args, messages) + find_fabricated_stats(args, messages) + _find_placeholder_labels(args)
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
                err_str = str(e)
                result = {"error": err_str}
                ok = False
                if verbose:
                    print(f"  [step {step+1}] {name} raised: {e}", file=sys.stderr)
                if fn and tool_failures.get(name) == err_str:
                    # Same tool just failed with the exact same error it did
                    # last time — a deterministic failure (bad .env config,
                    # a revoked refresh token), not a transient one a retry
                    # could fix. Stop burning steps on it and surface the
                    # real problem immediately.
                    if verbose:
                        print(f"  [step {step+1}] {name} failed identically twice in a row — not retryable, stopping early", file=sys.stderr)
                    return {"error": err_str}
                if fn:
                    tool_failures[name] = err_str
            if fn and ok:
                fetched_data = True
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result),
            })

    return {"error": "hit max_steps without a valid render_view"}


def draft_reply_text(original, sender_name, instruction=None, verbose=True):
    """
    For gmail_site.py's reply interface — a single, plain completion (no
    tools, no schema, no multi-step loop) that writes a suggested reply body
    to one real fetched email. Deliberately NOT the render_view tool-calling
    machinery above: drafting free text doesn't need JSON or a schema, and
    skipping both sidesteps essentially every failure mode this file spent
    2026-08-24 fighting (truncated JSON, malformed brackets, placeholder
    values). This is only ever a suggested starting point — gmail_site.py
    puts the result in an editable textarea and never sends it without a
    person confirming first (see connectors_gmail.send_reply's docstring).

    `original` is a get_message_full() result — real fetched data, so
    there's nothing here to fabricate content ABOUT (unlike render_view,
    there's no separate guardrail on this output: a bad draft is just a bad
    first draft the person edits or discards, not something that reaches
    anyone until they explicitly click Send).

    Returns "" (never raises) on any model failure, so a draft failure
    degrades to an empty textarea instead of blocking the reply feature —
    the person can still just type their own reply.
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
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=400,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        if verbose:
            print(f"  [draft_reply_text] model call failed: {type(e).__name__}: {e}", file=sys.stderr)
        return ""


def translate_to_search_query(text, verbose=True):
    """
    For gmail_site.py's single merged search box — added 2026-08-24 when the
    /copilot route's render_view tool-calling loop (run_agent, above) was
    removed from that file entirely after it hit "max_steps without a valid
    render_view" live. That failure mode belongs to the multi-step
    tool-calling engine (malformed JSON, fabricated placeholders, blown
    token budgets across several forced round-trips) — none of which this
    function is exposed to, because it isn't that engine. This is one
    single-shot completion, no tools, no schema, no loop: given whatever a
    person typed into the search box, it returns real Gmail search syntax
    (or "" on failure), which gmail_site.py then hands to
    get_gmail_messages() the same deterministic way a typed `from:` query
    already works. There's nothing here to fabricate — the output is a
    search query, not content shown as if it were real fetched data, so
    this doesn't need (and doesn't have) a guardrails.py-style grounding
    check the way render_view does.

    Returns "" (never raises) on any model failure — gmail_site.py's caller
    falls back to treating the raw typed text as a plain keyword search,
    so a translation failure never blocks the search box from doing
    something reasonable.
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
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=60,
        )
        out = (resp.choices[0].message.content or "").strip()
        # Strip accidental wrapping quotes — small models sometimes add them
        # even when told not to.
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
    query = sys.argv[1] if len(sys.argv) > 1 else "what unread emails do I have?"
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
