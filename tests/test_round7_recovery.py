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
    {"from": "CredAbility <marketing@mail.credability.co.uk>", "subject": "Kowsick, a new credit card added",
     "date": "Aug 25, 2026", "snippet": "Check if you're eligible - it won't harm your score", "unread": True, "id": "m1"},
    {"from": "\"McDonald's\" <no-reply@hello.mcdonalds.co.uk>", "subject": "Daily deals have DROPPED",
     "date": "Aug 25, 2026", "snippet": "Start your side mission", "unread": True, "id": "m2"},
]
connectors_gmail.get_gmail_messages = mock.Mock(return_value=FAKE_MESSAGES)

import agent_gmail
agent_gmail.get_gmail_messages = connectors_gmail.get_gmail_messages


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


step1 = FakeResp(
    FakeMessage(tool_calls=[FakeToolCall("tc1", "get_gmail_messages",
        json.dumps({"unread_only": True, "query": "", "limit": 10}))]),
    "tool_calls",
)

# Round 7 (2026-08-25): a real live render came back with the STRUCTURE correctly recovered
# (round 6's fix worked — all rows parsed) but with literal backslash-u-0027 text visible in the
# rendered page instead of real apostrophes ("you're eligible" shown verbatim instead of
# "you're eligible"). These Python string values are built to already contain the raw 6
# characters backslash + "u0027" as real content (NOT a real apostrophe) — exactly what a
# doubly-escaped wire payload decodes to after ONE json.loads pass on the outer envelope, which
# is exactly what run_agent does to tc.function.arguments. json.dumps below re-escapes that
# literal backslash for the wire (producing "\\u0027" in the JSON source), and run_agent's own
# json.loads decodes it right back to this same literal text — faithfully reproducing the live
# bug without needing the original raw log.
_ESC = "\\u0027"  # literal 6 chars: backslash, u, 0, 0, 2, 7 — NOT a real apostrophe
components_with_double_escaped_apostrophes = [
    {
        "type": "list",
        "rows": [
            {
                "name": "CredAbility <marketing@mail.credability.co.uk>",
                "note": f"Check if you{_ESC}re eligible - it won{_ESC}t harm your score",
                "badge": {"text": "Unread", "tone": "warning"},
            },
            {
                "name": f"McDonald{_ESC}s <no-reply@hello.mcdonalds.co.uk>",
                "note": "Start your side mission",
                "badge": {"text": "Unread", "tone": "warning"},
            },
        ],
    }
]

step2 = FakeResp(
    FakeMessage(tool_calls=[FakeToolCall("tc2", "render_view", json.dumps({
        "heading": "2 unread emails",
        "components": components_with_double_escaped_apostrophes,
    }))]),
    "tool_calls",
)

client_mock = mock.Mock()
client_mock.chat.completions.create.side_effect = [step1, step2]
agent_gmail.client = client_mock

result = agent_gmail.run_agent("show my unread emails as a task list", max_steps=8, verbose=True)
print()
print("RESULT KEYS:", list(result.keys()))
assert "render" in result, f"expected a render, got: {result}"
rows = result["render"]["components"][0]["rows"]
note0 = rows[0]["note"]
name1 = rows[1]["name"]
print("row 0 note:", repr(note0))
print("row 1 name:", repr(name1))
assert "\\u0027" not in note0, f"literal \\u0027 leaked into rendered content: {note0!r}"
assert "you're eligible" in note0 and "won't harm" in note0, f"apostrophes not correctly unescaped: {note0!r}"
assert "\\u0027" not in name1, f"literal \\u0027 leaked into rendered content: {name1!r}"
assert "McDonald's" in name1, f"expected a real apostrophe in McDonald's, got: {name1!r}"
print("PASSED — round 7: literal \\u0027 double-escape artifacts are unescaped into real")
print("         apostrophes before reaching the rendered page.")
