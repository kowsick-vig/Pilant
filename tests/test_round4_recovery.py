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
    {"from": "TryHackMe", "subject": "Let’s finish what you started \U0001F4BB",
     "date": "Aug 25, 2026", "snippet": "Your first room is waiting, so is your 25% reward.",
     "unread": True, "id": "m1"},
    {"from": "Uber Eats", "subject": "Get a free item at McDonald’s when you spend £20",
     "date": "Aug 25, 2026", "snippet": "Take a delicious detour with the new McDonald's Side Missions Menu",
     "unread": True, "id": "m2"},
    {"from": "Groq", "subject": "Compound is being decommissioned",
     "date": "Aug 25, 2026", "snippet": "Important GroqCloud notice. Switch your models now.",
     "unread": True, "id": "m3"},
    {"from": "McDonald's", "subject": "Daily deals have DROPPED \U0001F579️​",
     "date": "Aug 25, 2026", "snippet": "Start your side mission",
     "unread": True, "id": "m4"},
    {"from": "Squarespace", "subject": "[Action Required] Verify your Squarespace domain contact",
     "date": "Aug 25, 2026",
     "snippet": "Squarespace domains Welcome to Squarespace Domains You recently purchased a Squarespace domain. Please verify your email address below. ICANN requires email verification within 15 days of every domain",
     "unread": True, "id": "m5"},
    {"from": "Uber Eats", "subject": "Let’s switch it up",
     "date": "Aug 25, 2026", "snippet": "Give these restaurants a try",
     "unread": True, "id": "m6"},
    {"from": "PSBE Cyber News Group", "subject": "Cyber Essentials makes you compliant. Not completely threat-ready.",
     "date": "Aug 25, 2026",
     "snippet": "Hi Kowsick, Here’s the uncomfortable truth: most organizations that get breached already had MFA enforced, passwords under control, and a fresh security certificate on the wall. That’s because",
     "unread": True, "id": "m7"},
    {"from": "Some Store", "subject": "⏰ Your 20% off offer ends soon",
     "date": "Aug 25, 2026", "snippet": "The countdown to back-to-school savings is on",
     "unread": True, "id": "m8"},
    {"from": "Google", "subject": "You shared some Google Account data with W3Schools",
     "date": "Aug 25, 2026",
     "snippet": "Keep track of your Google Account data kowsick1vicky@gmail.com You’re receiving this email because you used Sign in with Google to sign in to W3Schools on August 25 at 8:59 AM. This email",
     "unread": True, "id": "m9"},
    {"from": "hello@gojiberry.ai", "subject": "\U0001F3AF Your AI Agent Just Delivered Fresh Leads!",
     "date": "Aug 25, 2026",
     "snippet": "\U0001F3AF Your AI Agent Just Delivered Fresh Leads! Hello kowsick, \U0001F680 Mission Accomplished! Your AI Agent has just delivered 22 new leads straight to your pipeline. These aren’t just any leads – they’re",
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


step1 = FakeResp(
    FakeMessage(tool_calls=[FakeToolCall("tc1", "get_gmail_messages",
        json.dumps({"query": "is:unread", "unread_only": True, "limit": 10}))]),
    "tool_calls",
)

# The EXACT real "components" string from the user's round-4 terminal log — a real
# tool_calls response whose components value is well-formed JSON that just stops
# before closing the last component object and the outer array (missing "}]").
truncated_components = '[{"type": "list", "rows": [{"name": "Let\\u2019s finish what you started \\ud83d\\udcbb", "note": "Your first room is waiting, so is your 25% reward.", "badge": {"text": "(3 unread)\\u00a0", "tone": "warning"}}, {"name": "Get a free item at McDonald\\u2019s when you spend \\u00a320", "note": "Take a delicious detour with the new McDonald\'s Side Missions Menu", "badge": {"text": "(3 unread)\\u00a0", "tone": "warning"}}, {"name": "Compound is being decommissioned", "note": "Important GroqCloud notice. Switch your models now.", "badge": {"text": "(3 unread)\\u00a0", "tone": "warning"}}, {"name": "Daily deals have DROPPED \\ud83d\\udd79\\ufe0f\\u200b", "note": "Start your side mission", "badge": {"text": "(3 unread)\\u00a0", "tone": "warning"}}, {"name": "[Action Required] Verify your Squarespace domain contact", "note": "Squarespace domains Welcome to Squarespace Domains You recently purchased a Squarespace domain. Please verify your email address below. ICANN requires email verification within 15 days of every domain", "badge": {"text": "(3 unread)\\u00a0", "tone": "warning"}}, {"name": "Let\\u2019s switch it up", "note": "Give these restaurants a try", "badge": {"text": "(3 unread)\\u00a0", "tone": "warning"}}, {"name": "Cyber Essentials makes you compliant. Not completely threat-ready.", "note": "Hi Kowsick, Here\\u2019s the uncomfortable truth: most organizations that get breached already had MFA enforced, passwords under control, and a fresh security certificate on the wall. That\\u2019s because", "badge": {"text": "(3 unread)\\u00a0", "tone": "warning"}}, {"name": "\\u23f0 Your 20% off offer ends soon", "note": "The countdown to back-to-school savings is on", "badge": {"text": "(3 unread)\\u00a0", "tone": "warning"}}, {"name": "You shared some Google Account data with W3Schools", "note": "Keep track of your Google Account data kowsick1vicky@gmail.com You\\u2019re receiving this email because you used Sign in with Google to sign in to W3Schools on August 25 at 8:59 AM. This email", "badge": {"text": "(3 unread)\\u00a0", "tone": "warning"}}, {"name": "\\ud83c\\udfaf Your AI Agent Just Delivered Fresh Leads!", "note": "\\ud83c\\udfaf Your AI Agent Just Delivered Fresh Leads! Hello kowsick, \\ud83d\\ude80 Mission Accomplished! Your AI Agent has just delivered 22 new leads straight to your pipeline. These aren\\u2019t just any leads \\u2013 they\\u2019re", "badge": {"text": "(3 unread)\\u00a0", "tone": "warning"}}]'

step2 = FakeResp(
    FakeMessage(tool_calls=[FakeToolCall("tc2", "render_view", json.dumps({
        "heading": "3 unread emails",
        "components": truncated_components,
    }))]),
    "tool_calls",
)

client_mock = mock.Mock()
client_mock.chat.completions.create.side_effect = [step1, step2]
agent_gmail.client = client_mock

result = agent_gmail.run_agent("show my unread emails as a task list", max_steps=8, verbose=True)
print()
print("RESULT KEYS:", list(result.keys()))
assert "render" in result, f"expected a recovered render on step 2 (real tool_calls, truncated components JSON), got: {result}"
rows = result["render"]["components"][0]["rows"]
assert len(rows) == 10, f"expected 10 recovered rows, got {len(rows)}"
names = [r["name"] for r in rows]
assert any("McDonald’s" in n for n in names)
assert any("[Action Required]" in n for n in names), "literal brackets in real content should survive intact"
print("PASSED — round 4's real tool_calls + truncated (missing closing brackets) 'components'")
print("         string recovered via the bracket-closing repair: all 10 rows parsed correctly.")
