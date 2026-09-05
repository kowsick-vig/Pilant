"""
Live verification of the migrated agent_slack.py (NVIDIA -> Claude, 2026-08-26).
Mirrors /tmp/test_agent_gmail_migration.py's approach: connectors_slack.get_slack_messages
is mocked (no live Slack token in this sandbox), but every model call goes to the REAL
Anthropic API, exercising claude_engine.py's loop, the dispatch architecture, and the
guardrails end to end.

Run: python3 /tmp/test_agent_slack_migration.py
"""
import sys, os, json, time, unittest.mock as mock

sys.path.insert(0, "/home/claude/pilant-agent")

import connectors_slack

FAKE_CHANNEL = [
    {
        "user": "Sarah Bennett",
        "text": "Heads up, the Q3 budget review doc is ready for review before Friday's meeting.",
        "ts": "2026-08-25T14:02:00Z",
        "reply_count": 2,
    },
    {
        "user": "Dev Bot",
        "text": "Build #482 failed on main — routing bug in the gmail connector.",
        "ts": "2026-08-25T09:15:00Z",
        "reply_count": 0,
    },
    {
        "user": "Marketing Team",
        "text": "New brand assets are up in the shared drive, take a look when you get a chance.",
        "ts": "2026-08-24T18:40:00Z",
        "reply_count": 0,
    },
]

connectors_slack.get_slack_messages = mock.Mock(return_value=FAKE_CHANNEL)

import agent_slack
agent_slack.get_slack_messages = connectors_slack.get_slack_messages

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
print("\n=== Test 1: real data fetch + render/summary (live API) ===")
t0 = time.time()
result = agent_slack.run_agent("what's been happening about the budget review?", verbose=True)
elapsed = time.time() - t0
print(f"elapsed: {elapsed:.1f}s")
print(json.dumps(result, indent=2, default=str)[:2000])

check("test1: no error key", "error" not in result, result.get("error"))
check("test1: got either a render or a grounded text summary (not an error/clarify)",
      "render" in result or "text" in result, result)
if "render" in result:
    view = result["render"]
    comps = view.get("components") or []
    check("test1: has at least one component", len(comps) > 0)
    blob = json.dumps(view).lower()
    check("test1: mentions sarah bennett / budget (grounded real data)", "budget" in blob or "bennett" in blob)
    check("test1: does NOT include the unrelated marketing message", "brand assets" not in blob)
elif "text" in result:
    blob = result["text"].lower()
    check("test1: text summary mentions budget/bennett (grounded real data)", "budget" in blob or "bennett" in blob)
    print("  (model summarized in plain text instead of calling render_view -- valid, see gmail parity note)")
check("test1: get_slack_messages called exactly once", connectors_slack.get_slack_messages.call_count == 1,
      f"call_count={connectors_slack.get_slack_messages.call_count}")


# ---------------------------------------------------------------------------
print("\n=== Test 2: conversational reply, no tool call (force_tool_choice=False) ===")
connectors_slack.get_slack_messages.reset_mock()
t0 = time.time()
result2 = agent_slack.run_agent("hey, what can you help me with?", verbose=True)
elapsed = time.time() - t0
print(f"elapsed: {elapsed:.1f}s")
print(json.dumps(result2, indent=2, default=str)[:1000])

check("test2: no error key", "error" not in result2, result2.get("error"))
check("test2: got a text reply, not a render/clarify", "text" in result2 and "render" not in result2 and "clarify" not in result2)
check("test2: get_slack_messages NOT called for small talk", connectors_slack.get_slack_messages.call_count == 0,
      f"call_count={connectors_slack.get_slack_messages.call_count}")


# ---------------------------------------------------------------------------
print("\n=== Test 3: too-vague clarify round trip (live API) ===")
connectors_slack.get_slack_messages.reset_mock()
t0 = time.time()
result3 = agent_slack.run_agent("find that message", verbose=True)
elapsed = time.time() - t0
print(f"elapsed: {elapsed:.1f}s")
print(json.dumps(result3, indent=2, default=str)[:1500])

got_clarify = "clarify" in result3
got_text_clarify = "text" in result3 and "?" in result3.get("text", "")
check("test3: model asks for more detail (via ask_user tool or plain text) for a vague request",
      got_clarify or got_text_clarify, result3)

if got_clarify:
    resumed = result3["messages"] + [{"role": "user", "content": "the one about the budget review"}]
    t0 = time.time()
    result3b = agent_slack.run_agent(messages=resumed, fetched_data=result3.get("fetched_data", False), verbose=True)
    elapsed = time.time() - t0
    print(f"resume elapsed: {elapsed:.1f}s")
    print(json.dumps(result3b, indent=2, default=str)[:1500])
    check("test3b: no error key after resume", "error" not in result3b, result3b.get("error"))
    check("test3b: resume produces a render or grounded text", "render" in result3b or "text" in result3b)
elif got_text_clarify:
    print("  (model clarified via plain text, not the ask_user tool -- valid alternate path)")


# ---------------------------------------------------------------------------
print("\n=== Test 4: fabrication guardrail unit test (dispatch-level, deterministic) ===")
dispatch = agent_slack._make_dispatch(seed_fetched_data=True, verbose=False)

fake_prior_messages = [
    {"role": "user", "content": "show me the channel"},
    {"role": "assistant", "content": [{"type": "tool_use", "id": "tc1", "name": "get_slack_messages", "input": {}}]},
    {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "tc1", "content": json.dumps(FAKE_CHANNEL)},
        ],
    },
]

fabricated_view = {
    "heading": "Channel Activity",
    "components": [
        {
            "type": "list",
            "rows": [
                {"name": "Someone who never posted", "note": "A message that was never actually sent"},
            ],
        }
    ],
}

outcome = dispatch("render_view", fabricated_view, "tcX", fake_prior_messages, True)
check("test4: fabricated render_view is rejected (tool_result nudge, no final)",
      outcome.final is None and outcome.tool_result is not None and "was rejected" in outcome.tool_result,
      outcome.tool_result)

grounded_view = {
    "heading": "Budget Review Update",
    "components": [
        {
            "type": "list",
            "rows": [
                {"name": "Sarah Bennett", "note": "Heads up, the Q3 budget review doc is ready for review before Friday's meeting."},
            ],
        }
    ],
}
outcome2 = dispatch("render_view", grounded_view, "tcY", fake_prior_messages, True)
check("test4b: grounded render_view is accepted (final set)", outcome2.final is not None, outcome2.tool_result)


# ---------------------------------------------------------------------------
print("\n=== Test 5: placeholder-label guardrail unit test ===")
placeholder_view = {
    "heading": "Messages",
    "components": [
        {
            "type": "list",
            "rows": [
                {"name": "Message 1", "note": "Sender"},
            ],
        }
    ],
}
outcome3 = dispatch("render_view", placeholder_view, "tcZ", fake_prior_messages, True)
check("test5: placeholder labels ('Message 1'/'Sender') rejected", outcome3.final is None and outcome3.tool_result is not None, outcome3.tool_result)


# ---------------------------------------------------------------------------
print(f"\n\n=== SUMMARY: {len(PASS)} passed, {len(FAIL)} failed ===")
if FAIL:
    print("FAILED:")
    for f in FAIL:
        print(f"  - {f}")
    sys.exit(1)
else:
    print("ALL PASSED")
