"""
Tests for agent_custom.py — the 2026-08-25 "build your own interface" mode
added after the user showed a DynamisOS reference screenshot: describing an
app/brand should get a short clarifying round (for vague requests) then a
full custom page built from realistic INVENTED sample data (no real
backend, no fabrication-grounding check — there's nothing to ground against).
"""
import sys, json, types, unittest.mock as mock

sys.path.insert(0, "/home/claude/pilant-agent")

fake_openai = types.ModuleType("openai")
class FakeOpenAI:
    def __init__(self, *a, **k):
        pass
fake_openai.OpenAI = FakeOpenAI
sys.modules["openai"] = fake_openai

import agent_custom


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


# ---- Scenario 1: a vague request ("clothing brand") gets clarified first --
step1 = FakeResp(
    FakeMessage(tool_calls=[FakeToolCall("tc1", "ask_user", json.dumps({
        "question": (
            "What's the main purpose (showcase, catalog, storefront)? What data should it "
            "show? Any style preference? Which tools should it appear to connect to?"
        ),
    }))]),
    "tool_calls",
)
client_mock = mock.Mock()
client_mock.chat.completions.create.side_effect = [step1]
agent_custom.client = client_mock

result = agent_custom.run_agent("clothing brand", max_steps=8, verbose=True)
assert "clarify" in result, f"expected a clarifying question for a vague 2-word request, got: {result}"
print(f"PASSED — Scenario 1: 'clothing brand' (vague) got clarified first: {result['clarify'][:60]}...")

# ---- Scenario 2: after answering, render_view with invented sample data succeeds
step2 = FakeResp(
    FakeMessage(tool_calls=[FakeToolCall("tc2", "render_view", json.dumps({
        "heading": "Aurora & Co. — Product Catalog",
        "meta": "Minimalist storefront, synced with a simulated Shopify feed",
        "components": [{
            "type": "list",
            "rows": [
                {"name": "Linen Wrap Dress", "note": "$128 — Sand, sizes XS-XL", "badge": {"text": "Bestseller", "tone": "good"}},
                {"name": "Merino Crew Sweater", "note": "$96 — Charcoal, sizes S-XL"},
            ],
        }],
    }))]),
    "tool_calls",
)
client_mock2 = mock.Mock()
client_mock2.chat.completions.create.side_effect = [step2]
agent_custom.client = client_mock2

resumed_messages = result["messages"] + [{"role": "user", "content": (
    "It's a storefront for a minimalist clothing brand called Aurora & Co. Show the product "
    "catalog. Clean, minimalist style. Pretend it's synced with Shopify."
)}]
result2 = agent_custom.run_agent(messages=resumed_messages, fetched_data=result.get("fetched_data", False))
assert "render" in result2, f"expected a successful render after answering, got: {result2}"
assert result2["render"]["heading"] == "Aurora & Co. — Product Catalog"
print("PASSED — Scenario 2: after answering, a realistic invented product catalog rendered successfully.")

# ---- Scenario 3: a DETAILED first request skips clarifying entirely ------
step3 = FakeResp(
    FakeMessage(tool_calls=[FakeToolCall("tc3", "render_view", json.dumps({
        "heading": "FitTrack — Weekly Workout Log",
        "components": [{
            "type": "stat_grid",
            "stats": [
                {"label": "Workouts this week", "value": "4"},
                {"label": "Streak", "value": "12 days"},
            ],
        }, {
            "type": "list",
            "rows": [
                {"name": "Upper Body Strength", "note": "Mon — 45 min, 6 exercises"},
                {"name": "5K Run", "note": "Wed — 28:14, 5.2 km"},
            ],
        }],
    }))]),
    "tool_calls",
)
client_mock3 = mock.Mock()
client_mock3.chat.completions.create.side_effect = [step3]
agent_custom.client = client_mock3

detailed_request = (
    "Build a fitness tracking dashboard showing this week's workout log and a streak counter, "
    "clean and motivating style, pretend it pulls from a Strava-like feed."
)
result3 = agent_custom.run_agent(detailed_request, max_steps=8, verbose=True)
assert "render" in result3, f"expected a direct render for a detailed request, got: {result3}"
assert client_mock3.chat.completions.create.call_count == 1, "a detailed request should render on the FIRST model turn, no clarifying round needed"
print("PASSED — Scenario 3: a sufficiently detailed first request rendered directly, no clarifying round.")

# ---- Scenario 4: code-enforced gate — render_view on a vague request WITHOUT
# asking first is rejected, forcing a real clarifying round -----------------
step4_bad = FakeResp(
    FakeMessage(tool_calls=[FakeToolCall("tc4", "render_view", json.dumps({
        "heading": "Clothing Brand",
        "components": [{"type": "list", "rows": [{"name": "Item 1", "note": "..."}]}],
    }))]),
    "tool_calls",
)
step4_good = FakeResp(
    FakeMessage(tool_calls=[FakeToolCall("tc5", "ask_user", json.dumps({
        "question": "What's the purpose, what should it show, any style preference, and which tools should it connect to?",
    }))]),
    "tool_calls",
)
client_mock4 = mock.Mock()
client_mock4.chat.completions.create.side_effect = [step4_bad, step4_good]
agent_custom.client = client_mock4

result4 = agent_custom.run_agent("clothing brand", max_steps=8, verbose=True)
assert client_mock4.chat.completions.create.call_count == 2, (
    "the model's premature render_view attempt should have been REJECTED (code-enforced gate), "
    "forcing a second turn"
)
assert "clarify" in result4, f"expected the second turn's ask_user to succeed after the first was rejected, got: {result4}"
print("PASSED — Scenario 4: a premature render_view on a vague, unclarified request was correctly")
print("         REJECTED by the code-enforced gate, forcing a real clarifying round.")

# ---- Scenario 5: placeholder/template content is rejected -----------------
step5_bad = FakeResp(
    FakeMessage(tool_calls=[FakeToolCall("tc6", "render_view", json.dumps({
        "heading": "Test build with placeholder content that is long enough to skip clarifying",
        "components": [{"type": "list", "rows": [
            {"name": "Item 1", "note": "Lorem ipsum"},
        ]}],
    }))]),
    "tool_calls",
)
step5_good = FakeResp(
    FakeMessage(tool_calls=[FakeToolCall("tc7", "render_view", json.dumps({
        "heading": "Test build with placeholder content that is long enough to skip clarifying",
        "components": [{"type": "list", "rows": [
            {"name": "Wireless Noise-Cancelling Headphones", "note": "$249 — In stock"},
        ]}],
    }))]),
    "tool_calls",
)
client_mock5 = mock.Mock()
client_mock5.chat.completions.create.side_effect = [step5_bad, step5_good]
agent_custom.client = client_mock5

detailed_request_5 = "Build a detailed product page for an electronics store with real-sounding items, prices, and stock status"
result5 = agent_custom.run_agent(detailed_request_5, max_steps=8, verbose=True)
assert client_mock5.chat.completions.create.call_count == 2, "the placeholder-content render should have been REJECTED, forcing a retry"
assert "render" in result5 and result5["render"]["components"][0]["rows"][0]["name"] == "Wireless Noise-Cancelling Headphones"
print("PASSED — Scenario 5: placeholder content ('Item 1', 'Lorem ipsum') was REJECTED; the")
print("         corrected, realistic render succeeded on retry.")

print()
print("ALL AGENT_CUSTOM SCENARIOS PASSED")
