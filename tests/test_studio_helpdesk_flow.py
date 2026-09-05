"""
End-to-end test of the migrated agent_helpdesk.py through studio.py's real Flask
routes (test client) -- mirrors the gmail/slack/github studio-flow tests. No
mocking needed: connectors_helpdesk.get_tickets is the real static/dummy dataset.
"""
import sys, json

sys.path.insert(0, "/home/claude/pilant-agent")

import studio
import connectors_helpdesk

studio.app.config["TESTING"] = True
client = studio.app.test_client()


def login():
    r = client.post("/login", data={"username": "kowsick", "password": "owner123"}, follow_redirects=True)
    assert r.status_code == 200, r.status_code


login()

r = client.post("/studio/new/helpdesk", follow_redirects=True)
assert r.status_code == 200, r.status_code
wf_id = studio.WORKFLOW_ORDER[0]
wf = studio.WORKFLOWS[wf_id]
assert wf["connector"] == "helpdesk", f"expected helpdesk workflow, got {wf['connector']!r}"
print(f"PASSED — new helpdesk workflow created (id={wf_id})")

r = client.post("/studio/message", data={"text": "what escalated tickets do we have?"}, follow_redirects=True)
assert r.status_code == 200, r.status_code

wf = studio.WORKFLOWS[wf_id]
print("messages so far:", json.dumps(wf["messages"], indent=2, default=str))
print("last_render:", json.dumps(wf.get("last_render"), indent=2, default=str))

assert wf.get("last_render") is not None, f"expected a rendered view, got messages={wf['messages']}"
escalated = [t["subject"] for t in connectors_helpdesk.get_tickets(status="escalated")]
blob = json.dumps(wf["last_render"]).lower()
assert any(s.lower() in blob for s in escalated), f"expected a real escalated ticket subject in render, got: {blob}"
assert any("built it" in m["text"].lower() for m in wf["messages"] if m["role"] == "assistant")
print("PASSED — /studio/message on a helpdesk workflow produced a real, grounded render through the full studio.py stack.")

print()
print("ALL STUDIO HELPDESK-FLOW SCENARIOS PASSED")
