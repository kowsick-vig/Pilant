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

MIGRATED 2026-08-29 from NVIDIA's now-dead free-tier meta/llama-3.1-8b-
instruct (see claude_engine.py's docstring: NVIDIA retired that model on
2026-08-26, which is what actually forced this migration — the user hit a
real 410 Gone calling their live healthcare_dashboard.py Copilot) to Claude,
via the shared claude_engine.py, mirroring every other connector's port the
same day three days earlier (agent_custom.py, agent_gmail.py, agent_slack.py,
agent_github.py, agent_helpdesk.py). This is the sixth and last.

What's GONE, same reasoning as every other port: the entire JSON-repair
stack (_close_truncated_json, _repair_blanket_quoted_json, normalize_args,
_repair_candidates, _scrub_surrogates, _unescape_literal_unicode_escapes),
the _parse_tool_calls_from_text() plain-text-tool-call recovery path, and
the "looks_like_malformed_tool_call" text-sniffing heuristic — none of it
applies to Claude's Messages API, where tool_use blocks arrive as an
already-parsed, schema-validated dict (block.input).

Like agent_gmail.py/agent_slack.py (and UNLIKE agent_helpdesk.py/
agent_github.py), this connector's original NVIDIA-era SYSTEM prompt already
treated a genuine conversational reply ("hi", "thanks", "what can you do?")
as a valid, final outcome rather than something to force into a tool call —
so this port keeps that behavior via force_tool_choice=False and an
on_no_tool_call hook, unchanged in spirit from before.

No first_turn/DynamisOS-comparison machinery here — that's a studio.py-only
feature (see CONNECTORS' "first_turn" flag in studio.py) for connectors
wired into Studio's own chat. This connector is deliberately NOT in Studio
at all — it's the standalone healthcare_dashboard.py (port 5006) and
healthcare_site.py (port 5005) apps' own Copilot — so it never had that
paragraph/gate before this migration and doesn't need it now either.

run_agent()'s calling convention (fresh call vs. messages=/fetched_data=
resume-after-clarify) is UNCHANGED, so healthcare_dashboard.py's and
healthcare_site.py's /copilot/send routes need ZERO changes to call this
Claude-backed version exactly like the old NVIDIA-backed one.
"""

import json
import re
import sys

from schema import UI_SCHEMA
from connectors_healthcare import get_appointments
from validation import validate_view
from renderer import render_html
from guardrails import find_fabricated_content, find_fabricated_stats, fabricated_content_nudge
import claude_engine as ce

TOOLS = [
    {
        "name": "get_appointments",
        "description": (
            "Fetch appointments for the clinic's configured sandbox test patients, "
            "optionally filtered by status: booked (upcoming, not yet checked in), "
            "checked_in (arrived, waiting), fulfilled (visit completed), cancelled, "
            "or no_show."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["booked", "checked_in", "fulfilled", "cancelled", "no_show"],
                }
            },
        },
    },
    {
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
            "the components required to answer the request — no unrelated appointments, "
            "no default dashboard."
        ),
        "input_schema": UI_SCHEMA,
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
    "Once you've called get_appointments for a data request, you must finish with render_view — "
    "never fall back to a plain-text explanation instead of showing a screen just because the "
    "real results don't perfectly match what was asked (e.g. none of the fetched appointments "
    "are dated 'today' in this sandbox, or a status filter came back thin). A person asking a "
    "data question wants to SEE the real data, not read a paragraph about it. In that situation, "
    "still call render_view with the real appointments you actually fetched, and use the "
    "heading/a short note to be honest about the mismatch — e.g. heading 'No appointments today "
    "— here's what's booked' rather than 'Nothing to show'. Only skip render_view entirely if "
    "get_appointments came back completely empty (render a screen that plainly says so, e.g. "
    "heading 'No booked appointments found' with no rows) — never replace a real render with a "
    "conversational reply. Plain text is reserved for messages that aren't a data request at "
    "all — see the next paragraph.\n\n"
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


# ---- Guardrails — all model-agnostic, ported unchanged from the NVIDIA version ----

_PLACEHOLDER_VALUES = {"patient", "provider", "status", "time", "reason", "date"}
_PLACEHOLDER_PATTERN = re.compile(r"^(patient|appointment|provider)\s*\d+$", re.IGNORECASE)


def _find_placeholder_labels(view):
    """Connector-local safety net, layered on top of guardrails.py's generic fabrication
    check: catches the model writing a field's own NAME as its VALUE (row name/note, or a
    field's value, literally "Patient", "Provider", "Patient 1", ...) instead of the real fetched
    text. guardrails.py's check deliberately skips single-word values, to avoid false-flagging
    real short UI text like "Booked" or "Critical" — which is exactly the gap these placeholders
    exploit, since each one is a single word. Ported unchanged from the pre-migration NVIDIA
    version (see _nvidia_backup/agent_healthcare.py) — this guardrail is model-agnostic."""
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
    is a good, cheap signal of "just echoed it back". Ported unchanged from
    the pre-migration NVIDIA version.
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


def _make_dispatch(seed_fetched_data, verbose):
    """Builds a fresh dispatch() closure for ONE run_agent() call, with its own private
    fetched-data state — see agent_gmail.py's copy of this function for the full reasoning.
    No tool_failures repeated-identical-failure tracking here — the pre-migration NVIDIA
    version never had it for this connector either (get_appointments talks to a real Epic
    sandbox, but the original file didn't distinguish a transient vs. deterministic failure
    for it), so this port preserves that as-is rather than adding a guardrail the original
    never had."""
    state = {"fetched_data": seed_fetched_data}

    def dispatch(name, args, tc_id, prior_messages, _seed_fetched_data_unused):
        if name == "get_appointments":
            if state["fetched_data"]:
                return ce.ToolOutcome(tool_result=ALREADY_FETCHED_NUDGE)
            try:
                result = get_appointments(**args)
            except Exception as e:
                if verbose:
                    print(f"  [dispatch] get_appointments raised: {e}", file=sys.stderr)
                return ce.ToolOutcome(tool_result=json.dumps({"error": str(e)}))
            state["fetched_data"] = True
            if verbose:
                print(f"  [dispatch] get_appointments({args}) — {len(result) if isinstance(result, list) else '?'} appointment(s)", file=sys.stderr)
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


def _on_no_tool_call(text, stop_reason):
    """A genuine plain-text reply is a normal, expected outcome for this connector (small
    talk, thanks, "what can you do?") — see SYSTEM's own instruction to reply in plain text
    for non-data-request messages, and force_tool_choice=False below which lets Claude
    actually choose to do this. Ported in spirit from the pre-migration NVIDIA version, which
    supported the same conversational-reply behavior via tool_choice='auto' plus its own
    (now-removed) malformed-tool-call text-sniffing heuristic — Claude's tool_use blocks are
    structurally distinct from plain text turns, so no heuristic is needed here at all."""
    if text:
        return ce.ToolOutcome(final={"text": text})
    return None


def run_agent(user_request=None, max_steps=8, verbose=True, messages=None, fetched_data=False):
    """
    Same calling convention as before and as every other connector — see this module's
    docstring and claude_engine.run_claude_agent's docstring for the full contract. No `user`
    argument here — this connector doesn't scope by identity, same as before.
    """
    dispatch = _make_dispatch(fetched_data, verbose)
    return ce.run_claude_agent(
        dispatch,
        system=SYSTEM,
        tools=TOOLS,
        max_steps=max_steps,
        verbose=verbose,
        messages=messages,
        user_request=user_request,
        fetched_data=fetched_data,
        on_no_tool_call=_on_no_tool_call,
        force_tool_choice=False,
    )


if __name__ == "__main__":
    from pathlib import Path
    import webbrowser

    query = sys.argv[1] if len(sys.argv) > 1 else "what appointments need attention right now?"
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
