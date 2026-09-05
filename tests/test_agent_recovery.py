import sys, json, types, unittest.mock as mock

sys.path.insert(0, "/home/claude/pilant-agent")

fake_openai = types.ModuleType("openai")
class FakeOpenAI:
    def __init__(self, *a, **k):
        pass
fake_openai.OpenAI = FakeOpenAI
sys.modules["openai"] = fake_openai

import connectors_gmail

FAKE_MESSAGES = [
    {"from": "Groq <developer@groq.co>", "subject": "Compound is being decommissioned",
     "date": "Aug 24, 2026", "snippet": "Important GroqCloud notice.", "unread": True, "id": "m1"},
    {"from": "McDonald's <no-reply@hello.mcdonalds.co.uk>", "subject": "Daily deals have DROPPED",
     "date": "Aug 24, 2026", "snippet": "Start your side mission", "unread": True, "id": "m2"},
]
connectors_gmail.get_gmail_messages = mock.Mock(return_value=FAKE_MESSAGES)

import agent_gmail

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


# --- Scenario A: real tool_calls for step 1+2, then the JSON-text render_view
# recoverable pattern from the actual bug, WITH heading present this time (a
# full, non-truncated version of what the log showed cut off at 500 chars).
step1 = FakeResp(
    FakeMessage(tool_calls=[FakeToolCall("tc1", "get_gmail_messages", json.dumps({"unread_only": True, "limit": 10, "query": "in:inbox is:unread"}))]),
    "tool_calls",
)

components_obj = [
    {"type": "list", "rows": [
        {"name": "Groq <developer@groq.co>: Compound is being decommissioned",
         "note": "Important GroqCloud notice.", "badge": {"tone": "warning", "text": "Unread"}},
        {"name": "McDonald's <no-reply@hello.mcdonalds.co.uk>: Daily deals have DROPPED",
         "note": "Start your side mission", "badge": {"tone": "warning", "text": "Unread"}},
    ]}
]
render_json_text = json.dumps({
    "name": "render_view",
    "parameters": {
        "heading": "2 unread emails",
        "meta": "as a task list",
        # repr() is what actually produces valid single-quoted Python-literal
        # syntax with embedded apostrophes correctly escaped (\'), matching
        # what the model's own dict-to-text conversion does — a naive
        # character replace would break on "McDonald's" the way a first
        # draft of this test script did.
        "components": repr(components_obj),
    }
})
step2 = FakeResp(FakeMessage(content=render_json_text), "stop")

client_mock = mock.Mock()
client_mock.chat.completions.create.side_effect = [step1, step2]
agent_gmail.client = client_mock

result = agent_gmail.run_agent("show my unread emails as a task list", max_steps=8, verbose=True)
print()
print("SCENARIO A RESULT KEYS:", list(result.keys()))
assert "render" in result, f"expected a recovered render, got: {result}"
assert result["render"]["heading"] == "2 unread emails"
rows = result["render"]["components"][0]["rows"]
assert len(rows) == 2
assert "Groq" in rows[0]["name"]
print("SCENARIO A PASSED — recovered a JSON-text render_view with real data intact")
print()

# --- Scenario B: Python-call-syntax render_view recovery, with an emoji
# written as a raw UTF-16 surrogate escape (the real McDonald's-style bug),
# to prove the surrogate scrub keeps this from crashing on encode.
connectors_gmail.get_gmail_messages = mock.Mock(return_value=FAKE_MESSAGES)
step1b = FakeResp(
    FakeMessage(tool_calls=[FakeToolCall("tc1", "get_gmail_messages", json.dumps({"unread_only": True}))]),
    "tool_calls",
)
pycall_text = (
    "render_view(heading='2 unread emails', components=[{'type': 'list', 'rows': "
    "[{'name': 'Groq <developer@groq.co>: Compound is being decommissioned', 'note': 'Important GroqCloud notice.'}, "
    "{'name': 'McDonald\\'s <no-reply@hello.mcdonalds.co.uk>: Daily deals have DROPPED \\ud83d\\udd79\\ufe0f\\u200b', "
    "'note': 'Start your side mission'}]}])"
)
step2b = FakeResp(FakeMessage(content=pycall_text), "stop")

client_mock2 = mock.Mock()
client_mock2.chat.completions.create.side_effect = [step1b, step2b]
agent_gmail.client = client_mock2

result_b = agent_gmail.run_agent("show my unread emails", max_steps=8, verbose=True)
print()
print("SCENARIO B RESULT KEYS:", list(result_b.keys()))
assert "render" in result_b, f"expected a recovered render, got: {result_b}"
mcdonalds_name = result_b["render"]["components"][0]["rows"][1]["name"]
print("McDonald's row name (post-scrub):", repr(mcdonalds_name))
mcdonalds_name.encode("utf-8")  # must not raise
print("SCENARIO B PASSED — recovered Python-call-syntax render_view, surrogate scrub prevented a UnicodeEncodeError")
print()

# --- Scenario C: redundant get_gmail_messages written as text after data
# was already fetched — should be nudged, not re-executed, and should NOT
# burn through to max_steps if the model then does the right thing.
# (agent_gmail.py does `from connectors_gmail import get_gmail_messages` at
# import time, so the mock must be patched on agent_gmail's own name to
# actually take effect here — reassigning connectors_gmail's copy after
# import doesn't reach back into agent_gmail's already-bound reference.)
agent_gmail.get_gmail_messages = mock.Mock(return_value=FAKE_MESSAGES)
step1c = FakeResp(
    FakeMessage(tool_calls=[FakeToolCall("tc1", "get_gmail_messages", json.dumps({"unread_only": True}))]),
    "tool_calls",
)
step2c = FakeResp(FakeMessage(content="get_gmail_messages(limit=10, query='in:inbox is:unread', unread_only=True)"), "stop")
step3c = FakeResp(FakeMessage(content=render_json_text), "stop")

client_mock3 = mock.Mock()
client_mock3.chat.completions.create.side_effect = [step1c, step2c, step3c]
agent_gmail.client = client_mock3

result_c = agent_gmail.run_agent("show unread", max_steps=8, verbose=True)
# The freshly-bound mock for this scenario must see ZERO calls: step 1's
# real fetch went through TOOL_FUNCTIONS' own captured reference (a
# pre-existing test-harness quirk, module-load-time binding), and step 2's
# text-written get_gmail_messages call must be nudged away, not executed —
# that's the actual behavior under test here.
assert agent_gmail.get_gmail_messages.call_count == 0, "should NOT have re-executed the redundant text call"
assert "render" in result_c
print()
print("SCENARIO C PASSED — redundant get_gmail_messages-as-text nudged away, not re-executed, recovered next step")

print()
print("ALL RECOVERY SCENARIOS PASSED")
