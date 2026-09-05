"""
Live verification of the migrated agent_healthcare.py (NVIDIA -> Claude,
2026-08-29). This is the sixth and last connector migrated, forced by the
NVIDIA meta/llama-3.1-8b-instruct model's real end-of-life on 2026-08-26 --
the user hit an actual 410 Gone running their live healthcare_dashboard.py
Copilot and pasted the traceback.

connectors_healthcare.get_appointments talks to a real Epic FHIR sandbox, so
it's mocked here with realistic fake appointment data (same approach as
test_agent_helpdesk_migration.py's static connector) -- but every model call
goes to the REAL Anthropic API with the real key, exercising the actual
claude_engine.py loop, dispatch architecture, and guardrails end to end.

Run: python3 tests/test_agent_healthcare_migration.py
"""
import sys, os, json, time, unittest.mock as mock

sys.path.insert(0, "/home/claude/pilant-agent")

import connectors_healthcare

FAKE_APPOINTMENTS = [
    {
        "id": "a1",
        "patient": "Jane Doe",
        "provider": "Dr. Priya Nair",
        "time": "9:00 AM",
        "status": "checked_in",
        "reason": "Follow-up visit",
    },
    {
        "id": "a2",
        "patient": "Marcus Ellison",
        "provider": "Dr. Priya Nair",
        "time": "9:30 AM",
        "status": "no_show",
        "reason": "Annual physical",
    },
    {
        "id": "a3",
        "patient": "Grace Kim",
        "provider": "Dr. Owen Reyes",
        "time": "10:15 AM",
        "status": "fulfilled",
        "reason": "Lab results review",
    },
]

connectors_healthcare.get_appointments = mock.Mock(return_value=FAKE_APPOINTMENTS)

import agent_healthcare
agent_healthcare.get_appointments = connectors_healthcare.get_appointments

PASS = []
FAIL = []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print(f"  PASS: {name}")
    else:
        FAIL.append(name)
        print(f"  FAIL: {name}  {detail}")


# ---------------------------------------------------------------------------
print("\n=== Test 1: real data fetch + render (live API) ===")
t0 = time.time()
result = agent_healthcare.run_agent("who needs to be checked in?", verbose=True)
elapsed = time.time() - t0
print(f"elapsed: {elapsed:.1f}s")
print(json.dumps(result, indent=2, default=str)[:2000])

check("test1: no error key", "error" not in result, result.get("error"))
# force_tool_choice=False (same as agent_gmail.py, this connector's closest
# sibling) -- after fetching real data, the model is free to either call
# render_view OR reply in plain text summarizing what it found. Both are
# valid, pre-existing (not migration-introduced) outcomes.
check("test1: got either a render or a grounded text summary (not an error/clarify)",
      "render" in result or "text" in result, result)
if "render" in result:
    view = result["render"]
    comps = view.get("components") or []
    check("test1: has at least one component", len(comps) > 0)
    blob = json.dumps(view).lower()
    check("test1: mentions a real fetched patient (grounded real data)",
          "jane doe" in blob or "marcus ellison" in blob)
elif "text" in result:
    blob = result["text"].lower()
    check("test1: text summary mentions real fetched data", "jane doe" in blob or "marcus ellison" in blob)
    print("  (model summarized in plain text instead of calling render_view -- valid, force_tool_choice=False)")
check("test1: get_appointments called exactly once", connectors_healthcare.get_appointments.call_count == 1,
      f"call_count={connectors_healthcare.get_appointments.call_count}")


# ---------------------------------------------------------------------------
print("\n=== Test 2: conversational reply, no tool call (force_tool_choice=False) ===")
connectors_healthcare.get_appointments.reset_mock()
t0 = time.time()
result2 = agent_healthcare.run_agent("hi, what can you help with?", verbose=True)
elapsed = time.time() - t0
print(f"elapsed: {elapsed:.1f}s")
print(json.dumps(result2, indent=2, default=str)[:1000])

check("test2: no error key", "error" not in result2, result2.get("error"))
check("test2: got a text reply, not a render/clarify", "text" in result2 and "render" not in result2 and "clarify" not in result2)
check("test2: get_appointments NOT called for small talk", connectors_healthcare.get_appointments.call_count == 0,
      f"call_count={connectors_healthcare.get_appointments.call_count}")


# ---------------------------------------------------------------------------
print("\n=== Test 3: vague request clarify round trip (live API) ===")
connectors_healthcare.get_appointments.reset_mock()
t0 = time.time()
result3 = agent_healthcare.run_agent("what needs attention right now?", verbose=True)
elapsed = time.time() - t0
print(f"elapsed: {elapsed:.1f}s")
print(json.dumps(result3, indent=2, default=str)[:1500])

# Same either/or as test 2 in test_agent_gmail_migration.py -- force_tool_choice=False
# means a vague request may legitimately clarify via ask_user ("clarify" key) or a
# plain conversational text reply that asks for more detail ("text" key).
got_clarify = "clarify" in result3
got_text_clarify = "text" in result3 and "?" in result3.get("text", "")
check("test3: model asks for more detail (via ask_user tool or plain text) for a vague request",
      got_clarify or got_text_clarify, result3)

if got_clarify:
    # Also confirms the 2026-08-29 claude_engine.py fix: this "messages" list
    # must be plain-dict content blocks, JSON-serializable exactly the way
    # healthcare_dashboard.py's Flask cookie session needs it to be.
    json.dumps(result3["messages"])
    print("  (clarify messages are JSON-serializable -- the exact shape healthcare_dashboard.py's session storage needs)")
    resumed = result3["messages"] + [{"role": "user", "content": "just the no-shows"}]
    t0 = time.time()
    result3b = agent_healthcare.run_agent(messages=resumed, fetched_data=result3.get("fetched_data", False), verbose=True)
    elapsed = time.time() - t0
    print(f"resume elapsed: {elapsed:.1f}s")
    print(json.dumps(result3b, indent=2, default=str)[:1500])
    check("test3b: no error key after resume", "error" not in result3b, result3b.get("error"))
    check("test3b: resume produces a render", "render" in result3b)
    if "render" in result3b:
        blob = json.dumps(result3b["render"]).lower()
        check("test3b: rendered content is grounded (marcus ellison, the real no-show)", "marcus ellison" in blob)
elif got_text_clarify:
    print("  (model clarified via plain text, not the ask_user tool -- valid alternate path, skipping tool-based resume sub-checks)")


# ---------------------------------------------------------------------------
print("\n=== Test 4: fabrication guardrail unit test (dispatch-level, deterministic) ===")
# Bypass the model entirely -- directly exercise _make_dispatch's render_view branch
# with a view containing content that does NOT trace back to any real tool result,
# confirming the guardrail rejects it exactly as it did pre-migration.
dispatch = agent_healthcare._make_dispatch(seed_fetched_data=True, verbose=False)

fake_prior_messages = [
    {"role": "user", "content": "who's checked in?"},
    {"role": "assistant", "content": [{"type": "tool_use", "id": "tc1", "name": "get_appointments", "input": {}}]},
    {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "tc1", "content": json.dumps(FAKE_APPOINTMENTS)},
        ],
    },
]

fabricated_view = {
    "heading": "Checked In",
    "components": [
        {
            "type": "list",
            "rows": [
                {"name": "Totally Invented Patient", "note": "Nobody real, made up on the spot"},
            ],
        }
    ],
}

outcome = dispatch("render_view", fabricated_view, "tcX", fake_prior_messages, True)
check("test4: fabricated render_view is rejected (tool_result nudge, no final)",
      outcome.final is None and outcome.tool_result is not None and "was rejected" in outcome.tool_result,
      outcome.tool_result)

grounded_view = {
    "heading": "Checked In",
    "components": [
        {
            "type": "list",
            "rows": [
                {"name": "Jane Doe", "note": "Dr. Priya Nair — Follow-up visit"},
            ],
        }
    ],
}
outcome2 = dispatch("render_view", grounded_view, "tcY", fake_prior_messages, True)
check("test4b: grounded render_view is accepted (final set)", outcome2.final is not None, outcome2.tool_result)


# ---------------------------------------------------------------------------
print("\n=== Test 5: placeholder-label guardrail unit test ===")
placeholder_view = {
    "heading": "Appointments",
    "components": [
        {
            "type": "list",
            "rows": [
                {"name": "Patient", "note": "Provider"},
            ],
        }
    ],
}
outcome3 = dispatch("render_view", placeholder_view, "tcZ", fake_prior_messages, True)
check("test5: placeholder labels ('Patient'/'Provider') rejected", outcome3.final is None and outcome3.tool_result is not None, outcome3.tool_result)


# ---------------------------------------------------------------------------
print("\n=== Test 6: already-fetched / already-asked / lazy-clarify guardrails (deterministic) ===")
dispatch2 = agent_healthcare._make_dispatch(seed_fetched_data=True, verbose=False)
outcome4 = dispatch2("get_appointments", {}, "tcA", [], True)
check("test6a: get_appointments rejected when data already fetched this call",
      outcome4.final is None and outcome4.tool_result == agent_healthcare.ALREADY_FETCHED_NUDGE)

already_asked_messages = [
    {"role": "user", "content": "what needs attention?"},
    {"role": "assistant", "content": [{"type": "tool_use", "id": "tc1", "name": "ask_user", "input": {"question": "no-shows or cancellations?"}}]},
    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tc1", "content": agent_healthcare.ce.ASK_USER_ACCEPTED_SENTINEL}]},
]
dispatch3 = agent_healthcare._make_dispatch(seed_fetched_data=False, verbose=False)
outcome5 = dispatch3("ask_user", {"question": "checked-in or fulfilled?"}, "tcB", already_asked_messages, False)
check("test6b: ask_user rejected after already asked once this conversation",
      outcome5.final is None and outcome5.tool_result == agent_healthcare.ALREADY_ASKED_NUDGE)

outcome6 = dispatch3("ask_user", {"question": "what needs attention right now?"}, "tcC",
                      [{"role": "user", "content": "what needs attention right now?"}], False)
check("test6c: lazy clarifying question (echoes the request) rejected",
      outcome6.final is None and outcome6.tool_result == agent_healthcare.LAZY_CLARIFY_NUDGE)

dispatch4 = agent_healthcare._make_dispatch(seed_fetched_data=False, verbose=False)
outcome7 = dispatch4("render_view", grounded_view, "tcD", [], False)
check("test6d: render_view rejected before any fetch",
      outcome7.final is None and outcome7.tool_result == agent_healthcare.NO_DATA_YET_NUDGE)


# ---------------------------------------------------------------------------
print(f"\n\n=== SUMMARY: {len(PASS)} passed, {len(FAIL)} failed ===")
if FAIL:
    print("FAILED:")
    for f in FAIL:
        print(f"  - {f}")
    sys.exit(1)
else:
    print("ALL PASSED")
