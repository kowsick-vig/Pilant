"""
Composition Engine, connected to connectors_retail.py — which as of
2026-08-22 is a REAL Shopify connector, not fabricated data (see that
file's docstring for the SHOPIFY_STORE_URL / SHOPIFY_ACCESS_TOKEN setup
it needs). Same schema, same bounded tool-calling loop, same guardrails as
agent_github.py — only the domain changed, from "security ops" / "GitHub
issues" to "a Shopify store's order and inventory desk." This is the piece
that gets embedded into retail_site.py's dummy admin page (the page chrome
around the widget is still decorative demo dressing — see that file — but
the Ask Ops widget itself now answers from a real store).
"""

import os
import json
import sys
import time
from pathlib import Path

from openai import OpenAI

from schema import UI_SCHEMA
from connectors_retail import get_orders, get_inventory
from validation import validate_view

env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=os.environ["NVIDIA_API_KEY"],
    # Without an explicit timeout, a stalled connection can hang for 30+
    # minutes (SDK default ~10min per attempt x 2 retries) with zero output —
    # looks like "the model is thinking" but is actually a dead network path.
    timeout=45.0,
    # 0, not the SDK's own retry count — see agent_github.py's client
    # comment for the full reasoning: the SDK's internal retry compounds
    # with our own two retry layers and turns a single slow call into a
    # multi-minute wait instead of adding real resilience.
    max_retries=0,
)

MODEL = "meta/llama-3.1-8b-instruct"  # swapped 2026-08-22: gpt-oss-120b was hanging with zero bytes back on NVIDIA's backend (confirmed via raw curl, unrelated to our code) — this model responded correctly and fast on the same account/connection. Re-check tool-call reliability if you swap back.

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_orders",
            "description": (
                "Fetch recent orders from the connected Shopify store, optionally filtered by "
                "fulfillment status. Each order also includes financial_status and has_refund "
                "for cases the status filter alone doesn't capture."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["unfulfilled", "partial", "fulfilled", "restocked", "cancelled"],
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_inventory",
            "description": (
                "Fetch inventory items (Shopify product variants) from the connected store, "
                "optionally filtered to only low/out-of-stock items and/or by category "
                "(Shopify's product_type field — this varies per store, so use whatever "
                "category the person's request actually names)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "low_stock_only": {"type": "boolean"},
                    "category": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "render_view",
            "description": (
                "Emit the final generated screen for the user's request. Call this exactly once, "
                "as the last step, after fetching whatever data you need. Include ONLY the "
                "components required to answer the request — no unrelated data, no default dashboard."
            ),
            "parameters": UI_SCHEMA,
        },
    },
]

SYSTEM = (
    "You are the Composition Engine inside Pilant, embedded in a store's internal ops "
    "platform, backed by a real connected Shopify store. A staff member has typed a request. "
    "Call get_orders and/or get_inventory with whatever filters answer it, then call "
    "render_view exactly once with a screen that shows ONLY what they asked for — no "
    "unrelated orders or stock, no default dashboard. Use judgment to pick badge tones: "
    "cancelled orders, orders with has_refund true, and out-of-stock inventory → tone "
    "'critical'; unfulfilled orders and low-stock inventory → tone 'warning'; fulfilled "
    "orders and in-stock inventory → tone 'good'; anything else (partial, restocked) → tone "
    "'default'. Do not pad the screen with anything unrequested. You must call render_view "
    "before finishing — do not answer in plain text.\n\n"
    "Critical: render_view's fields must contain real, literal data — the actual orders or "
    "inventory returned by the tools you called. NEVER invent an order or item, or write "
    "placeholder/template syntax such as {{get_orders(...)}}. If you need data, call the tool "
    "first, wait for its real result in the conversation, then copy the real values into "
    "render_view. 'components' must be an actual JSON array of component objects, not a string."
)

TOOL_FUNCTIONS = {
    "get_orders": get_orders,
    "get_inventory": get_inventory,
}


def normalize_args(obj):
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
                return obj
        return obj
    if isinstance(obj, list):
        return [normalize_args(v) for v in obj]
    if isinstance(obj, dict):
        return {k: normalize_args(v) for k, v in obj.items()}
    return obj


NO_TOOL_CALL_NUDGE = (
    "You responded with plain text instead of using the tool-calling mechanism. "
    "Do not write JSON in your reply — call an actual tool (get_orders, get_inventory, or "
    "render_view) using function calling. Call a data tool now."
)

NO_DATA_YET_NUDGE = (
    "render_view was rejected: you haven't called get_orders or get_inventory yet, so you "
    "have no real data to show. Call one of them first, wait for its real result, then call "
    "render_view again using those real values."
)


def run_agent(user_request, max_steps=8, verbose=True):
    """
    Thin retry wrapper — see agent_github.py's run_agent() for why a
    network-error result gets exactly one full retry, and nothing else does.
    """
    result = _run_agent_once(user_request, max_steps, verbose)
    if isinstance(result, dict) and result.get("_network_error"):
        if verbose:
            print("  [retry] first attempt failed on a network error — retrying the whole request once", file=sys.stderr)
        result = _run_agent_once(user_request, max_steps, verbose)
    if isinstance(result, dict):
        result.pop("_network_error", None)
    return result


def _run_agent_once(user_request, max_steps, verbose):
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": user_request},
    ]
    fetched_data = False

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
                    # Was 2000 — see agent_github.py for why 700 is a safe cut.
                    max_tokens=700,
                )
            except Exception:
                resp = client.chat.completions.create(
                    model=MODEL,
                    messages=messages,
                    tools=TOOLS,
                    tool_choice="auto",
                    max_tokens=700,
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
                )
            }
        if verbose:
            print(f"  [step {step+1}] model call took {time.time()-step_start:.1f}s", file=sys.stderr)

        msg = resp.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None)

        if not tool_calls:
            if verbose:
                print(f"  [step {step+1}] responded in plain text instead of calling a tool — nudging", file=sys.stderr)
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

            if name == "render_view":
                if not fetched_data:
                    if verbose:
                        print(f"  [step {step+1}] render_view called before any data fetch — REJECTED", file=sys.stderr)
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": NO_DATA_YET_NUDGE})
                    continue
                problem = validate_view(args, UI_SCHEMA)
                if problem is None:
                    if verbose:
                        print(f"  [step {step+1}] render_view called — valid, done", file=sys.stderr)
                    return args
                if verbose:
                    print(f"  [step {step+1}] render_view called — REJECTED: {problem}", file=sys.stderr)
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": problem})
                continue

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
    query = sys.argv[1] if len(sys.argv) > 1 else "what orders need attention right now?"
    print(f"REQUEST: {query}\n", file=sys.stderr)
    result = run_agent(query)
    print(json.dumps(result, indent=2))
