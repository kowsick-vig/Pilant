"""
The Composition Engine — a real, bounded AI agent.

Given a typed request, it decides which connector tools to call, reads the
results, and emits a final screen by calling render_view — whose input
schema IS the UI schema, so the model's output is shaped by the schema
rather than free text or raw HTML.
"""

import os
import json
import sys
import webbrowser
from pathlib import Path

import anthropic

from schema import UI_SCHEMA
from connectors import get_security_incidents, get_pending_approvals
from validation import validate_view
from renderer import render_html

# load .env manually (no extra dependency)
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

client = anthropic.Anthropic()

TOOLS = [
    {
        "name": "get_security_incidents",
        "description": "Fetch current security incidents, optionally filtered to ones affecting customer-facing systems and/or by severity.",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_systems_only": {"type": "boolean"},
                "severity": {"type": "string", "enum": ["critical", "high", "medium", "low"]}
            }
        }
    },
    {
        "name": "get_pending_approvals",
        "description": "Fetch requests currently waiting on the user's approval.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "render_view",
        "description": (
            "Emit the final generated screen for the user's request. Call this exactly once, "
            "as the last step, after fetching whatever data you need. Include ONLY the "
            "components required to answer the request — no unrelated data, no default dashboard."
        ),
        "input_schema": UI_SCHEMA
    }
]

SYSTEM = (
    "You are the Composition Engine inside Pilant, a security operations platform that has no "
    "default dashboard. A person has typed a request. Call whichever data tools you need to "
    "answer it, then call render_view exactly once with a screen that shows ONLY what they "
    "asked for. Be precise about what the request actually implies — 'what's affecting "
    "customers' means filter to customer-facing systems; 'waiting on my approval' means "
    "approvals, not incidents. Do not pad the screen with anything unrequested.\n\n"
    "Critical: render_view's fields must contain real, literal data — the actual values "
    "returned by the data tools you called. NEVER write placeholder or template syntax "
    "such as {{get_security_incidents(...)}} or any string that looks like a function call. "
    "If you need data, call the tool first, wait for its real result, then copy the real "
    "values into render_view. 'components' must be an actual JSON array of component "
    "objects, not a string."
)

TOOL_FUNCTIONS = {
    "get_security_incidents": get_security_incidents,
    "get_pending_approvals": get_pending_approvals,
}


NO_DATA_YET_NUDGE = (
    "render_view was rejected: you haven't called a data tool yet, so you have no real "
    "data to show. Call get_security_incidents or get_pending_approvals first, wait for "
    "its real result, then call render_view again using those real values."
)


def normalize_args(obj):
    """
    Some models double-encode nested arrays/objects as JSON strings inside
    tool call arguments instead of real nested structures. Claude rarely
    does this, but recovering here costs nothing and keeps agent.py and
    agent_nvidia.py behaving identically if the model is swapped later.
    """
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


def run_agent(user_request, max_steps=8, verbose=True):
    messages = [{"role": "user", "content": user_request}]
    fetched_data = False

    for step in range(max_steps):
        try:
            resp = client.messages.create(
                model="claude-sonnet-4-5",
                max_tokens=2000,
                system=SYSTEM,
                tools=TOOLS,
                tool_choice={"type": "any"},
                messages=messages,
            )
        except Exception:
            resp = client.messages.create(
                model="claude-sonnet-4-5",
                max_tokens=2000,
                system=SYSTEM,
                tools=TOOLS,
                messages=messages,
            )

        messages.append({"role": "assistant", "content": resp.content})

        if resp.stop_reason != "tool_use":
            return {"error": "model did not call render_view", "text": resp.content}

        tool_results = []
        for block in resp.content:
            if block.type != "tool_use":
                continue
            if block.name == "render_view":
                block_input = normalize_args(block.input)
                if not fetched_data:
                    if verbose:
                        print(f"  [step {step+1}] render_view called before any data fetch — REJECTED", file=sys.stderr)
                    tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": NO_DATA_YET_NUDGE})
                    continue
                problem = validate_view(block_input, UI_SCHEMA)
                if problem is None:
                    if verbose:
                        print(f"  [step {step+1}] render_view called — valid, done", file=sys.stderr)
                    return block_input
                if verbose:
                    print(f"  [step {step+1}] render_view called — REJECTED: {problem}", file=sys.stderr)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": problem,
                })
                continue
            fn = TOOL_FUNCTIONS.get(block.name)
            if verbose:
                print(f"  [step {step+1}] tool call: {block.name}({block.input})", file=sys.stderr)
            result = fn(**block.input) if fn else {"error": "unknown tool"}
            if fn:
                fetched_data = True
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(result),
            })
        messages.append({"role": "user", "content": tool_results})

    return {"error": "hit max_steps without rendering"}


if __name__ == "__main__":
    query = sys.argv[1] if len(sys.argv) > 1 else "Show only the critical incidents affecting customer systems. Prioritize what I need to investigate."
    print(f"REQUEST: {query}\n", file=sys.stderr)
    result = run_agent(query)
    print(json.dumps(result, indent=2))

    if "error" not in result:
        out_path = Path(__file__).parent / "output.html"
        out_path.write_text(render_html(result, query))
        print(f"\nrendered screen written to {out_path}", file=sys.stderr)
        webbrowser.open(out_path.as_uri())
