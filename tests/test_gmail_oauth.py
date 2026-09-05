"""
Offline test for connectors_gmail.py's real-OAuth additions (2026-08-25):
get_authorization_url, exchange_code_for_tokens, get_connected_account,
disconnect, and _get_access_token()'s new "live connection wins over .env"
priority. No live network access from this sandbox, so urllib.request.urlopen
is mocked at the point connectors_gmail.py actually calls it.
"""
import sys, os, json, io, types, unittest.mock as mock

sys.path.insert(0, "/home/claude/pilant-agent")

# Give connectors_gmail a client id/secret to work with, bypassing the real .env
os.environ["GMAIL_CLIENT_ID"] = "test-client-id.apps.googleusercontent.com"
os.environ["GMAIL_CLIENT_SECRET"] = "test-client-secret"
os.environ.pop("GMAIL_REFRESH_TOKEN", None)

import connectors_gmail as cg

# ---- Scenario 1: get_authorization_url builds a real Google consent URL ----
url = cg.get_authorization_url("http://127.0.0.1:5008/oauth/gmail/callback", state="abc123")
assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?"), url
assert "client_id=test-client-id.apps.googleusercontent.com" in url
assert "redirect_uri=http%3A%2F%2F127.0.0.1%3A5008%2Foauth%2Fgmail%2Fcallback" in url
assert "access_type=offline" in url
assert "prompt=consent" in url
assert "state=abc123" in url
assert "scope=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fgmail.readonly" in url
print("PASSED — Scenario 1: get_authorization_url builds a correct real Google OAuth consent URL.")

# ---- Scenario 2: missing GMAIL_CLIENT_ID raises a clear RuntimeError -------
saved = os.environ.pop("GMAIL_CLIENT_ID")
try:
    try:
        cg.get_authorization_url("http://127.0.0.1:5008/oauth/gmail/callback")
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "GMAIL_CLIENT_ID" in str(e)
finally:
    os.environ["GMAIL_CLIENT_ID"] = saved
print("PASSED — Scenario 2: missing GMAIL_CLIENT_ID raises a clear, actionable RuntimeError.")


def _fake_urlopen_sequence(responses):
    """responses: list of (status_ok, payload_dict). status_ok False raises
    an HTTPError-like via urllib.error.HTTPError."""
    calls = []

    def _fake(req, timeout=15):
        calls.append(req.full_url)
        ok, payload = responses.pop(0)
        body = json.dumps(payload).encode("utf-8")
        if not ok:
            import urllib.error
            raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {}, io.BytesIO(body))
        cm = mock.MagicMock()
        cm.__enter__.return_value.read.return_value = body
        return cm

    return _fake, calls


# ---- Scenario 3: successful exchange_code_for_tokens connects a real account
responses = [
    (True, {"access_token": "AT1", "refresh_token": "RT1", "expires_in": 3600}),  # token exchange
    (True, {"emailAddress": "kowsick.other@gmail.com"}),                          # profile lookup
]
fake_urlopen, calls = _fake_urlopen_sequence(responses)
with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
    email = cg.exchange_code_for_tokens("auth-code-xyz", "http://127.0.0.1:5008/oauth/gmail/callback")
assert email == "kowsick.other@gmail.com", email
assert cg.get_connected_account() == "kowsick.other@gmail.com"
assert cg._connected["refresh_token"] == "RT1"
assert calls[0] == cg.TOKEN_URL
assert calls[1] == f"{cg.API_ROOT}/profile"
print("PASSED — Scenario 3: exchange_code_for_tokens connects a real account and exposes its address.")

# ---- Scenario 4: a live-connected refresh token wins over GMAIL_REFRESH_TOKEN
os.environ["GMAIL_REFRESH_TOKEN"] = "env-token-should-not-be-used"
token_responses = [(True, {"access_token": "AT2", "expires_in": 3600})]
fake_urlopen2, calls2 = _fake_urlopen_sequence(token_responses)
with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen2):
    tok = cg._get_access_token()
assert tok == "AT2"
# confirm the refresh_token used in the POST body was RT1 (the live-connected one), not the env one
req_used = None
def _capture(req, timeout=15):
    global req_used
    req_used = req
    cm = mock.MagicMock()
    cm.__enter__.return_value.read.return_value = json.dumps({"access_token": "AT3", "expires_in": 3600}).encode()
    return cm
cg._token_cache["token"] = None
cg._token_cache["expires_at"] = 0
with mock.patch("urllib.request.urlopen", side_effect=_capture):
    cg._get_access_token()
body = req_used.data.decode()
assert "refresh_token=RT1" in body, body
assert "env-token-should-not-be-used" not in body
print("PASSED — Scenario 4: a live-connected account's refresh token wins over .env's GMAIL_REFRESH_TOKEN.")

# ---- Scenario 5: disconnect() clears the live connection, .env fallback resumes
cg.disconnect()
assert cg.get_connected_account() is None
cg._token_cache["token"] = None
cg._token_cache["expires_at"] = 0
req_used = None
with mock.patch("urllib.request.urlopen", side_effect=_capture):
    cg._get_access_token()
body = req_used.data.decode()
assert "refresh_token=env-token-should-not-be-used" in body, body
print("PASSED — Scenario 5: disconnect() clears the live account; .env's GMAIL_REFRESH_TOKEN is used again.")

# ---- Scenario 6: exchange_code_for_tokens with no refresh_token in the response fails loudly
responses6 = [(True, {"access_token": "AT4", "expires_in": 3600})]  # no refresh_token
fake_urlopen6, _ = _fake_urlopen_sequence(responses6)
with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen6):
    try:
        cg.exchange_code_for_tokens("auth-code-2", "http://127.0.0.1:5008/oauth/gmail/callback")
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "refresh token" in str(e).lower()
print("PASSED — Scenario 6: a sign-in that returns no refresh_token fails loudly instead of connecting a doomed session.")

print()
print("ALL GMAIL OAUTH SCENARIOS PASSED")
