"""
End-to-end test for the 2026-08-25 Integrations column feature in studio.py:
- All 5 connectors appear in CONNECTORS and render as clickable items.
- Clicking an integration-column item (/studio/new/<key>) creates a workflow
  PRE-CONNECTED to that connector — chat should NOT ask "which app?" for it.
- The plain "+ New workflow" button (/studio/new) still creates a
  connector-less workflow that DOES ask "which app?" (regression check —
  conversational picker path must still work).
- An unrecognized connector_key in the URL falls back gracefully instead of
  500ing.
- GitHub's run_agent is invoked with a `user` kwarg (needs_user=True);
  Gmail's is invoked WITHOUT one (would TypeError if passed, since its
  run_agent doesn't accept `user` at all) — proves the needs_user wiring in
  studio_message() is real, not just present in the registry.
"""
import sys, types, unittest.mock as mock

sys.path.insert(0, "/home/claude/pilant-agent")

fake_openai = types.ModuleType("openai")
class FakeOpenAI:
    def __init__(self, *a, **k):
        pass
fake_openai.OpenAI = FakeOpenAI
sys.modules["openai"] = fake_openai

import studio

# ---- login helper -----------------------------------------------------
studio.app.config["TESTING"] = True
client = studio.app.test_client()


def login():
    r = client.post("/login", data={"username": "kowsick", "password": "owner123"}, follow_redirects=True)
    assert r.status_code == 200, r.status_code


login()

# ---- Scenario 1: all 5 connectors registered & rendered ----------------
assert set(studio.CONNECTORS.keys()) == {"gmail", "slack", "github", "healthcare", "helpdesk"}, studio.CONNECTORS.keys()
r = client.get("/studio")
page = r.get_data(as_text=True)
for key, cfg in studio.CONNECTORS.items():
    assert f'/studio/new/{key}' in page, f"missing integrations-column link for {key}"
    assert cfg["label"] in page, f"missing label for {key} in rendered page"
print("PASSED — Scenario 1: all 5 connectors registered and rendered as clickable integration items.")

# ---- Scenario 2: click GitHub in the integrations column -> pre-connected
r = client.post("/studio/new/github", follow_redirects=True)
page = r.get_data(as_text=True)
wf = studio.WORKFLOWS[studio.WORKFLOW_ORDER[0]]
assert wf["connector"] == "github", wf["connector"]
assert "which app" not in page.lower(), "should NOT ask which app — already pre-connected"
assert "GitHub" in page
print("PASSED — Scenario 2: clicking GitHub in the integrations column pre-connects the new workflow, skipping the app-picker question.")

# ---- Scenario 3: plain "+ New workflow" still asks which app (regression)
r = client.post("/studio/new", follow_redirects=True)
page = r.get_data(as_text=True)
wf2 = studio.WORKFLOWS[studio.WORKFLOW_ORDER[0]]
assert wf2["connector"] is None, wf2["connector"]
assert "which app" in page.lower(), "plain + New workflow should still ask which app"
print("PASSED — Scenario 3: plain '+ New workflow' still opens the conversational app-picker (unchanged).")

# ---- Scenario 4: unrecognized connector_key falls back gracefully ------
r = client.post("/studio/new/not_a_real_connector", follow_redirects=True)
assert r.status_code == 200
wf3 = studio.WORKFLOWS[studio.WORKFLOW_ORDER[0]]
assert wf3["connector"] is None, wf3["connector"]
print("PASSED — Scenario 4: an unrecognized connector_key falls back to the connector-less flow instead of erroring.")

# ---- Scenario 5: needs_user wiring is real (github gets user=, gmail doesn't)
# Re-open the github-pre-connected workflow from scenario 2 and send a message,
# mocking both run_agent functions to assert call signatures.
github_wf_id = None
for wid in studio.WORKFLOW_ORDER:
    if studio.WORKFLOWS[wid]["connector"] == "github":
        github_wf_id = wid
        break
assert github_wf_id, "expected a github-connected workflow from scenario 2"

with client.session_transaction() as sess:
    sess["current_workflow"] = github_wf_id

# CONNECTORS holds a direct reference captured at import time (run_agent=github_run_agent),
# so patch the registry entry's function directly instead of the module attribute.
real_github_fn = studio.CONNECTORS["github"]["run_agent"]
mock_github_fn = mock.Mock(return_value={"render": {"heading": "x", "components": []}})
studio.CONNECTORS["github"]["run_agent"] = mock_github_fn
try:
    client.post("/studio/message", data={"text": "show my open issues"}, follow_redirects=True)
finally:
    studio.CONNECTORS["github"]["run_agent"] = real_github_fn

assert mock_github_fn.called, "github run_agent should have been called"
_, kwargs = mock_github_fn.call_args
assert "user" in kwargs, f"github call should include a user kwarg, got kwargs={kwargs}"
assert kwargs["user"]["username"] == "kowsick", kwargs["user"]

# Now do the same for gmail (needs_user unset) — must NOT receive a user kwarg.
gmail_wf = studio._create_workflow(connector="gmail")
with client.session_transaction() as sess:
    sess["current_workflow"] = gmail_wf["id"]

real_gmail_fn = studio.CONNECTORS["gmail"]["run_agent"]
mock_gmail_fn = mock.Mock(return_value={"render": {"heading": "x", "components": []}})
studio.CONNECTORS["gmail"]["run_agent"] = mock_gmail_fn
try:
    client.post("/studio/message", data={"text": "show my unread emails"}, follow_redirects=True)
finally:
    studio.CONNECTORS["gmail"]["run_agent"] = real_gmail_fn

assert mock_gmail_fn.called, "gmail run_agent should have been called"
_, kwargs2 = mock_gmail_fn.call_args
assert "user" not in kwargs2, f"gmail call should NOT include a user kwarg, got kwargs={kwargs2}"
print("PASSED — Scenario 5: needs_user wiring is real — GitHub's run_agent receives a `user` kwarg, Gmail's does not.")

print()
print("ALL STUDIO INTEGRATIONS-COLUMN SCENARIOS PASSED")
