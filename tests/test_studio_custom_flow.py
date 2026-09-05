"""
End-to-end test of the 2026-08-25 "describe your own app" flow through
studio.py itself: a brand-new workflow (connector=None), first message names
no known connector ("clothing brand") — this must immediately become a
'custom' workflow AND forward that same text as the real first build
request (not just a "connected!" confirmation, and not "I didn't catch an
app in that").
"""
import sys, types, json, unittest.mock as mock

sys.path.insert(0, "/home/claude/pilant-agent")
import os
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

# ---- Scenario 1: "custom" is registered but excluded from the pickable list
labels = studio._connector_list_text()
assert "Custom" not in labels, f"'custom' should be excluded from the pickable connector list, got: {labels!r}"
assert "Gmail" in labels and "Slack" in labels and "GitHub" in labels and "Helpdesk" in labels
print(f"PASSED — Scenario 1: connector list text excludes 'custom': {labels!r}")

# ---- Scenario 2: Integrations page doesn't show a Custom card -------------
r = client.get("/integrations")
page = r.get_data(as_text=True)
assert "Custom interface" not in page, "the freeform 'custom' entry should not get an Integrations card"
print("PASSED — Scenario 2: /integrations page has no card for the freeform 'custom' entry.")

# ---- Scenario 3: a free-form first message in a fresh workflow immediately
# becomes a 'custom' workflow AND triggers a real build attempt, not just a
# "connected!" confirmation or an "I didn't catch an app" bounce-back.
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

ask_step = FakeResp(
    FakeMessage(tool_calls=[FakeToolCall("tc1", "ask_user", json.dumps({
        "question": "What's the main purpose, what should it show, any style preference, and which tools should it connect to?",
    }))]),
    "tool_calls",
)
client_mock = mock.Mock()
client_mock.chat.completions.create.side_effect = [ask_step]

r = client.post("/studio/new", follow_redirects=True)
wf_id = studio.WORKFLOW_ORDER[0]
assert studio.WORKFLOWS[wf_id]["connector"] is None, "a brand-new workflow should start with no connector chosen"

with mock.patch("agent_custom.client", client_mock):
    r = client.post("/studio/message", data={"text": "clothing brand"}, follow_redirects=True)

wf = studio.WORKFLOWS[wf_id]
assert wf["connector"] == "custom", f"expected 'clothing brand' (no known app named) to fall back to 'custom', got: {wf['connector']!r}"
assert any("purpose" in m["text"].lower() for m in wf["messages"] if m["role"] == "assistant"), (
    f"expected the custom agent's real clarifying question in the transcript, got: {wf['messages']}"
)
# Critically: NOT the old "I didn't catch an app in that" bounce-back, and NOT a bare
# "Connected to Custom interface" confirmation with no real build attempt — a REAL model
# call happened (proven by the mock actually being invoked).
assert client_mock.chat.completions.create.call_count == 1, "expected the custom agent to actually run on the first message"
assert not any("didn't catch an app" in m["text"] for m in wf["messages"])
print("PASSED — Scenario 3: 'clothing brand' (unmatched) immediately became a 'custom' workflow")
print("         and triggered a REAL build attempt (a genuine clarifying question came back),")
print("         not a connector-name bounce-back.")

print()
print("ALL STUDIO CUSTOM-FLOW SCENARIOS PASSED")
