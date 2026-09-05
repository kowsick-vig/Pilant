"""
Tests for the 2026-08-25 chat-bubble markdown rendering + the custom
connector's DynamisOS-style formatted clarifying question: the user asked
"am I able to have a conversation like this [screenshot]?" — a bold intro,
a numbered 1-4 list with bold sub-headers, matching the reference. Verifies
(a) _render_chat_markdown turns that shape into real <ol>/<li>/<strong> HTML,
(b) it's genuinely safe against a model trying to inject real HTML, and (c)
a full run through agent_custom + studio.py's /studio/message route actually
produces that formatting in the rendered page, end to end.
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

# ---- Scenario 1: unit-level rendering shape --------------------------------
sample = (
    "I'd love to help you build an interface for a clothing brand! To make sure I build "
    "exactly what you need, could you clarify a few things:\n\n"
    "1. **What's the main purpose?** A product catalog or a brand showcase, or something else?\n"
    "2. **What data should it show?** I can use realistic sample data.\n"
    "3. **Any style or layout preference?** Minimalist, bold, or grid-based?\n"
    "4. **Which tools/apps should it appear to connect to?** Instagram, Google Sheets, or "
    "Shopify — simulated is fine.\n\n"
    "Once I have these, I'll build it for you!"
)
out = studio._render_chat_markdown(sample)
assert out.count("<li>") == 4, out
assert out.count("<strong>") == 4, out
assert "<ol>" in out and "</ol>" in out
assert "<p>" in out
print("PASSED — Scenario 1: a DynamisOS-shaped question renders as a real <ol> with 4 <li>,")
print("         each with a <strong> sub-header — not a flat paragraph.")

# ---- Scenario 2: adversarial safety — a model trying to inject real HTML --
evil = "**bold**\n\n1. item <img src=x onerror=alert(1)> one\n2. <script>alert(2)</script> two"
out2 = studio._render_chat_markdown(evil)
# The dangerous part is a LIVE '<img ...>' or '<script>...' tag actually
# reaching the page — the raw substring "onerror=" surviving as inert,
# already-escaped text (inside "&lt;img ... onerror=... &gt;") is harmless
# and expected; what matters is there is no unescaped '<img' or '<script'.
assert "<script>" not in out2 and "<img " not in out2
assert "&lt;script&gt;" in out2 and "&lt;img" in out2
print("PASSED — Scenario 2: adversarial HTML/script injection inside the text is neutralized —")
print("         only ever appears HTML-escaped, never as live markup.")

# ---- Scenario 3: end-to-end through agent_custom + studio.py --------------
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
    FakeMessage(tool_calls=[FakeToolCall("tc1", "ask_user", json.dumps({"question": sample}))]),
    "tool_calls",
)
client_mock = mock.Mock()
client_mock.chat.completions.create.side_effect = [ask_step]

client.post("/studio/new", follow_redirects=True)
with mock.patch("agent_custom.client", client_mock):
    client.post("/studio/message", data={"text": "clothing brand"}, follow_redirects=True)

r = client.get("/studio")
page = r.get_data(as_text=True)
assert "<ol>" in page and page.count("<li>") >= 4
assert "<strong>What&#x27;s the main purpose?</strong>" in page or "What" in page and "<strong>" in page
assert "<script>" not in page
print("PASSED — Scenario 3: end-to-end, typing 'clothing brand' produces a rendered page with a")
print("         real numbered, bolded clarifying question — matching the DynamisOS reference shape.")

print()
print("ALL CHAT MARKDOWN FORMATTING SCENARIOS PASSED")
