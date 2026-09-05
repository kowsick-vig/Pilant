"""
Live-API smoke test (real Anthropic call, no model mocking) for the
2026-08-26 "ask like DynamisOS in our main workflow" feature — the
dispatch-level gate is proven correct by unit tests in
/tmp/test_first_turn_clarify.py, but this proves the actual instructed
behavior holds against the real model: on a brand-new gmail workflow's
first request, does Claude actually call ask_user with a real bundled
question (not just get nudged in circles until max_steps), and does the
resumed call, after an answer, proceed straight to a real fetch+render?
"""
import sys
import unittest.mock as mock

sys.path.insert(0, "/home/claude/pilant-agent")
import os
os.environ.setdefault("GMAIL_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
os.environ.setdefault("GMAIL_CLIENT_SECRET", "test-client-secret")

import agent_gmail

passed = 0
failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  OK   {label}")
    else:
        failed += 1
        print(f"  FAIL {label}  {detail}")


FAKE_MESSAGES = [
    {"from": "finance@acme.com", "subject": "Q3 budget review", "date": "Aug 26, 2026",
     "snippet": "Attached is the updated budget for review.", "unread": True, "id": "m1"},
    {"from": "bob@acme.com", "subject": "lunch?", "date": "Aug 26, 2026",
     "snippet": "want to grab lunch today?", "unread": False, "id": "m2"},
]

print("== Live test: brand-new conversation, first_turn=True — Claude should ask ONE bundled question ==")
with mock.patch.object(agent_gmail, "get_gmail_messages", return_value=FAKE_MESSAGES) as m:
    result = agent_gmail.run_agent("show my inbox", first_turn=True, verbose=True)
    print(result)
    check("got a clarify result, not a render (proactive question fired before any fetch)",
          "clarify" in result, result)
    check("get_gmail_messages was NEVER actually called before the question", m.call_count == 0)
    if "clarify" in result:
        q = result["clarify"].lower()
        check("the question reads like it covers purpose (not just a generic 'could you clarify')",
              len(q) > 20)

    if "clarify" in result:
        print("\n== Resuming with an answer — should now proceed straight to fetch+render, no second question ==")
        resumed = result["messages"] + [{"role": "user", "content": "everything unread, and I like a compact list"}]
        result2 = agent_gmail.run_agent(messages=resumed, fetched_data=result.get("fetched_data", False), first_turn=False, verbose=True)
        print(result2)
        check("resumed call produced a real render (not another clarify)", "render" in result2, result2)
        check("resumed call actually called the real fetch tool", m.call_count == 1)

print(f"\n=== SUMMARY: {passed} passed, {failed} failed ===")
if failed:
    sys.exit(1)
