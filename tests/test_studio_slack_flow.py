"""
End-to-end test of the migrated agent_slack.py through studio.py's real Flask
routes (test client) -- mirrors /tmp/test_studio_gmail_flow.py: login -> new
slack workflow -> a real data request -> verify the rendered output, using
the REAL Anthropic API (only connectors_slack.get_slack_messages is mocked).
"""
import sys, os, json, unittest.mock as mock

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
]
connectors_slack.get_slack_messages = mock.Mock(return_value=FAKE_CHANNEL)

import studio
import agent_slack
agent_slack.get_slack_messages = connectors_slack.get_slack_messages

studio.app.config["TESTING"] = True
client = studio.app.test_client()


def login():
    r = client.post("/login", data={"username": "kowsick", "password": "owner123"}, follow_redirects=True)
    assert r.status_code == 200, r.status_code


login()

r = client.post("/studio/new/slack", follow_redirects=True)
assert r.status_code == 200, r.status_code
wf_id = studio.WORKFLOW_ORDER[0]
wf = studio.WORKFLOWS[wf_id]
assert wf["connector"] == "slack", f"expected slack workflow, got {wf['connector']!r}"
print(f"PASSED — new slack workflow created (id={wf_id})")

r = client.post("/studio/message", data={"text": "what's going on with the budget review?"}, follow_redirects=True)
assert r.status_code == 200, r.status_code

wf = studio.WORKFLOWS[wf_id]
print("messages so far:", json.dumps(wf["messages"], indent=2, default=str))
print("last_render:", json.dumps(wf.get("last_render"), indent=2, default=str))

# Model may legitimately either render or reply in grounded text (see agent_slack.py's
# force_tool_choice=False docstring) -- accept either as long as something happened.
last_assistant_text = next((m["text"] for m in reversed(wf["messages"]) if m["role"] == "assistant"), "")
got_render = wf.get("last_render") is not None
got_grounded_text = "budget" in last_assistant_text.lower() or "bennett" in last_assistant_text.lower()

assert got_render or got_grounded_text, f"expected a render or a grounded reply, got messages={wf['messages']}"

if got_render:
    blob = json.dumps(wf["last_render"]).lower()
    assert "budget" in blob or "bennett" in blob, f"expected grounded real content in render, got: {blob}"
    assert any("built it" in m["text"].lower() for m in wf["messages"] if m["role"] == "assistant")
    print("PASSED — /studio/message on a slack workflow produced a real, grounded render through the full studio.py stack.")
else:
    print("PASSED — /studio/message on a slack workflow produced a real, grounded conversational reply (valid alt path).")

assert connectors_slack.get_slack_messages.call_count == 1, (
    f"expected get_slack_messages called exactly once through studio.py, got {connectors_slack.get_slack_messages.call_count}"
)

print()
print("ALL STUDIO SLACK-FLOW SCENARIOS PASSED")
