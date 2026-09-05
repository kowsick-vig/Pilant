"""
End-to-end test of the migrated agent_github.py through studio.py's real Flask
routes (test client) -- mirrors the gmail/slack studio-flow tests, but also
exercises the needs_user identity-scoping path unique to this connector: login
as the RESTRICTED 'analyst' user and confirm out-of-scope issues never reach
the rendered screen, via the real /studio/message route.
"""
import sys, os, json, unittest.mock as mock

sys.path.insert(0, "/home/claude/pilant-agent")

import connectors_github

FAKE_ISSUES = [
    {"title": "Login page crashes on submit", "number": 101, "state": "open", "labels": ["bug", "critical"], "created_at": "2026-08-25T10:00:00Z", "comments": 4, "url": "https://github.com/x/x/issues/101"},
    {"title": "Add dark mode toggle", "number": 102, "state": "open", "labels": ["enhancement"], "created_at": "2026-08-24T10:00:00Z", "comments": 1, "url": "https://github.com/x/x/issues/102"},
]
connectors_github.get_github_issues = mock.Mock(return_value=FAKE_ISSUES)

import studio
import agent_github
agent_github.get_github_issues = connectors_github.get_github_issues

studio.app.config["TESTING"] = True
client = studio.app.test_client()


def login(username, password):
    r = client.post("/login", data={"username": username, "password": password}, follow_redirects=True)
    assert r.status_code == 200, r.status_code


login("analyst", "analyst123")

r = client.post("/studio/new/github", follow_redirects=True)
assert r.status_code == 200, r.status_code
wf_id = studio.WORKFLOW_ORDER[0]
wf = studio.WORKFLOWS[wf_id]
assert wf["connector"] == "github", f"expected github workflow, got {wf['connector']!r}"
print(f"PASSED — new github workflow created (id={wf_id}) as restricted user 'analyst'")

r = client.post("/studio/message", data={"text": "what open issues are there?"}, follow_redirects=True)
assert r.status_code == 200, r.status_code

wf = studio.WORKFLOWS[wf_id]
print("messages so far:", json.dumps(wf["messages"], indent=2, default=str))
print("last_render:", json.dumps(wf.get("last_render"), indent=2, default=str))

assert wf.get("last_render") is not None, f"expected a rendered view, got messages={wf['messages']}"
blob = json.dumps(wf["last_render"]).lower()
assert "login page crashes" in blob, f"expected the in-scope bug issue to be visible, got: {blob}"
assert "dark mode" not in blob, f"identity scoping FAILED — out-of-scope issue leaked through studio.py: {blob}"
assert connectors_github.get_github_issues.call_count == 1, (
    f"expected get_github_issues called exactly once through studio.py, got {connectors_github.get_github_issues.call_count}"
)
print("PASSED — /studio/message on a github workflow, as a scoped user, produced a grounded, "
      "correctly identity-filtered render through the full studio.py stack (needs_user kwarg wired correctly).")

print()
print("ALL STUDIO GITHUB-FLOW SCENARIOS PASSED")
