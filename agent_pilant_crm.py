"""
Composition Engine wired to connectors_pilant_crm.py — Pilant's own real,
internal CRM/finance data (customers, sales pipeline, revenue, expenses).
Added 2026-08-30 alongside pilant_crm.py's own AI Copilot, built directly
on claude_engine.py from the start — there was never an NVIDIA-era version
of this connector to migrate, unlike every other one in this project.

Unlike every other connector here, connectors_pilant_crm.py's data isn't a
demo standing in for an external API and it isn't sample data either — see
that module's own docstring: it's Pilant's REAL business records, real
because someone actually typed them into pilant_crm.py's forms. This
agent's job is to answer real questions about that real data (and reject,
same as every other connector, anything render_view tries to show that
doesn't trace back to it) — never to invent numbers that merely sound
plausible for a CRM/finance screen.

force_tool_choice=False (like agent_gmail.py/agent_healthcare.py, unlike
agent_helpdesk.py/agent_github.py): genuine small talk ("hi", "thanks")
gets a real plain-text reply instead of being forced through a data fetch
that has nothing to answer.
"""

import json
import re
import sys

from schema import UI_SCHEMA
from connectors_pilant_crm import list_customers, list_deals, list_revenue, list_expenses, dashboard_summary
from validation import validate_view
from guardrails import find_fabricated_content, find_fabricated_stats, fabricated_content_nudge
import claude_engine as ce

TOOLS = [
    {
        "name": "get_crm_data",
        "description": (
            "Fetch the current real Pilant CRM data in one call: every customer, every sales "
            "pipeline deal (with stage and amount), every logged revenue entry, every logged "
            "expense, and a computed summary (totals, pipeline value by stage, this month's "
            "revenue/expenses/net). No arguments — always returns everything currently on "
            "record, since a CRM question often needs more than one dataset at once (e.g. "
            "'which active customers have an open deal?')."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "ask_user",
        "description": (
            "Ask the person a single clarifying question before doing anything else. Use this "
            "ONLY when the request is genuinely ambiguous in a way that would change what "
            "you'd show. The question must NAME the specific ambiguity and offer concrete "
            "options. Do NOT use this for requests you can reasonably interpret yourself. Ask "
            "AT MOST ONE question per request: after the person answers, proceed straight to "
            "fetching data and rendering."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"question": {"type": "string", "description": "One short, specific question."}},
            "required": ["question"],
        },
    },
    {
        "name": "render_view",
        "description": (
            "Emit the final generated screen for the person's request. Call this exactly once, "
            "as the last step, after fetching real data with get_crm_data. Include ONLY the "
            "components required to answer the request — no unrelated customers/deals, no "
            "default dashboard."
        ),
        "input_schema": UI_SCHEMA,
    },
]

SYSTEM = (
    "You are Pilant's own internal Composition Engine, embedded as an AI Copilot in Pilant's "
    "real CRM/finance tracker. You're having a normal conversation with Pilant's own team — "
    "some of what they say will be a real request to see customer/pipeline/revenue/expense "
    "data, and some of it will just be talk.\n\n"
    "If they're asking about customers, deals, pipeline stages, revenue, expenses, or the "
    "business's numbers generally — that's a data request: call get_crm_data (it always "
    "returns everything currently on record, so you never need to call it more than once), "
    "then call render_view exactly once with a screen that shows ONLY what they asked for — no "
    "unrelated records, no default dashboard. Use judgment on badge tones: deal stage 'Won' -> "
    "'good', 'Negotiation'/'Proposal' -> 'warning', 'Lost' -> 'critical', 'Lead'/'Qualified' -> "
    "'default'; customer status 'active' -> 'good', 'churned' -> 'critical', 'lead'/'prospect' "
    "-> 'default'. Do not pad the screen with anything unrequested.\n\n"
    "Once you've called get_crm_data for a data request, you must finish with render_view — "
    "never fall back to a plain-text explanation instead of showing a screen. A person asking "
    "'what's our open pipeline worth' or 'who are our active customers' wants to SEE the real "
    "numbers/rows, not read a paragraph summarizing them — even a single figure like an open "
    "pipeline total should render as a stat_grid/panel, not prose. If the relevant data is "
    "completely empty (nothing logged yet in that category), still call render_view with an "
    "honest empty state — e.g. heading 'No deals logged yet' with no rows — never replace a "
    "real render with a conversational reply. Plain text is reserved for messages that aren't a "
    "data request at all — see the next paragraph.\n\n"
    "If they're NOT asking for CRM data — a greeting, thanks, small talk, a question about what "
    "you can do, or anything else conversational — do not call any tool. Just reply normally in "
    "plain text, like a helpful, friendly assistant would. Keep these replies short and "
    "natural, and when it fits, mention you can pull up real customer/pipeline/revenue/expense "
    "data if they want to see something specific.\n\n"
    "Only call ask_user first if a genuine data request is ambiguous in a way that would change "
    "what you'd show — and only once. After the person answers, proceed straight to "
    "get_crm_data and render_view; do not ask a second question in the same request, and do not "
    "ask about things you could reasonably infer yourself. A clarifying question must name the "
    "actual options — never just repeat the person's own request back as a question.\n\n"
    "Critical: render_view's fields must contain real, literal data — the actual customers, "
    "deals, revenue entries, and expenses get_crm_data returned. NEVER invent a customer, a "
    "deal amount, a revenue figure, or an expense, and never write placeholder/template syntax "
    "such as {{get_crm_data(...)}}. If get_crm_data comes back with an empty list for something "
    "(a brand-new install, or nothing logged yet in that category), render that honestly — a "
    "real empty state, never invented records to fill the gap. 'components' must be an actual "
    "JSON array of component objects, not a string."
)


_PLACEHOLDER_VALUES = {"customer", "company", "deal", "stage", "status", "amount", "category", "source", "note"}
_PLACEHOLDER_PATTERN = re.compile(r"^(customer|deal|expense|revenue)\s*\d+$", re.IGNORECASE)


def _find_placeholder_labels(view):
    """Same connector-local safety net every other connector has, layered
    on top of guardrails.py's generic fabrication check — see
    agent_gmail.py's copy of this function for the full reasoning. Field
    names specific to this connector's own data (customer/company/deal/
    stage/status rather than subject/snippet/from)."""
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
    "render_view was rejected: you haven't called get_crm_data yet, so you have no real data "
    "to show. Call it first, wait for its real result, then call render_view again using those "
    "real values."
)

ALREADY_FETCHED_NUDGE = (
    "get_crm_data was not run again: you already have its real result earlier in this "
    "conversation — it always returns everything on record, so a second call wouldn't add "
    "anything new. Use those exact real values now in a render_view call."
)

ALREADY_ASKED_NUDGE = (
    "ask_user was rejected: you already asked one clarifying question in this conversation and "
    "the person just answered it. Do not ask another question — use their answer to call "
    "get_crm_data now, then render_view."
)


def _is_lazy_clarifying_question(question, user_request):
    """Same heuristic every other connector uses — see agent_gmail.py's
    copy of this function for the full reasoning."""
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
    "of narrowing it — that doesn't clarify anything. Either ask a real, narrowing question "
    "with actual options in it, or skip ask_user entirely and call get_crm_data with your best "
    "reasonable interpretation."
)


def _make_dispatch(seed_fetched_data, verbose):
    """Builds a fresh dispatch() closure for ONE run_agent() call, with its
    own private fetched-data state — see agent_gmail.py's copy of this
    function for the full reasoning."""
    state = {"fetched_data": seed_fetched_data}

    def dispatch(name, args, tc_id, prior_messages, _seed_fetched_data_unused):
        if name == "get_crm_data":
            if state["fetched_data"]:
                return ce.ToolOutcome(tool_result=ALREADY_FETCHED_NUDGE)
            data = {
                "customers": list_customers(),
                "deals": list_deals(),
                "revenue": list_revenue(),
                "expenses": list_expenses(),
                "summary": dashboard_summary(),
            }
            state["fetched_data"] = True
            if verbose:
                print(
                    f"  [dispatch] get_crm_data() — {len(data['customers'])} customer(s), "
                    f"{len(data['deals'])} deal(s), {len(data['revenue'])} revenue entr(y/ies), "
                    f"{len(data['expenses'])} expense(s)",
                    file=sys.stderr,
                )
            return ce.ToolOutcome(tool_result=json.dumps(data, default=str))

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
    """A genuine plain-text reply is a normal, expected outcome for this
    connector — see SYSTEM's own instruction to reply in plain text for
    non-data-request messages, and force_tool_choice=False below which
    lets Claude actually choose to do this."""
    if text:
        return ce.ToolOutcome(final={"text": text})
    return None


def run_agent(user_request=None, max_steps=8, verbose=True, messages=None, fetched_data=False):
    """Same calling convention as every other connector — see this
    module's docstring and claude_engine.run_claude_agent's docstring for
    the full contract. No `user` argument — this is Pilant's own internal
    data, not scoped per external identity."""
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
    from renderer import render_html

    query = sys.argv[1] if len(sys.argv) > 1 else "what's our open pipeline worth?"
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
