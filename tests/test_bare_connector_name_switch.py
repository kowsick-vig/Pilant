"""
Regression test for the live-reproduced 2026-08-25 bug (screenshot):
1. User types "hi" into a brand-new workflow (connector=None) -> too vague
   to match anything, falls to 'custom', which asks its own clarifying
   question (by design).
2. User replies "gmail" -> meant "connect me to my real Gmail", but this
   used to be swallowed as literal free-text answer content for the
   'custom' connector's pending clarify. 'custom' then rendered a fake,
   alarming "Emails from Unverified Senders" dashboard (fabricated unread
   counts, phishing-style subject lines) -- entirely invented, not real
   Gmail data.
3. A follow-up "give me unread emails" continued in the same fake-data mode.

Fix: studio._is_bare_connector_name() recognizes a message that is EXACTLY
a real connector's name and nothing else (even without a connect-ish verb,
even mid-clarify) and treats it as a switch to that real connector instead
of forwarding it as content to whatever connector/clarify is active.
"""
import sys, os, types, json, unittest.mock as mock

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

# ---- Scenario 1: unit-level bare-name matching -----------------------------
assert studio._is_bare_connector_name("gmail") == "gmail"
assert studio._is_bare_connector_name("Gmail") == "gmail"
assert studio._is_bare_connector_name("gmail!") == "gmail"
assert studio._is_bare_connector_name("  Gmail.  ") == "gmail"
assert studio._is_bare_connector_name("Slack") == "slack"
# NOT a bare name -- has other words, so this must NOT auto-switch;
# it should fall through to normal handling (verb-gated switch or a real
# request/answer for whatever connector is active).
assert studio._is_bare_connector_name("gmail please") is None
assert studio._is_bare_connector_name("a gmail-style inbox") is None
assert studio._is_bare_connector_name("custom") is None  # 'custom' itself excluded
print("PASSED — Scenario 1: _is_bare_connector_name matches only an exact,")
print("         standalone connector name/label, nothing looser.")

# ---- Scenario 2: end-to-end -- the exact live-reproduced sequence ---------
class FakeToolCallFn:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments

class FakeToolCall:
    def __init__(self, id_, name, arguments):
        self.id = id_
        self.function = FakeToolCallFn(name, arguments)
    def model_dump(self):
        return {"id": self.id, "type": "function",
                "function": {"name": self.function.name, "arguments": self.function.arguments}}

class FakeMessage:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls

class FakeChoice:
    def __init__(self, message, finish_reason):
        self.message = message
        self.finish_reason = finish_reason

class FakeResp:
    def __init__(self, message, finish_reason):
        self.choices = [FakeChoice(message, finish_reason)]

FAKE_MESSAGES = [
    {"from": "a@x.com", "subject": "Alpha", "date": "Aug 25, 2026", "snippet": "First", "unread": True, "id": "m1"},
    {"from": "b@x.com", "subject": "Beta", "date": "Aug 25, 2026", "snippet": "Second", "unread": True, "id": "m2"},
]
studio.connectors_gmail.get_gmail_messages = mock.Mock(return_value=FAKE_MESSAGES)
studio.agent_gmail.get_gmail_messages = studio.connectors_gmail.get_gmail_messages
studio.agent_gmail.TOOL_FUNCTIONS["get_gmail_messages"] = studio.connectors_gmail.get_gmail_messages

# Step 1: "hi" -> ambiguous, custom connector, asks a clarifying question.
ask_step = FakeResp(
    FakeMessage(tool_calls=[FakeToolCall("tc0", "ask_user", json.dumps({
        "question": "I'd love to help you build an interface! What would you like to build?",
    }))]),
    "tool_calls",
)
client_mock_0 = mock.Mock()
client_mock_0.chat.completions.create.side_effect = [ask_step]
client.post("/studio/new", follow_redirects=True)
wf_id = studio.WORKFLOW_ORDER[0]
with mock.patch("agent_custom.client", client_mock_0):
    client.post("/studio/message", data={"text": "hi"}, follow_redirects=True)

wf = studio.WORKFLOWS[wf_id]
assert wf["connector"] == "custom", f"expected 'hi' to land on custom (ambiguous), got {wf['connector']!r}"
assert wf["agent_messages"] is not None, "a clarify should be pending"

# Step 2: "gmail" -- should switch to the REAL gmail connector, not get
# swallowed as an answer to custom's pending clarify.
client_mock_1 = mock.Mock()  # must NOT be called at all -- this is a pure routing switch
client_mock_1.chat.completions.create.side_effect = AssertionError("custom's model should not be called for a bare 'gmail' reply")
with mock.patch("agent_custom.client", client_mock_1):
    client.post("/studio/message", data={"text": "gmail"}, follow_redirects=True)

wf = studio.WORKFLOWS[wf_id]
assert wf["connector"] == "gmail", f"expected bare 'gmail' reply to switch to the real gmail connector, got {wf['connector']!r}"
assert wf["agent_messages"] is None, "switching connectors must clear the old pending clarify"
assert wf["last_render"] is None, "a pure connector switch must not fabricate/render anything"

page = client.get("/studio").get_data(as_text=True)
assert "Unverified Senders" not in page and "compromised" not in page, (
    "no fabricated phishing-style mockup content should appear after switching to real gmail"
)
print("PASSED — Scenario 2a: after 'hi' -> custom clarify -> a bare 'gmail' reply switches")
print("         to the REAL Gmail connector instead of being fed to custom as answer text.")

# Step 3: a real follow-up now actually fetches real data through gmail.
fetch_step = FakeResp(
    FakeMessage(tool_calls=[FakeToolCall("tc1", "get_gmail_messages", json.dumps({"unread_only": True, "query": "", "limit": 10}))]),
    "tool_calls",
)
render_step = FakeResp(
    FakeMessage(tool_calls=[FakeToolCall("tc2", "render_view", json.dumps({
        "heading": "Unread emails",
        "components": [{"type": "list", "rows": [{"name": "Alpha"}, {"name": "Beta"}]}],
    }))]),
    "tool_calls",
)
client_mock_2 = mock.Mock()
client_mock_2.chat.completions.create.side_effect = [fetch_step, render_step]
with mock.patch("agent_gmail.client", client_mock_2):
    client.post("/studio/message", data={"text": "give me unread emails"}, follow_redirects=True)

wf = studio.WORKFLOWS[wf_id]
assert wf["connector"] == "gmail"
assert wf["last_render"] is not None and wf["last_render"]["heading"] == "Unread emails"
studio.connectors_gmail.get_gmail_messages.assert_called()

page = client.get("/studio").get_data(as_text=True)
assert "Alpha" in page and "Beta" in page
assert "Unverified Senders" not in page and "compromised" not in page
print("PASSED — Scenario 2b: the follow-up 'give me unread emails' fetched and rendered")
print("         real Gmail data -- no fabricated content anywhere in the sequence.")

print()
print("ALL BARE-CONNECTOR-NAME SWITCH SCENARIOS PASSED")
