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
    {"from": "CredAbility <marketing@mail.credability.co.uk>", "subject": "Kowsick, a new credit card added in the last 30 days", "date": "Aug 25, 2026", "snippet": "Check if you're eligible - it won't harm your score", "unread": True, "id": "m1"},
    {"from": "Upwork <upwork@email.upwork.com>", "subject": "Refer a new Upwork client - now receive $35 gift card.", "date": "Aug 25, 2026", "snippet": "Limited time Upwork Referral Program gift card jumps from $25 to $35.", "unread": True, "id": "m2"},
    {"from": "PureGym Offers <puregym@offers.emails-puregym.com>", "subject": "Never Miss a Class Again!", "date": "Aug 25, 2026", "snippet": "Book your favourite classes in advance with our bolt-on!", "unread": True, "id": "m3"},
    {"from": "TryHackMe <donotreply@tryhackme.com>", "subject": "Let’s finish what you started \U0001F4BB", "date": "Aug 25, 2026", "snippet": "Your first room is waiting, so is your 25% reward.", "unread": True, "id": "m4"},
    {"from": "Uber Eats <ubereats@uber.com>", "subject": "Get a free item at McDonald’s when you spend £20", "date": "Aug 25, 2026", "snippet": "Take a delicious detour with the new McDonald's Side Missions Menu", "unread": True, "id": "m5"},
    {"from": "Groq <developer@groq.co>", "subject": "Compound is being decommissioned", "date": "Aug 25, 2026", "snippet": "Important GroqCloud notice. Switch your models now.", "unread": True, "id": "m6"},
    {"from": "\"McDonald's\" <no-reply@hello.mcdonalds.co.uk>", "subject": "Daily deals have DROPPED", "date": "Aug 25, 2026", "snippet": "Start your side mission", "unread": True, "id": "m7"},
    {"from": "Squarespace <no-reply@squarespace.com>", "subject": "[Action Required] Verify your Squarespace domain contact", "date": "Aug 25, 2026", "snippet": "Squarespace domains Welcome to Squarespace Domains You recently purchased a Squarespace domain. Please verify your email address below. ICANN requires email verification within 15 days of every domain", "unread": True, "id": "m8"},
    {"from": "Uber Eats <uber@uber.com>", "subject": "Let's switch it up", "date": "Aug 25, 2026", "snippet": "Give these restaurants a try", "unread": True, "id": "m9"},
    {"from": "PSBE Cyber News Group <marcus-cybernewsgroup.co.uk@shared1.ccsend.com>", "subject": "Cyber Essentials makes you compliant. Not completely threat-ready.", "date": "Aug 25, 2026", "snippet": "Hi Kowsick, Here's the uncomfortable truth: most organizations that get breached already had MFA enforced, passwords under control, and a fresh security certificate on the wall. That's because", "unread": True, "id": "m10"},
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

# The EXACT real "components" string value from the user's round-6 terminal log (post outer
# JSON-decode) — real ground truth: blanket single-quoted Python-dict-repr text (round 3's bug)
# that is ALSO genuinely truncated at the end, missing "]}]" (round 4's bug), AND contains a
# real sender display name with literal embedded quote marks written as \"McDonald's\" (the new
# round 6 wrinkle that desyncs _close_truncated_json when run on the raw single-quoted text).
truncated_and_quoted_components = "[{'type': 'list', 'rows': [{'name': 'CredAbility <marketing@mail.credability.co.uk>: Kowsick, a new credit card  added in the last 30 days', 'note': 'Check if you're eligible - it won't harm your score', 'badge': {'text': 'Unread', 'tone': 'warning'}}, {'name': 'Upwork <upwork@email.upwork.com>: Refer a new Upwork client - now receive $35 gift card.', 'note': 'Limited time Upwork Referral Program gift card jumps from $25 to $35.', 'badge': {'text': 'Unread', 'tone': 'warning'}}, {'name': 'PureGym Offers <puregym@offers.emails-puregym.com>: Never Miss a Class Again!', 'note': 'Book your favourite classes in advance with our bolt-on!', 'badge': {'text': 'Unread', 'tone': 'warning'}}, {'name': 'TryHackMe <donotreply@tryhackme.com>: Let’s finish what you started \U0001F4BB', 'note': 'Your first room is waiting, so is your 25% reward.', 'badge': {'text': 'Unread', 'tone': 'warning'}}, {'name': 'Uber Eats <ubereats@uber.com>: Get a free item at McDonald’s when you spend £20', 'note': 'Take a delicious detour with the new McDonald's Side Missions Menu', 'badge': {'text': 'Unread', 'tone': 'warning'}}, {'name': 'Groq <developer@groq.co>: Compound is being decommissioned', 'note': 'Important GroqCloud notice. Switch your models now.', 'badge': {'text': 'Unread', 'tone': 'warning'}}, {'name': '\\\"McDonald's\\\" <no-reply@hello.mcdonalds.co.uk>: Daily deals have DROPPED', 'note': 'Start your side mission', 'badge': {'text': 'Unread', 'tone': 'warning'}}, {'name': 'Squarespace <no-reply@squarespace.com>: [Action Required] Verify your Squarespace domain contact', 'note': 'Squarespace domains Welcome to Squarespace Domains You recently purchased a Squarespace domain. Please verify your email address below. ICANN requires email verification within 15 days of every domain', 'badge': {'text': 'Unread', 'tone': 'warning'}}, {'name': 'Uber Eats <uber@uber.com>: Let's switch it up', 'note': 'Give these restaurants a try', 'badge': {'text': 'Unread', 'tone': 'warning'}}, {'name': 'PSBE Cyber News Group <marcus-cybernewsgroup.co.uk@shared1.ccsend.com>: Cyber Essentials makes you compliant. Not completely threat-ready.', 'note': 'Hi Kowsick, Here's the uncomfortable truth: most organizations that get breached already had MFA enforced, passwords under control, and a fresh security certificate on the wall. That's because', 'badge': {'text': 'Unread', 'tone': 'warning'}}]"

step2 = FakeResp(
    FakeMessage(tool_calls=[FakeToolCall("tc2", "render_view", json.dumps({
        "heading": "10 unread emails",
        "meta": "as a task list",
        "components": truncated_and_quoted_components,
    }))]),
    "tool_calls",
)

client_mock = mock.Mock()
client_mock.chat.completions.create.side_effect = [step1, step2]
agent_gmail.client = client_mock

result = agent_gmail.run_agent("show my unread emails as a task list", max_steps=8, verbose=True)
print()
print("RESULT KEYS:", list(result.keys()))
assert "render" in result, f"expected a recovered render, got: {result}"
rows = result["render"]["components"][0]["rows"]
assert len(rows) == 10, f"expected 10 recovered rows, got {len(rows)}"
names = [r["name"] for r in rows]
assert any("McDonald's" in n and n.strip().startswith('"McDonald') for n in names), \
    f"expected the quoted-name row to survive with its literal quote marks intact, got: {names}"
assert any("you're eligible" in n or "won't harm" in n for n in names) or \
       any("won't harm" in r.get("note", "") for r in rows), "apostrophes in row 1 should survive"
assert any("Here's the uncomfortable truth" in r.get("note", "") for r in rows), "apostrophes in row 10's note should survive"
print("PASSED — round 6's real tool_calls + BOTH truncated-JSON AND blanket-quoted 'components'")
print("         string (with a literal embedded quote-marked sender name) recovered via the new")
print("         repair-then-close ordering: all 10 rows parsed, every real apostrophe and the")
print("         literal quote marks around \"McDonald's\" preserved.")
