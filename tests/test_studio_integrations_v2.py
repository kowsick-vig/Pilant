"""
End-to-end test for the 2026-08-25 redesign of studio.py's Integrations
feature: a sidebar nav link (not an icon column) leading to a full
/integrations page, Healthcare removed, and a real Gmail OAuth
connect/callback/disconnect flow (Google's HTTP calls mocked).
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

# ---- Scenario 1: Healthcare is gone; the 4 real single-app connectors
# remain, plus the static "jira" connector (added 2026-08-31, fake/crowded
# Jira-style issues), the "unified" mixed-apps connector (added 2026-08-31),
# and the freeform "custom" entry added 2026-08-25 (see
# test_studio_custom_flow.py) --
assert set(studio.CONNECTORS.keys()) == {"gmail", "slack", "github", "helpdesk", "jira", "unified", "custom"}, studio.CONNECTORS.keys()
assert studio.CONNECTORS["custom"].get("freeform") is True, "custom' should be flagged freeform"
assert studio.CONNECTORS["unified"].get("category") == "overview", "'unified' should be in its own 'overview' category"
assert studio.CONNECTORS["jira"].get("category") == "developer", "'jira' should sit alongside GitHub in 'developer'"
print("PASSED — Scenario 1: Healthcare removed from CONNECTORS; the 4 real single-app connectors,")
print("         the static 'jira' connector, the 'unified' mixed-apps connector, and the freeform")
print("         'custom' entry remain.")

# ---- Scenario 2: /studio sidebar has NO icon column, has an Integrations nav link
r = client.get("/studio")
page = r.get_data(as_text=True)
assert '<div class="integrations">' not in page, "old icon column should be gone"
assert 'class="integ-item"' not in page, "old icon-column items should be gone"
assert 'href="/integrations"' in page, "sidebar should link to the Integrations page"
assert "Integrations" in page
print("PASSED — Scenario 2: the old icon column is gone; the sidebar has a plain Integrations nav link.")

# ---- Scenario 3: /integrations renders 4 real cards, no Healthcare, and no
# card for the freeform "custom" entry (nothing to connect/no credentials) --
r = client.get("/integrations")
page = r.get_data(as_text=True)
assert r.status_code == 200
for key, cfg in studio.CONNECTORS.items():
    if cfg.get("freeform"):
        assert cfg["label"] not in page, f"freeform '{key}' should NOT get an Integrations card"
        continue
    assert cfg["label"] in page, f"missing card for {key}"
    assert f'/studio/new/{key}' in page
assert "Healthcare" not in page
assert 'nav-integrations active' in page, "Integrations nav link should show active on its own page"
print("PASSED — Scenario 3: /integrations renders one card per REAL connector (Gmail, Slack, GitHub,")
print("         Helpdesk), Healthcare absent, and no card for the freeform 'custom' entry.")

# ---- Scenario 4: Gmail card shows "Connect Gmail" when not connected ------
assert "Connect Gmail" in page
assert "/oauth/gmail/connect" in page
assert "Connected as" not in page
print("PASSED — Scenario 4: Gmail's card shows a real 'Connect Gmail' action when no account is connected.")

# ---- Scenario 5: /oauth/gmail/connect redirects to Google's real consent screen
r = client.get("/oauth/gmail/connect", follow_redirects=False)
assert r.status_code in (301, 302, 303, 307, 308), r.status_code
location = r.headers["Location"]
assert location.startswith("https://accounts.google.com/o/oauth2/v2/auth?"), location
assert "redirect_uri=http%3A%2F%2F127.0.0.1%3A5008%2Foauth%2Fgmail%2Fcallback" in location, location
assert studio.GMAIL_OAUTH_REDIRECT_URI == "http://127.0.0.1:5008/oauth/gmail/callback"
with client.session_transaction() as sess:
    assert sess.get("gmail_oauth_state"), "state should be stashed in session for the callback to verify"
print("PASSED — Scenario 5: clicking Connect Gmail redirects the browser to Google's real OAuth consent page.")

# ---- Scenario 6: callback completes the connection (Google's HTTP calls mocked)
import json as _json, io as _io
with client.session_transaction() as sess:
    state = sess["gmail_oauth_state"]

call_log = []
def fake_urlopen(req, timeout=15):
    call_log.append(req.full_url)
    if req.full_url == studio.connectors_gmail.TOKEN_URL:
        body = _json.dumps({"access_token": "AT", "refresh_token": "RT", "expires_in": 3600}).encode()
    else:
        body = _json.dumps({"emailAddress": "kowsick.another@gmail.com"}).encode()
    cm = mock.MagicMock()
    cm.__enter__.return_value.read.return_value = body
    return cm

with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
    r = client.get(f"/oauth/gmail/callback?code=abc&state={state}", follow_redirects=True)
page = r.get_data(as_text=True)
assert "Connected Gmail as kowsick.another@gmail.com" in page or "Connected as kowsick.another@gmail.com" in page, page
assert studio.connectors_gmail.get_connected_account() == "kowsick.another@gmail.com"
print("PASSED — Scenario 6: the OAuth callback exchanges the code and shows the newly connected Gmail address.")

# ---- Scenario 7: Gmail card now shows "Connected as ..." + Disconnect -----
r = client.get("/integrations")
page = r.get_data(as_text=True)
assert "Connected as kowsick.another@gmail.com" in page
assert "/oauth/gmail/disconnect" in page
print("PASSED — Scenario 7: Gmail's card reflects the live connection with a Disconnect action.")

# ---- Scenario 8: a mismatched/missing state is rejected --------------------
r = client.get("/oauth/gmail/callback?code=abc&state=wrong", follow_redirects=True)
page = r.get_data(as_text=True)
assert "stale or tampered" in page.lower()
print("PASSED — Scenario 8: a callback with a bad/missing state is rejected instead of connecting blindly.")

# ---- Scenario 9: disconnect clears it -------------------------------------
r = client.post("/oauth/gmail/disconnect", follow_redirects=True)
page = r.get_data(as_text=True)
assert studio.connectors_gmail.get_connected_account() is None
assert "Connect Gmail" in page
print("PASSED — Scenario 9: Disconnect clears the live Gmail connection; the card reverts to 'Connect Gmail'.")

print()
print("ALL STUDIO INTEGRATIONS PAGE (v2) SCENARIOS PASSED")
