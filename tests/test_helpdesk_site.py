import sys, json
from unittest.mock import patch, MagicMock

sys.path.insert(0, "/home/claude/pilant-agent")
import helpdesk_site as hs

app = hs.app
app.config["TESTING"] = True

def make_tool_call(id, name, args):
    tc = MagicMock()
    tc.id = id
    tc.function.name = name
    tc.function.arguments = json.dumps(args)
    tc.model_dump.return_value = {"id": id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
    return tc

def make_resp(tool_calls=None, content=None):
    msg = MagicMock()
    msg.tool_calls = tool_calls
    msg.content = content
    resp = MagicMock()
    resp.choices = [MagicMock(message=msg)]
    return resp

client = app.test_client()

print("=== TEST 1: login ===")
r = client.post("/login", data={"username": "kowsick", "password": "owner123"}, follow_redirects=True)
assert r.status_code == 200
assert b"Ask for exactly" in r.data or b"query" in r.data
print("PASS: logged in, landed on", r.request.path)

print("\n=== TEST 2: non-ambiguous query -> rendered screen ===")
import agent_helpdesk as ah
with patch.object(ah.client.chat.completions, "create") as mock_create:
    mock_create.side_effect = [
        make_resp(tool_calls=[make_tool_call("1", "get_tickets", {"status": "open"})]),
        make_resp(tool_calls=[make_tool_call("2", "render_view", {
            "heading": "Open tickets",
            "meta": "4 open tickets",
            "components": [
                {"type": "list", "title": "Open tickets", "rows": [
                    {"name": "TK-1042: Can't reset password", "note": "priya@acme.io", "badge": {"text": "high", "tone": "warning"}},
                ]}
            ]
        })]),
    ]
    r = client.post("/generate", data={"query": "what open tickets need attention?"})
    assert r.status_code == 200
    assert b"Open tickets" in r.data
    assert b"Can&#39;t reset password" in r.data or b"Can't reset password" in r.data or b"reset password" in r.data
    print("PASS: rendered screen contains expected content")

print("\n=== TEST 3: ambiguous query -> clarify -> answer -> render ===")
client2 = app.test_client()
client2.post("/login", data={"username": "analyst", "password": "analyst123"}, follow_redirects=True)
with patch.object(ah.client.chat.completions, "create") as mock_create:
    mock_create.side_effect = [
        make_resp(tool_calls=[make_tool_call("1", "ask_user", {"question": "Do you mean escalated or all open tickets?"})]),
    ]
    r = client2.post("/generate", data={"query": "what needs attention?"})
    assert r.status_code == 200
    assert b"Do you mean escalated" in r.data
    assert b"Your answer" in r.data
    print("PASS: clarifying question shown, input relabeled")

    mock_create.side_effect = [
        make_resp(tool_calls=[make_tool_call("2", "get_tickets", {"status": "escalated"})]),
        make_resp(tool_calls=[make_tool_call("3", "render_view", {
            "heading": "Escalated tickets",
            "components": [
                {"type": "list", "title": "Escalated", "rows": [
                    {"name": "TK-1041: Invoice shows duplicate charge", "note": "marcus@globex.com", "badge": {"text": "critical", "tone": "critical"}},
                ]}
            ]
        })]),
    ]
    r = client2.post("/generate", data={"query": "escalated ones"})
    assert r.status_code == 200
    assert b"Escalated tickets" in r.data
    print("PASS: resumed conversation rendered final screen")

print("\n=== TEST 4: logged-out access redirects to login ===")
client3 = app.test_client()
r = client3.get("/", follow_redirects=False)
assert r.status_code == 302
assert "/login" in r.headers["Location"]
print("PASS: unauthenticated redirect works")

print("\nALL HELPDESK_SITE TESTS PASSED")
