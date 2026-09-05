"""
End-to-end test for the 2026-08-25 Gmail-styled preview: a Gmail-connected
workflow's live preview uses renderer.render_gmail_fragment (Gmail chrome:
search bar, sidebar, checkbox/star inbox rows), while every other connector
keeps the plain generic renderer unchanged.
"""
import sys, os, types, unittest.mock as mock

sys.path.insert(0, "/home/claude/pilant-agent")
os.environ["GMAIL_CLIENT_ID"] = "test-client-id.apps.googleusercontent.com"
os.environ["GMAIL_CLIENT_SECRET"] = "test-client-secret"

fake_openai = types.ModuleType("openai")
class FakeOpenAI:
    def __init__(self, *a, **k):
        pass
fake_openai.OpenAI = FakeOpenAI
sys.modules["openai"] = fake_openai

import studio

studio.app.config["TESTING"] = True
client = studio.app.test_client()


def login():
    r = client.post("/login", data={"username": "kowsick", "password": "owner123"}, follow_redirects=True)
    assert r.status_code == 200, r.status_code


login()

SAMPLE_RENDER = {
    "heading": "2 unread emails",
    "components": [
        {
            "type": "list",
            "rows": [
                {"name": "CredAbility <marketing@mail.credability.co.uk>: A new credit card added",
                 "note": "Check if you're eligible - it won't harm your score",
                 "badge": {"text": "Unread", "tone": "warning"}},
                {"name": "Groq <developer@groq.co>: Compound is being decommissioned",
                 "note": "Switch your models now.",
                 "badge": {"text": "Unread", "tone": "warning"}},
            ],
        }
    ],
}

# ---- Scenario 1: a Gmail workflow's preview uses the Gmail chrome ---------
r = client.post("/studio/new/gmail", follow_redirects=True)
gmail_wf_id = studio.WORKFLOW_ORDER[0]
studio.WORKFLOWS[gmail_wf_id]["last_render"] = SAMPLE_RENDER
studio.WORKFLOWS[gmail_wf_id]["last_request_text"] = "show my unread emails as a task list"
with client.session_transaction() as sess:
    sess["current_workflow"] = gmail_wf_id
r = client.get("/studio")
page = r.get_data(as_text=True)
assert 'class="gmail-app"' in page, "Gmail workflow should render the Gmail-chrome preview"
assert 'class="gmail-topbar"' in page and 'Search mail' in page
assert 'class="gmail-sidebar"' in page and "Compose" in page
assert 'class="gmail-row gmail-row-unread"' in page or 'gmail-row-unread' in page
assert "CredAbility" in page and "A new credit card added" in page
import html as _htmlmod
unescaped_page = _htmlmod.unescape(page)
assert "you're eligible" in unescaped_page and "won't harm" in unescaped_page  # apostrophes intact (HTML-escaped, as expected)
print("PASSED — Scenario 1: a Gmail-connected workflow's preview renders full Gmail chrome (topbar, sidebar, inbox rows).")

# ---- Scenario 2: a non-Gmail connector's preview is unaffected ------------
r = client.post("/studio/new/slack", follow_redirects=True)
slack_wf_id = studio.WORKFLOW_ORDER[0]
studio.WORKFLOWS[slack_wf_id]["last_render"] = SAMPLE_RENDER
studio.WORKFLOWS[slack_wf_id]["last_request_text"] = "show recent messages"
with client.session_transaction() as sess:
    sess["current_workflow"] = slack_wf_id
r = client.get("/studio")
page = r.get_data(as_text=True)
assert 'class="gmail-app"' not in page, "Slack workflow should NOT get the Gmail chrome"
assert 'class="list-row"' in page, "Slack workflow should still use the generic list-row renderer"
print("PASSED — Scenario 2: a non-Gmail connector's preview is untouched — still the generic card-list renderer.")

# ---- Scenario 3: account_label shows when a real Gmail account is connected
import json as _json
def fake_urlopen(req, timeout=15):
    if req.full_url == studio.connectors_gmail.TOKEN_URL:
        body = _json.dumps({"access_token": "AT", "refresh_token": "RT", "expires_in": 3600}).encode()
    else:
        body = _json.dumps({"emailAddress": "kowsick1vicky@gmail.com"}).encode()
    cm = mock.MagicMock()
    cm.__enter__.return_value.read.return_value = body
    return cm

client.get("/oauth/gmail/connect", follow_redirects=False)
with client.session_transaction() as sess:
    state = sess["gmail_oauth_state"]
with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
    client.get(f"/oauth/gmail/callback?code=abc&state={state}", follow_redirects=True)

with client.session_transaction() as sess:
    sess["current_workflow"] = gmail_wf_id
r = client.get("/studio")
page = r.get_data(as_text=True)
assert "kowsick1vicky@gmail.com" in page, "connected account's real address should show in the Gmail preview"
print("PASSED — Scenario 3: a live-connected Gmail account's address shows in the styled preview's heading/avatar.")

studio.connectors_gmail.disconnect()

print()
print("ALL GMAIL PREVIEW RENDER SCENARIOS PASSED")
