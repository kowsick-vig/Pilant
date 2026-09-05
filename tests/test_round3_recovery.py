import sys, json, types, unittest.mock as mock

sys.path.insert(0, "/home/claude/pilant-agent")

fake_openai = types.ModuleType("openai")
class FakeOpenAI:
    def __init__(self, *a, **k):
        pass
fake_openai.OpenAI = FakeOpenAI
sys.modules["openai"] = fake_openai

import connectors_gmail

# Real-shaped fake inbox matching the real subjects referenced in the corrupted
# components string below, so the fabrication guardrail sees them as grounded.
FAKE_MESSAGES = [
    {"from": "TryHackMe <donotreply@tryhackme.com>", "subject": "Your first room is waiting",
     "date": "Aug 25, 2026", "snippet": "Your first room is waiting, so is your 25% reward.",
     "unread": True, "id": "m1"},
    {"from": "Uber Eats <ubereats@uber.com>", "subject": "New McDonald's Side Missions Menu",
     "date": "Aug 25, 2026", "snippet": "Take a delicious detour with the new McDonald's Side Missions Menu",
     "unread": True, "id": "m2"},
    {"from": "Groq <developer@groq.co>", "subject": "Important GroqCloud notice",
     "date": "Aug 25, 2026", "snippet": "Important GroqCloud notice. Switch your models now.",
     "unread": True, "id": "m3"},
    {"from": "McDonald's <no-reply@hello.mcdonalds.co.uk>", "subject": "Start your side mission",
     "date": "Aug 25, 2026", "snippet": "Start your side mission",
     "unread": True, "id": "m4"},
    {"from": "PSBE Cyber News Group <marcus-cybernewsgroup.co.uk@shared1.ccsend.com>",
     "subject": "The uncomfortable truth about breaches",
     "date": "Aug 25, 2026",
     "snippet": "Hi Kowsick, Here's the uncomfortable truth: most organizations that get breached already had MFA enforced, passwords under control, and a fresh security certificate on the wall. That's because",
     "unread": True, "id": "m7"},
    {"from": "hello@gojiberry.ai", "subject": "Mission Accomplished",
     "date": "Aug 25, 2026",
     "snippet": "Hello kowsick, Mission Accomplished! Your AI Agent has just delivered 22 new leads straight to your pipeline. These aren't just any leads – they're",
     "unread": True, "id": "m10"},
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


# Step 1: a real tool call, fetching real data.
step1 = FakeResp(
    FakeMessage(tool_calls=[FakeToolCall("tc1", "get_gmail_messages",
        json.dumps({"query": "is:unread", "unread_only": True, "limit": 10}))]),
    "tool_calls",
)

# Step 2: a REAL tool call (finish_reason="tool_calls") to render_view, but the
# "components" argument is a corrupted string — the EXACT real content from the
# user's round-3 terminal log, blanket single-quoted with unescaped apostrophes.
corrupted_components = "[{'type': 'list', 'rows': [{'name': 'Email 1: TryHackMe <donotreply@tryhackme.com> - Snippet 1 Your first room is waiting, so is your 25% reward.', 'note': 'Your first room is waiting, so is your 25% reward.', 'badge': {'text': 'Unread', 'tone': 'warning'}}, {'name': 'Email 2: Uber Eats <ubereats@uber.com> - Snippet 2 Take a delicious detour with the new McDonald's Side Missions Menu', 'note': 'Take a delicious detour with the new McDonald's Side Missions Menu', 'badge': {'text': 'Unread', 'tone': 'warning'}}, {'name': 'Email 3: Groq <developer@groq.co> - Snippet 3 Important GroqCloud notice. Switch your models now.', 'note': 'Important GroqCloud notice. Switch your models now.', 'badge': {'text': 'Unread', 'tone': 'warning'}}, {'name': 'Email 4: McDonald's <no-reply@hello.mcdonalds.co.uk> - Snippet 4 Start your side mission', 'note': 'Start your side mission', 'badge': {'text': 'Unread', 'tone': 'warning'}}, {'name': 'Email 7: PSBE Cyber News Group <marcus-cybernewsgroup.co.uk@shared1.ccsend.com> - Snippet 7 Hi Kowsick, Here's the uncomfortable truth: most organizations that get breached already had MFA enforced, passwords under control, and a fresh security certificate on the wall. That's because', 'note': 'Hi Kowsick, Here's the uncomfortable truth: most organizations that get breached already had MFA enforced, passwords under control, and a fresh security certificate on the wall. That's because', 'badge': {'text': 'Unread', 'tone': 'warning'}}, {'name': 'Email 10: hello@gojiberry.ai - Snippet 10 Hello kowsick, Mission Accomplished! Your AI Agent has just delivered 22 new leads straight to your pipeline. These aren't just any leads – they're', 'note': 'Hello kowsick, Mission Accomplished! Your AI Agent has just delivered 22 new leads straight to your pipeline. These aren't just any leads – they're', 'badge': {'text': 'Unread', 'tone': 'warning'}}]}]"

step2 = FakeResp(
    FakeMessage(tool_calls=[FakeToolCall("tc2", "render_view", json.dumps({
        "heading": "6 unread emails",
        "meta": "as a task list",
        "components": corrupted_components,
    }))]),
    "tool_calls",
)

client_mock = mock.Mock()
client_mock.chat.completions.create.side_effect = [step1, step2]
agent_gmail.client = client_mock

result = agent_gmail.run_agent("show my unread emails as a task list", max_steps=8, verbose=True)
print()
print("RESULT KEYS:", list(result.keys()))
assert "render" in result, f"expected a recovered render on step 2 (real tool_calls, corrupted components string), got: {result}"
rows = result["render"]["components"][0]["rows"]
assert len(rows) == 6, f"expected 6 recovered rows, got {len(rows)}"
names = [r["name"] for r in rows]
assert any("McDonald's" in n for n in names), "apostrophe in McDonald's should survive intact"
assert any("Here's the uncomfortable truth" in n for n in names), "apostrophe in Here's should survive intact"
assert any("aren't just any leads" in n and "they're" in n for n in names), "aren't/they're should survive intact"
print("PASSED — round 3's real tool_calls + blanket-quoted 'components' string recovered via the")
print("         heuristic quote repair: all 6 rows parsed, every genuine apostrophe preserved.")
