"""
Generic content-verification guardrail, shared across every Composition
Engine connector (agent_healthcare.py, agent_github.py, and any future one).

Backstory: agent_healthcare.py originally had its own bespoke version of
this check — _extract_real_appointments / _real_name_pool / _name_is_real /
_find_fabricated_names — built specifically around appointments having a
"patient" and a "provider" name. It works, but it only knows how to check
one shape of data. agent_github.py (issues, with a title/author/labels
instead of a patient/provider) had no equivalent guardrail at all, which
means a fabricated issue title could currently slip through render_view
there undetected.

This module replaces the domain-specific version with one that doesn't know
or care what kind of data it's checking. The idea is the same either way:
after get_appointments/get_github_issues/whatever-comes-next actually
returns real data, render_view's rows/fields must only contain content that
traces back to that real result — never something the model invented. The
generalization is: instead of matching against a "real name pool" built
from patient/provider fields specifically, it flattens *every* string value
out of *every* tool result returned so far (whatever shape that data is)
into a per-record pool, and checks candidate content against that.

Trade-off worth being honest about: the original name-matcher only ever
checked things that looked like a person's name (via a name-shaped regex),
so it never had to worry about false-positiving on short generic words. This
generic version instead just requires a candidate to have at least two word
tokens before it's checked at all — which is a coarser filter, and a
two-word coincidence (a made-up value that happens to share two common
words with something real) is more likely to slip past undetected than the
old regex-gated version was for names specifically. It trades some of that
precision for working across arbitrary connectors instead of just one.
"""

import json
import re

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Skip single-word values (statuses like "Open", "Critical", "Bug") — with
# only one token there's nothing meaningful left to check, and single common
# words are exactly the kind of thing that would false-positive constantly
# if we tried to verify them against real data.
_MIN_CHECKABLE_TOKENS = 2


def _tokens(s):
    return set(_TOKEN_RE.findall((s or "").lower()))


def extract_tool_results(messages):
    """
    Every real tool result recorded in this conversation so far, in the
    order they happened — the raw, real result of whatever tool(s) were
    actually called. Deliberately generic: this doesn't check which
    function produced a given result, so it works for get_appointments,
    get_github_issues, or any tool a future connector adds, with no changes
    here.

    Handles BOTH wire formats live in this codebase during the 2026-08-26
    NVIDIA->Claude migration (see claude_engine.py's docstring): the
    OpenAI/NVIDIA shape every not-yet-migrated connector still uses —
    {"role": "tool", "content": <json string>} — and Claude's shape every
    migrated connector uses instead, where a tool result is a content BLOCK
    inside a {"role": "user", "content": [...]} message:
    {"type": "tool_result", "tool_use_id": ..., "content": <json string>}.
    Both eventually get to the same thing: a JSON string this function
    decodes. This dual handling can be simplified back down to just the
    Claude shape once every connector has been migrated.
    """
    results = []
    for m in messages:
        role = m.get("role")
        if role == "tool":
            try:
                results.append(json.loads(m.get("content") or "null"))
            except json.JSONDecodeError:
                continue
        elif role == "user" and isinstance(m.get("content"), list):
            for block in m["content"]:
                block_type = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
                if block_type != "tool_result":
                    continue
                content = block.get("content") if isinstance(block, dict) else getattr(block, "content", None)
                if not isinstance(content, str):
                    continue
                try:
                    results.append(json.loads(content))
                except json.JSONDecodeError:
                    continue
    return results


def _flatten_strings(value, out):
    """Recursively collect every string (and numeric/bool, stringified —
    e.g. a GitHub issue number) leaf value out of an arbitrary JSON-shaped
    value. This is what makes the check connector-agnostic — it doesn't
    know or care whether a field is called 'patient', 'title', or 'author',
    it just looks at every real value anywhere inside one real fetched
    record."""
    if isinstance(value, bool):
        out.append(str(value))
    elif isinstance(value, (str, int, float)):
        out.append(str(value))
    elif isinstance(value, list):
        for v in value:
            _flatten_strings(v, out)
    elif isinstance(value, dict):
        for v in value.values():
            _flatten_strings(v, out)


def records_from_tool_results(tool_results):
    """
    Split each tool result into individual 'records' — one item of a list
    result (one appointment, one GitHub issue), or the whole result if it
    isn't a list. Checking a candidate against one record at a time, instead
    of against everything flattened into one big blob, keeps the same
    precision the original healthcare-specific guardrail had: a fabricated
    value can't be 'explained' by stitching together words that actually
    came from two different, unrelated real records.
    """
    records = []
    for result in tool_results:
        items = result if isinstance(result, list) else [result]
        for item in items:
            strs = []
            _flatten_strings(item, strs)
            if strs:
                records.append(" ".join(strs))
    return records


_OVERLAP_RATIO = 0.5
_MIN_OVERLAP_TOKENS = 2


def content_is_grounded(candidate, records):
    """
    True if candidate plausibly came from one real record.

    Checked two ways: an exact substring match either direction (fast path
    for the common case — a value copied verbatim), or token overlap
    against one record's tokens. Overlap, not a strict subset, is
    deliberate: real UI text usually wraps a fetched value in connective
    words the model adds while composing a sentence ('with Dr. Sarah
    Bennett', 'opened by jsmith-dev · #482') that will never appear in the
    raw fetched JSON itself. Requiring every token to match would flag that
    normal, honest composition as fabrication. Requiring at least half the
    candidate's tokens (and at least two of them) to come from ONE real
    record is forgiving of that connective wrapping while still catching
    genuine invention — a fabricated name or title has close to zero
    overlap with any real record, not partial overlap.

    Also true (not flagged) when there's nothing to check against yet — an
    empty-state screen, or a candidate with no real word tokens at all
    (rare given the caller's _MIN_CHECKABLE_TOKENS filter).
    """
    c = (candidate or "").strip().lower()
    if not c or not records:
        return True
    c_tokens = _tokens(c)
    if not c_tokens:
        return True
    for record in records:
        r = record.lower()
        if c in r or r in c:
            return True
        overlap = c_tokens & _tokens(record)
        if len(overlap) >= _MIN_OVERLAP_TOKENS and len(overlap) / len(c_tokens) >= _OVERLAP_RATIO:
            return True
    return False


# Which render_view fields actually carry literal, copy-from-source content
# worth checking. Deliberately narrow: `rows[].action`, `stats[].value`, and
# badge text are either fixed UI labels ("Resolve", "View details") or
# derived aggregates (a count, a percentage) that legitimately won't appear
# verbatim in the raw fetched data — checking those would just produce
# false positives, not catch real fabrication. `rows[].url` (added
# 2026-08-26 alongside schema.py's optional row url field) IS checked here
# — a link is exactly the kind of thing that must be copied verbatim from
# a real fetched record, never invented, since an invented url could point
# anywhere.
def find_fabricated_content(view, messages):
    """
    Generic replacement for agent_healthcare.py's original
    _find_fabricated_names: scans render_view's row/field content for
    anything that doesn't trace back to a real record returned by a tool
    call so far in this conversation — the signature of the model inventing
    data instead of using what it actually fetched, regardless of which
    connector or domain produced that data.
    """
    tool_results = extract_tool_results(messages)
    records = records_from_tool_results(tool_results)

    bad = []
    for comp in (view.get("components") or []):
        if not isinstance(comp, dict):
            continue
        for row in (comp.get("rows") or []):
            if not isinstance(row, dict):
                continue
            for key in ("name", "note", "url"):
                val = row.get(key)
                if isinstance(val, str) and len(_tokens(val)) >= _MIN_CHECKABLE_TOKENS:
                    if not content_is_grounded(val, records):
                        bad.append(val)
        for field in (comp.get("fields") or []):
            if not isinstance(field, dict):
                continue
            val = field.get("value")
            if isinstance(val, str) and len(_tokens(val)) >= _MIN_CHECKABLE_TOKENS:
                if not content_is_grounded(val, records):
                    bad.append(val)
        # Added 2026-09-05 alongside schema.py's new "data_table" component
        # type — table_rows[].values is data_table's own shape (there's no
        # per-cell label the way "fields" has, just an ordered list of cell
        # strings matching "columns"), so it needs its own small loop here;
        # "timeline" and "metric" needed no such addition since they reuse
        # rows/stats verbatim and are already covered above / by
        # find_fabricated_stats below respectively.
        for row in (comp.get("table_rows") or []):
            if not isinstance(row, dict):
                continue
            for val in (row.get("values") or []):
                if isinstance(val, str) and len(_tokens(val)) >= _MIN_CHECKABLE_TOKENS:
                    if not content_is_grounded(val, records):
                        bad.append(val)

    # de-dupe while preserving order
    seen = set()
    return [b for b in bad if not (b in seen or seen.add(b))]


# 2026-08-25: `stats[].value` was deliberately left out of find_fabricated_content
# above because most stat_grid values are legitimate DERIVED aggregates (a count, a
# percentage) that honestly won't appear verbatim anywhere in the raw fetched data —
# grounding-checking every stat would just false-positive on things like "3 unread"
# computed correctly from real records. That reasoning holds for counts/percentages,
# but it created a blind spot for a different, worse failure: seen live on the Gmail
# connector, a "summarize what came in from finance this week" request produced a
# stat_grid literally titled "Finance Summary" with invented figures ("TOTAL $10,000",
# "DUE TODAY $500") — numbers with no possible source, since none of these connectors'
# fetched data (Gmail messages, Slack messages, GitHub issues, helpdesk tickets) has
# any monetary field at all. A derived count is a legitimate transformation of real
# data; a specific dollar figure the connector's data literally cannot produce is
# invention. This narrower, separate check targets only that second case — stat
# values that look like a specific monetary/financial figure — and requires those
# (and only those) to trace back to real fetched content, leaving ordinary derived
# counts and percentages exempt exactly as before.
_CURRENCY_VALUE_RE = re.compile(r"[$£€¥]\s?\d")
_FINANCIAL_LABEL_WORDS = {
    "total", "due", "balance", "owed", "amount", "invoice", "payment", "cost",
    "price", "spent", "revenue", "budget", "paid", "refund", "subtotal", "fee",
}


def find_fabricated_stats(view, messages):
    """
    Companion to find_fabricated_content, scoped to stat_grid components only.
    Flags a stat whose value looks like a specific monetary/financial figure
    (a currency symbol followed by digits, or a label like 'Total'/'Due'/
    'Balance' paired with a number) unless it traces back to real fetched
    content. Ordinary derived stats (counts, percentages, non-financial
    labels) are left untouched — same trade-off find_fabricated_content
    documents above, just drawing the line at "financial-looking" instead of
    "any stat at all".
    """
    tool_results = extract_tool_results(messages)
    records = records_from_tool_results(tool_results)

    bad = []
    for comp in (view.get("components") or []):
        if not isinstance(comp, dict):
            continue
        for stat in (comp.get("stats") or []):
            if not isinstance(stat, dict):
                continue
            val = stat.get("value")
            label = stat.get("label") or ""
            if not isinstance(val, str) or not val.strip():
                continue
            label_words = set(re.findall(r"[a-z]+", label.lower()))
            looks_financial = bool(_CURRENCY_VALUE_RE.search(val)) or bool(label_words & _FINANCIAL_LABEL_WORDS)
            if not looks_financial:
                continue
            combined = f"{label} {val}".strip()
            if not content_is_grounded(val, records) and not content_is_grounded(combined, records):
                bad.append(f"{label}: {val}" if label else val)

    seen = set()
    return [b for b in bad if not (b in seen or seen.add(b))]


# Added 2026-08-27 alongside schema.py's new "suggestions" component type
# (customer-360 primitive): find_fabricated_content above deliberately only
# scans `rows`/`fields`, so a "suggestions" component's own `suggestions`
# array is exempt from it by construction — a recommended action is Claude's
# judgment, not a fact, and shouldn't be rejected just for not appearing
# verbatim in fetched data the way a row's name/note has to. But that carve-out
# is deliberately narrow, not a blanket exemption: the exact failure already
# seen live on the Gmail connector (see find_fabricated_stats' docstring
# above — an invented "$10,000 TOTAL" stat) is just as possible inside a
# suggestion's free text ("reach out — they've spent $4,200 this year"), so
# this check reuses find_fabricated_stats' narrower "does this look like a
# specific monetary figure" test against suggestion text specifically,
# rather than exempting suggestions from fact-checking altogether.
# Extracts the actual monetary TOKEN ("$4,200", "€42.00"), not just detects
# one's presence like _CURRENCY_VALUE_RE above — find_ungrounded_suggestion_facts
# needs the token itself, checked in isolation, because a suggestion is a
# full free-text sentence ("Order #1001 totalling $42.00 is unfulfilled —
# check on it") and content_is_grounded's token-overlap test is calibrated
# for a short field value, not a whole sentence: most of a sentence's words
# ("totalling", "check", "on", "it") are the model's own connective prose
# and will never appear in raw fetched JSON, so checking the full sentence
# against records would false-positive on genuinely grounded suggestions
# (caught live while building this — see the module's test coverage).
# Checking just the extracted figure avoids that: content_is_grounded's
# substring fast-path correctly matches "$42.00" against a record
# containing "total": "$42.00 USD".
_CURRENCY_TOKEN_RE = re.compile(r"[$£€¥]\s?\d[\d,]*\.?\d*")


def find_ungrounded_suggestion_facts(view, messages):
    """
    Companion to find_fabricated_stats, scoped to `suggestions` components.
    Flags a suggestion string containing what looks like a specific
    monetary figure unless that exact figure traces back to real fetched
    content — same narrow "financial-looking" trigger as
    find_fabricated_stats, for the same reason (a suggestion reasoning
    generically about real data elsewhere on the screen is legitimate; a
    suggestion inventing its own dollar figure is the same failure mode as
    an invented stat). Checks each monetary token in the suggestion
    individually, not the sentence as a whole — see _CURRENCY_TOKEN_RE's
    comment for why that distinction matters here.
    """
    tool_results = extract_tool_results(messages)
    records = records_from_tool_results(tool_results)

    bad = []
    for comp in (view.get("components") or []):
        if not isinstance(comp, dict) or comp.get("type") != "suggestions":
            continue
        for s in (comp.get("suggestions") or []):
            if not isinstance(s, str):
                continue
            for token in _CURRENCY_TOKEN_RE.findall(s):
                if not content_is_grounded(token, records):
                    bad.append(s)
                    break

    seen = set()
    return [b for b in bad if not (b in seen or seen.add(b))]


_CLAIM_BEARING_TYPES = {"alert", "status_badge", "empty_state", "error_state"}


def find_ungrounded_alert_claims(view, messages):
    """
    Sibling of find_ungrounded_suggestion_facts, scoped to schema.py's
    2026-09-05 "alert" component type, and — as of 2026-09-06's round-3
    types — three more that share the exact same shape of risk:
    status_badge, empty_state, and error_state. All four carry freeform
    title/subtitle prose synthesizing a real condition ("3 issues are
    blocked and overdue", "No blocked issues right now", "Gmail isn't
    accessible right now") rather than a copied field value — the exact
    shape of claim find_ungrounded_suggestion_facts already exists to
    police for suggestions, so this reuses the same narrow "financial-
    looking figure" trigger and the same per-token (not whole-sentence)
    check, for the same reason: most of a claim sentence's words are the
    model's own connective prose and would false-positive against raw
    fetched JSON if checked as a whole string. Kept as one function
    (rather than four near-duplicates) since all four types pose
    identically-shaped risk — only _CLAIM_BEARING_TYPES needs to grow if
    a future type adds the same kind of freeform claim.

    Also scans comp['badge']['text'] for these same types — added after a
    test caught a real gap: status_badge's docstring in schema.py says the
    badge IS the status claim itself (e.g. badge.text could read "$9,999
    overdue"), not just supporting decoration the way a badge attached to
    a list row is. Checking only title/subtitle would have let a
    fabricated figure hide in the one field most likely to carry it for
    this type.
    """
    tool_results = extract_tool_results(messages)
    records = records_from_tool_results(tool_results)

    bad = []
    for comp in (view.get("components") or []):
        if not isinstance(comp, dict) or comp.get("type") not in _CLAIM_BEARING_TYPES:
            continue
        badge = comp.get("badge")
        badge_text = badge.get("text") if isinstance(badge, dict) else None
        for text in (comp.get("title"), comp.get("subtitle"), badge_text):
            if not isinstance(text, str):
                continue
            for token in _CURRENCY_TOKEN_RE.findall(text):
                if not content_is_grounded(token, records):
                    bad.append(text)
                    break

    seen = set()
    return [b for b in bad if not (b in seen or seen.add(b))]


def fabricated_content_nudge(bad_values):
    quoted = ", ".join(repr(b) for b in bad_values)
    return (
        "render_view was rejected: it contains value(s) that don't match any real data "
        f"actually returned by a tool call so far this conversation — {quoted}. Do not invent "
        "names, titles, or other specific details — use ONLY exact values already present in "
        "the tool result(s) returned so far in this conversation. If the real data is empty for "
        "what was requested, say so honestly in the heading/meta instead of fabricating content."
    )
