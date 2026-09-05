"""
Live verification (real Anthropic API, real claude_engine.py dispatch loop) that
the Studio chat connector (agent_gmail.py) actually uses the new `folder` param
end to end when a person asks about a built-in Gmail folder in natural language
-- not just that the schema/prompt mention it (that's covered by the fast/mocked
/tmp/test_gmail_folders.py). Mirrors /tmp/test_agent_gmail_migration.py's
live-API approach: get_gmail_messages is mocked with realistic fake data (no
real Gmail creds in this sandbox), but every model call is real.

Run: python3 /tmp/test_agent_gmail_folders_live.py
"""
import sys, json, time, unittest.mock as mock

sys.path.insert(0, "/home/claude/pilant-agent")

import connectors_gmail

FAKE_SPAM = [
    {
        "id": "s1",
        "from": "Prize Notification <winner@totally-legit-prizes.example>",
        "to": "me@example.com",
        "subject": "YOU HAVE WON $1,000,000 — CLAIM NOW",
        "snippet": "Congratulations! You've been selected to receive a cash prize...",
        "date": "2026-08-25T11:00:00Z",
        "unread": True,
    },
    {
        "id": "s2",
        "from": "Pharmacy Deals <deals@discount-meds.example>",
        "to": "me@example.com",
        "subject": "80% off all medications this week only",
        "snippet": "Limited time offer, order now before stock runs out...",
        "date": "2026-08-24T08:30:00Z",
        "unread": True,
    },
]

FAKE_SENT = [
    {
        "id": "t1",
        "from": "me@example.com",
        "to": "alice@example.com",
        "subject": "Re: budget numbers",
        "snippet": "Sounds good, I'll get those numbers to you by Friday.",
        "date": "2026-08-25T15:00:00Z",
        "unread": False,
    },
]

captured_calls = []


def fake_get_gmail_messages(**kwargs):
    captured_calls.append(kwargs)
    folder = (kwargs.get("folder") or "").lower()
    if folder == "spam":
        return FAKE_SPAM
    if folder == "sent":
        return FAKE_SENT
    return []


connectors_gmail.get_gmail_messages = mock.Mock(side_effect=fake_get_gmail_messages)

import agent_gmail
agent_gmail.get_gmail_messages = connectors_gmail.get_gmail_messages

PASS, FAIL = [], []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print(f"  PASS: {name}")
    else:
        FAIL.append(name)
        print(f"  FAIL: {name}  {detail}")


# ---------------------------------------------------------------------------
print("\n=== Test 1: 'show me my spam' -> get_gmail_messages(folder='spam') (live API) ===")
captured_calls.clear()
t0 = time.time()
result = agent_gmail.run_agent("show me what's in my spam folder", verbose=True)
print(f"elapsed: {time.time() - t0:.1f}s")
print(json.dumps(result, indent=2, default=str)[:1500])

check("test1: get_gmail_messages was called at least once", len(captured_calls) > 0, captured_calls)
if captured_calls:
    check("test1: folder='spam' was passed", captured_calls[0].get("folder", "").lower() == "spam", captured_calls[0])
check("test1: no error key", "error" not in result, result.get("error"))
check("test1: got a render or grounded text summary", "render" in result or "text" in result, result)
if "render" in result:
    rendered_text = json.dumps(result["render"])
    check("test1: rendered content mentions the real fetched spam data (prize/pharmacy), not fabricated",
          "1,000,000" in rendered_text or "medications" in rendered_text or "Prize" in rendered_text or "Pharmacy" in rendered_text,
          rendered_text[:500])


# ---------------------------------------------------------------------------
print("\n=== Test 2: 'what have I sent recently' -> folder='sent', shows recipient not self (live API) ===")
captured_calls.clear()
t0 = time.time()
result2 = agent_gmail.run_agent("what emails have I sent recently?", verbose=True)
print(f"elapsed: {time.time() - t0:.1f}s")
print(json.dumps(result2, indent=2, default=str)[:1500])

check("test2: get_gmail_messages was called at least once", len(captured_calls) > 0, captured_calls)
if captured_calls:
    check("test2: folder='sent' was passed", captured_calls[0].get("folder", "").lower() == "sent", captured_calls[0])
check("test2: no error key", "error" not in result2, result2.get("error"))
check("test2: got a render or grounded text summary", "render" in result2 or "text" in result2, result2)
if "render" in result2:
    rendered_text2 = json.dumps(result2["render"])
    check("test2: rendered content shows the recipient (alice), not just 'me@example.com' as the row label",
          "alice" in rendered_text2.lower(), rendered_text2[:500])


# ---------------------------------------------------------------------------
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILURES:", FAIL)
    sys.exit(1)
print("ALL LIVE GMAIL FOLDER SCENARIOS PASSED")
