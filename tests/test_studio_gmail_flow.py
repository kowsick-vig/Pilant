"""
End-to-end test of the migrated agent_gmail.py through studio.py's real Flask
routes (test client) — mirrors the validation done for the agent_custom.py
pilot, but for the gmail connector: login -> new gmail workflow -> a real data
request -> verify the rendered output, using the REAL Anthropic API (only
connectors_gmail.get_gmail_messages is mocked, since no live Gmail OAuth
creds exist in this sandbox).
"""
import sys, os, json, unittest.mock as mock

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
]
connectors_gmail.get_gmail_messages = mock.Mock(return_value=FAKE_INBOX)

import studio
import agent_gmail
agent_gmail.get_gmail_messages = connectors_gmail.get_gmail_messages

studio.app.config["TESTING"] = True
client = studio.app.test_client()


def login():
    r = client.post("/login", data={"username": "kowsick", "password": "owner123"}, follow_redirects=True)
    assert r.status_code == 200, r.status_code


login()

r = client.post("/studio/new/gmail", follow_redirects=True)
assert r.status_code == 200, r.status_code
wf_id = studio.WORKFLOW_ORDER[0]
wf = studio.WORKFLOWS[wf_id]
assert wf["connector"] == "gmail", f"expected gmail workflow, got {wf['connector']!r}"
print(f"PASSED — new gmail workflow created (id={wf_id})")

r = client.post("/studio/message", data={"text": "what unread emails do I have?"}, follow_redirects=True)
assert r.status_code == 200, r.status_code

wf = studio.WORKFLOWS[wf_id]
print("messages so far:", json.dumps(wf["messages"], indent=2, default=str))
print("last_render:", json.dumps(wf.get("last_render"), indent=2, default=str))

assert wf.get("last_render") is not None, f"expected a rendered view, got messages={wf['messages']}"
blob = json.dumps(wf["last_render"]).lower()
assert "budget" in blob or "bennett" in blob, f"expected grounded real content in render, got: {blob}"
assert connectors_gmail.get_gmail_messages.call_count == 1, (
    f"expected get_gmail_messages called exactly once through studio.py, got {connectors_gmail.get_gmail_messages.call_count}"
)
assert any("built it" in m["text"].lower() for m in wf["messages"] if m["role"] == "assistant"), (
    f"expected the standard 'Built it' confirmation message, got: {wf['messages']}"
)
print("PASSED — /studio/message on a gmail workflow produced a real, grounded render through the full studio.py stack.")

print()
print("ALL STUDIO GMAIL-FLOW SCENARIOS PASSED")
