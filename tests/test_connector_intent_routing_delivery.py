"""
Regression test for the 2026-08-25 bug: a brand-new workflow's first
message ("show my inbox", "what's in my email") used to fall straight
through to the 'custom' connector — which fabricates realistic-looking but
entirely invented sample data by design — because _match_connector only
recognized a connector's literal name/label, and "show my inbox" doesn't
contain the word "gmail". That produced fake content (invented names like
"John Doe") presented as if it were the person's real inbox.

Fix: studio._infer_connector_from_intent() recognizes domain keywords
(inbox/email/unread -> gmail, etc.) so a first message that clearly wants
real data gets routed to the real connector and acted on immediately,
instead of silently defaulting to the fabricating 'custom' connector.
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

# ---- Scenario 1: unit-level intent matching --------------------------------
assert studio._infer_connector_from_intent("show my inbox") == "gmail"
assert studio._infer_connector_from_intent("what's in my email") == "gmail"
assert studio._infer_connector_from_intent("any unread messages?") == "gmail"
assert studio._infer_connector_from_intent("what's in the #general channel") == "slack"
assert studio._infer_connector_from_intent("any open issues on the repo") == "github"
assert studio._infer_connector_from_intent("show escalated tickets") == "helpdesk"
# still falls through to None (-> custom) for a genuinely app-less description
assert studio._infer_connector_from_intent("clothing brand") is None
# a literal name still matches via the plain name path
assert studio._infer_connector_from_intent("gmail") == "gmail"
print("PASSED — Scenario 1: _infer_connector_from_intent recognizes real-data keywords")
print("         without requiring the connector's literal name.")

# ---- Scenario 2: _match_connector no longer false-matches "custom" --------
assert studio._match_connector("no custom sorting needed, just show recent stuff") is None
print("PASSED — Scenario 2: _match_connector ignores the 'custom' connector itself,")
print("         so ordinary sentences containing the word 'custom' don't mis-route.")

# ---- Scenario 3: end-to-end — a brand-new workflow's first message is a
# real-data request phrased without the connector's name, and it must NOT
# land on the fabricating 'custom' connector. ---------------------------
FAKE_MESSAGES = [
    {"from": "a@x.com", "subject": "Alpha", "date": "Aug 25, 2026", "snippet": "First", "unread": True, "id": "m1"},
    {"from": "b@x.com", "subject": "Beta", "date": "Aug 25, 2026", "snippet": "Second", "unread": False, "id": "m2"},
]
studio.connectors_gmail.get_gmail_messages = mock.Mock(return_value=FAKE_MESSAGES)
studio.agent_gmail.get_gmail_messages = studio.connectors_gmail.get_gmail_messages
studio.agent_gmail.TOOL_FUNCTIONS["get_gmail_messages"] = studio.connectors_gmail.get_gmail_messages


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


fetch_step = FakeResp(
    FakeMessage(tool_calls=[FakeToolCall("tc1", "get_gmail_messages", json.dumps({"unread_only": False, "query": "", "limit": 10}))]),
    "tool_calls",
)
render_step = FakeResp(
    FakeMessage(tool_calls=[FakeToolCall("tc2", "render_view", json.dumps({
        "heading": "Your inbox",
        "components": [{"type": "list", "rows": [{"name": "Alpha"}, {"name": "Beta"}]}],
    }))]),
    "tool_calls",
)

client.post("/studio/new", follow_redirects=True)  # connector=None
wf_id = studio.WORKFLOW_ORDER[0]  # WORKFLOW_ORDER is newest-first

client_mock = mock.Mock()
client_mock.chat.completions.create.side_effect = [fetch_step, render_step]
with mock.patch("agent_gmail.client", client_mock):
    client.post("/studio/message", data={"text": "show my inbox"}, follow_redirects=True)

wf = studio.WORKFLOWS[wf_id]
assert wf["connector"] == "gmail", f"expected 'show my inbox' to route to gmail, got {wf['connector']!r}"
assert wf["last_render"] is not None, "the real request should have been acted on immediately, not just a 'connected' confirmation"
assert wf["last_render"]["heading"] == "Your inbox"
# the real gmail tool was actually called — proof this used real data, not fabrication
studio.connectors_gmail.get_gmail_messages.assert_called()

r = client.get("/studio")
page = r.get_data(as_text=True)
assert "John Doe" not in page and "Jane" not in page, "no fabricated placeholder names should appear"
assert "Alpha" in page and "Beta" in page, "the real fetched data should be what's rendered"
print("PASSED — Scenario 3: a brand-new workflow's 'show my inbox' request routed straight to")
print("         the real Gmail connector and rendered real fetched data — not fabricated content.")

print()
print("ALL CONNECTOR INTENT ROUTING SCENARIOS PASSED")
