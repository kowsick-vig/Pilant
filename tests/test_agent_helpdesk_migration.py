"""
Live verification of the migrated agent_helpdesk.py (NVIDIA -> Claude, 2026-08-26).
Fifth and last connector. Mirrors the agent_github.py migration test's shape (also
force_tool_choice=True, no conversational-reply mode) but without identity scoping.
connectors_helpdesk.get_tickets is the real static/dummy connector (no mocking needed --
it's deterministic in-memory data, not a live network call), and every model call goes to
the REAL Anthropic API.

Run: python3 /tmp/test_agent_helpdesk_migration.py
"""
import sys, os, json, time

sys.path.insert(0, "/home/claude/pilant-agent")

import connectors_helpdesk
import agent_helpdesk

PASS = []
FAIL = []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print(f"  PASS: {name}")
    else:
        FAIL.append(name)
        print(f"  FAIL: {name}  {detail}")


# Look at the real static ticket data so assertions are grounded in what's actually there.
all_tickets = connectors_helpdesk.get_tickets()
escalated = [t for t in all_tickets if t.get("status") == "escalated"]
print(f"real static data: {len(all_tickets)} total tickets, {len(escalated)} escalated")
assert escalated, "test assumes at least one escalated ticket exists in the static dataset"
sample_subject = escalated[0]["subject"]

# ---------------------------------------------------------------------------
print("\n=== Test 1: real data fetch + render, escalated tickets (live API) ===")
t0 = time.time()
result = agent_helpdesk.run_agent("what escalated tickets do we have?", verbose=True)
elapsed = time.time() - t0
print(f"elapsed: {elapsed:.1f}s")
print(json.dumps(result, indent=2, default=str)[:2500])

check("test1: no error key", "error" not in result, result.get("error"))
check("test1: got a render (force_tool_choice=True, no text mode)", "render" in result, result)
if "render" in result:
    blob = json.dumps(result["render"]).lower()
    check(f"test1: mentions a real escalated ticket subject ({sample_subject!r})", sample_subject.lower() in blob)
    non_escalated_subjects = [t["subject"] for t in all_tickets if t.get("status") != "escalated"]
    leaked = [s for s in non_escalated_subjects if s.lower() in blob]
    check("test1: does not include non-escalated ticket subjects", not leaked, leaked)


# ---------------------------------------------------------------------------
print("\n=== Test 2: too-vague clarify round trip (live API, forced tool choice) ===")
t0 = time.time()
result2 = agent_helpdesk.run_agent("what needs attention?", verbose=True)
elapsed = time.time() - t0
print(f"elapsed: {elapsed:.1f}s")
print(json.dumps(result2, indent=2, default=str)[:1500])

check("test2: no error (either clarified or rendered a reasonable interpretation)",
      "error" not in result2, result2)
check("test2: got clarify or render (never bare text, since force_tool_choice=True)",
      "clarify" in result2 or "render" in result2, result2)

if "clarify" in result2:
    resumed = result2["messages"] + [{"role": "user", "content": "the escalated ones"}]
    t0 = time.time()
    result2b = agent_helpdesk.run_agent(messages=resumed, fetched_data=result2.get("fetched_data", False), verbose=True)
    elapsed = time.time() - t0
    print(f"resume elapsed: {elapsed:.1f}s")
    print(json.dumps(result2b, indent=2, default=str)[:1500])
    check("test2b: no error after resume", "error" not in result2b, result2b.get("error"))
    check("test2b: resume produces a render", "render" in result2b)


# ---------------------------------------------------------------------------
print("\n=== Test 3: fabrication guardrail unit test (dispatch-level, deterministic) ===")
dispatch = agent_helpdesk._make_dispatch(seed_fetched_data=True, verbose=False)

fake_prior_messages = [
    {"role": "user", "content": "show me the tickets"},
    {"role": "assistant", "content": [{"type": "tool_use", "id": "tc1", "name": "get_tickets", "input": {}}]},
    {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "tc1", "content": json.dumps(all_tickets)},
        ],
    },
]

fabricated_view = {
    "heading": "Escalated Tickets",
    "components": [
        {
            "type": "list",
            "rows": [
                {"name": "Totally invented ticket nobody filed", "note": "This ticket does not exist in the system"},
            ],
        }
    ],
}
outcome = dispatch("render_view", fabricated_view, "tcX", fake_prior_messages, True)
check("test3: fabricated render_view is rejected (tool_result nudge, no final)",
      outcome.final is None and outcome.tool_result is not None and "was rejected" in outcome.tool_result,
      outcome.tool_result)

grounded_view = {
    "heading": "Escalated Tickets",
    "components": [
        {
            "type": "list",
            "rows": [
                {"name": sample_subject, "note": f"Requester: {escalated[0].get('requester', 'unknown')}"},
            ],
        }
    ],
}
outcome2 = dispatch("render_view", grounded_view, "tcY", fake_prior_messages, True)
check("test3b: grounded render_view is accepted (final set)", outcome2.final is not None, outcome2.tool_result)


# ---------------------------------------------------------------------------
print("\n=== Test 4: placeholder-label guardrail unit test ===")
placeholder_view = {
    "heading": "Tickets",
    "components": [
        {
            "type": "list",
            "rows": [
                {"name": "Ticket 1", "note": "Status"},
            ],
        }
    ],
}
outcome3 = dispatch("render_view", placeholder_view, "tcZ", fake_prior_messages, True)
check("test4: placeholder labels ('Ticket 1'/'Status') rejected", outcome3.final is None and outcome3.tool_result is not None, outcome3.tool_result)


# ---------------------------------------------------------------------------
print("\n=== Test 5: get_tickets called exactly once per run (no redundant fetch) ===")
import unittest.mock as mock
real_get_tickets = connectors_helpdesk.get_tickets
wrapped = mock.Mock(side_effect=real_get_tickets)
agent_helpdesk.get_tickets = wrapped
t0 = time.time()
result5 = agent_helpdesk.run_agent("show me billing tickets", verbose=True)
elapsed = time.time() - t0
print(f"elapsed: {elapsed:.1f}s")
check("test5: no error", "error" not in result5, result5.get("error"))
check("test5: got a render", "render" in result5, result5)
check("test5: get_tickets called exactly once", wrapped.call_count == 1, f"call_count={wrapped.call_count}")
agent_helpdesk.get_tickets = real_get_tickets


# ---------------------------------------------------------------------------
print(f"\n\n=== SUMMARY: {len(PASS)} passed, {len(FAIL)} failed ===")
if FAIL:
    print("FAILED:")
    for f in FAIL:
        print(f"  - {f}")
    sys.exit(1)
else:
    print("ALL PASSED")
