"""
Live verification of the migrated agent_gmail.py (NVIDIA -> Claude, 2026-08-26).

No real Gmail OAuth creds exist in this sandbox (.env has no GMAIL_CLIENT_ID /
GMAIL_CLIENT_SECRET / GMAIL_REFRESH_TOKEN), so get_gmail_messages is mocked with
realistic fake inbox data -- but every model call goes to the REAL Anthropic API
with the real key, exercising the actual claude_engine.py loop, dispatch
architecture, and guardrails end to end. This mirrors the agent_custom.py pilot's
validation approach (live API, not mocked model responses).

Run: python3 /tmp/test_agent_gmail_migration.py
"""
import sys, os, json, time, unittest.mock as mock

sys.path.insert(0, "/home/claude/pilant-agent")

import connectors_gmail

FAKE_INBOX = [
    {
        "id": "m1",
        "from": "Sarah Bennett <sarah.bennett@acme.com>",
        "subject": "Q3 budget review — action needed",
        "snippet": "Hi, can you take a look at the attached budget numbers before Friday's meeting?",
        "date": "2026-08-25T14:02:00Z",
        "unread": True,
    },
    {
        "id": "m2",
        "from": "GitHub <notifications@github.com>",
        "subject": "[pilant-agent] New issue: fix routing bug",
        "snippet": "kowsick1vicky opened a new issue in pilant-agent/pilant-agent",
        "date": "2026-08-25T09:15:00Z",
        "unread": True,
    },
    {
        "id": "m3",
        "from": "LinkedIn <messages-noreply@linkedin.com>",
        "subject": "You have 3 new connection requests",
        "snippet": "See who wants to connect with you on LinkedIn",
        "date": "2026-08-24T18:40:00Z",
        "unread": False,
    },
]

connectors_gmail.get_gmail_messages = mock.Mock(return_value=FAKE_INBOX)

import agent_gmail
agent_gmail.get_gmail_messages = connectors_gmail.get_gmail_messages

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
result = agent_gmail.run_agent("what unread emails do I have?", verbose=True)
elapsed = time.time() - t0
print(f"elapsed: {elapsed:.1f}s")
print(json.dumps(result, indent=2, default=str)[:2000])

check("test1: no error key", "error" not in result, result.get("error"))
# force_tool_choice=False means tool_choice stays "auto" on EVERY step (see
# claude_engine._run_once, and confirmed pre-existing in the NVIDIA backup's
# _run_agent_once at line 827/976-993) -- after fetching real data, the model
# is free to either call render_view OR reply in plain text summarizing what
# it found. Both are valid, pre-existing (not migration-introduced) outcomes,
# so accept either as long as the content is grounded in the real fetch.
check("test1: got either a render or a grounded text summary (not an error/clarify)",
      "render" in result or "text" in result, result)
if "render" in result:
    view = result["render"]
    comps = view.get("components") or []
    check("test1: has at least one component", len(comps) > 0)
    blob = json.dumps(view).lower()
    check("test1: mentions sarah bennett / budget (grounded real data)", "budget" in blob or "bennett" in blob)
    check("test1: mentions github issue (grounded real data)", "github" in blob or "routing bug" in blob)
    check("test1: does NOT include the read linkedin item", "linkedin" not in blob)
elif "text" in result:
    blob = result["text"].lower()
    check("test1: text summary mentions budget/bennett (grounded real data)", "budget" in blob or "bennett" in blob)
    check("test1: text summary mentions github issue (grounded real data)", "github" in blob or "routing bug" in blob)
    print("  (model summarized in plain text instead of calling render_view -- valid, pre-existing behavior, see NVIDIA backup parity check)")
check("test1: get_gmail_messages called exactly once", connectors_gmail.get_gmail_messages.call_count == 1,
      f"call_count={connectors_gmail.get_gmail_messages.call_count}")


# ---------------------------------------------------------------------------
print("\n=== Test 2: conversational reply, no tool call (force_tool_choice=False) ===")
connectors_gmail.get_gmail_messages.reset_mock()
t0 = time.time()
result2 = agent_gmail.run_agent("hi, what can you do?", verbose=True)
elapsed = time.time() - t0
print(f"elapsed: {elapsed:.1f}s")
print(json.dumps(result2, indent=2, default=str)[:1000])

check("test2: no error key", "error" not in result2, result2.get("error"))
check("test2: got a text reply, not a render/clarify", "text" in result2 and "render" not in result2 and "clarify" not in result2)
check("test2: get_gmail_messages NOT called for small talk", connectors_gmail.get_gmail_messages.call_count == 0,
      f"call_count={connectors_gmail.get_gmail_messages.call_count}")


# ---------------------------------------------------------------------------
print("\n=== Test 3: too-vague clarify round trip (live API) ===")
connectors_gmail.get_gmail_messages.reset_mock()
t0 = time.time()
result3 = agent_gmail.run_agent("find that email", verbose=True)
elapsed = time.time() - t0
print(f"elapsed: {elapsed:.1f}s")
print(json.dumps(result3, indent=2, default=str)[:1500])

# force_tool_choice=False means the model may legitimately clarify either via
# the ask_user tool ("clarify" key) OR a plain conversational text reply that
# asks for more detail ("text" key) -- both are valid per this connector's
# design (see agent_gmail.py's docstring: genuine conversational replies are
# an expected outcome here, unlike agent_custom.py). studio.py (lines
# 1085-1111) handles a "text" reply the same way either path leads to: shown
# as a chat message, memory-logged, next user turn is a fresh request that
# has that exchange as context.
got_clarify = "clarify" in result3
got_text_clarify = "text" in result3 and "?" in result3.get("text", "")
check("test3: model asks for more detail (via ask_user tool or plain text) for a vague request",
      got_clarify or got_text_clarify, result3)

if got_clarify:
    resumed = result3["messages"] + [{"role": "user", "content": "the one about the Q3 budget review"}]
    t0 = time.time()
    result3b = agent_gmail.run_agent(messages=resumed, fetched_data=result3.get("fetched_data", False), verbose=True)
    elapsed = time.time() - t0
    print(f"resume elapsed: {elapsed:.1f}s")
    print(json.dumps(result3b, indent=2, default=str)[:1500])
    check("test3b: no error key after resume", "error" not in result3b, result3b.get("error"))
    check("test3b: resume produces a render", "render" in result3b)
    if "render" in result3b:
        blob = json.dumps(result3b["render"]).lower()
        check("test3b: rendered content is grounded (budget/bennett)", "budget" in blob or "bennett" in blob)
elif got_text_clarify:
    print("  (model clarified via plain text, not the ask_user tool -- valid alternate path, skipping tool-based resume sub-checks)")


# ---------------------------------------------------------------------------
print("\n=== Test 4: fabrication guardrail unit test (dispatch-level, deterministic) ===")
# Bypass the model entirely -- directly exercise _make_dispatch's render_view branch
# with a view containing content that does NOT trace back to any real tool result,
# confirming the guardrail rejects it exactly as it did pre-migration.
dispatch = agent_gmail._make_dispatch(seed_fetched_data=True, verbose=False)

fake_prior_messages = [
    {"role": "user", "content": "show me my emails"},
    {"role": "assistant", "content": [{"type": "tool_use", "id": "tc1", "name": "get_gmail_messages", "input": {}}]},
    {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "tc1", "content": json.dumps(FAKE_INBOX)},
        ],
    },
]

fabricated_view = {
    "heading": "Your Inbox",
    "components": [
        {
            "type": "list",
            "rows": [
                {"name": "Totally made up subject line nobody sent", "note": "This message does not exist"},
            ],
        }
    ],
}

outcome = dispatch("render_view", fabricated_view, "tcX", fake_prior_messages, True)
check("test4: fabricated render_view is rejected (tool_result nudge, no final)",
      outcome.final is None and outcome.tool_result is not None and "was rejected" in outcome.tool_result,
      outcome.tool_result)

grounded_view = {
    "heading": "Unread Emails",
    "components": [
        {
            "type": "list",
            "rows": [
                {"name": "Q3 budget review — action needed", "note": "Sarah Bennett — Hi, can you take a look at the attached budget numbers"},
            ],
        }
    ],
}
outcome2 = dispatch("render_view", grounded_view, "tcY", fake_prior_messages, True)
check("test4b: grounded render_view is accepted (final set)", outcome2.final is not None, outcome2.tool_result)


# ---------------------------------------------------------------------------
print("\n=== Test 5: placeholder-label guardrail unit test ===")
placeholder_view = {
    "heading": "Emails",
    "components": [
        {
            "type": "list",
            "rows": [
                {"name": "Email 1", "note": "Snippet"},
            ],
        }
    ],
}
outcome3 = dispatch("render_view", placeholder_view, "tcZ", fake_prior_messages, True)
check("test5: placeholder labels ('Email 1'/'Snippet') rejected", outcome3.final is None and outcome3.tool_result is not None, outcome3.tool_result)


# ---------------------------------------------------------------------------
print(f"\n\n=== SUMMARY: {len(PASS)} passed, {len(FAIL)} failed ===")
if FAIL:
    print("FAILED:")
    for f in FAIL:
        print(f"  - {f}")
    sys.exit(1)
else:
    print("ALL PASSED")
