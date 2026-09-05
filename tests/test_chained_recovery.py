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
agent_gmail.get_gmail_messages = connectors_gmail.get_gmail_messages

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

# The EXACT text from the user's real step-1 terminal log (round two bug):
# chained get_gmail_messages(...) + render_view(...) with fabricated
# placeholder rows, written as plain text, never a real tool call.
step1_text = (
    'get_gmail_messages(query="is:unread", unread_only=True, limit=10)\n'
    'render_view(components=[\n'
    '    {"type": "list", "rows": [\n'
    '        {"name": "Email 1", "note": "Snippet 1", "badge": {"text": "Read", "tone": "good"}},\n'
    '        {"name": "Email 2", "note": "Snippet 2", "badge": {"text": "Unread", "tone": "warning"}},\n'
    '        {"name": "Email 3", "note": "Snippet 3", "badge": {"text": "Unread", "tone": "warning"}}\n'
    '    ]}\n'
    '], heading="3 Unread Emails", meta="Recent emails that need your attention")'
)

# Step 2: model finally does the right thing after seeing the real data +
# rejection reason, using the ACTUAL real subjects this time.
step2_render = json.dumps({
    "heading": "2 unread emails",
    "meta": "as a task list",
    "components": [
        {"type": "list", "rows": [
            {"name": "Groq <developer@groq.co>: Compound is being decommissioned",
             "note": "Important GroqCloud notice.", "badge": {"tone": "warning", "text": "Unread"}},
            {"name": "McDonald's <no-reply@hello.mcdonalds.co.uk>: Daily deals have DROPPED",
             "note": "Start your side mission", "badge": {"tone": "warning", "text": "Unread"}},
        ]}
    ],
})
step2_text = f'render_view(**{step2_render})' if False else None  # placeholder, real call below

client_mock = mock.Mock()
client_mock.chat.completions.create.side_effect = [
    FakeResp(FakeMessage(content=step1_text), "stop"),
    FakeResp(FakeMessage(tool_calls=None, content=json.dumps({
        "name": "render_view",
        "parameters": json.loads(step2_render),
    })), "stop"),
]
agent_gmail.client = client_mock

result = agent_gmail.run_agent("show my unread emails as a task list", max_steps=8, verbose=True)
print()
print("RESULT KEYS:", list(result.keys()))
assert agent_gmail.get_gmail_messages.call_count == 1, f"expected exactly 1 real fetch, got {agent_gmail.get_gmail_messages.call_count}"
assert "render" in result, f"expected a recovered render on step 2, got: {result}"
rows = result["render"]["components"][0]["rows"]
assert len(rows) == 2 and "Groq" in rows[0]["name"] and "McDonald" in rows[1]["name"]
print("PASSED — chained get_gmail_messages+render_view(placeholder) recovered: fetched once for real,")
print("         rejected the fabricated placeholder render, and the corrected next attempt succeeded.")
