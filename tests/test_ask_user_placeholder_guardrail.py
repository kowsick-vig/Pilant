"""
Regression test for the bug the user reported live: agent_custom's ask_user
clarifying question sometimes comes back with the tool description's own
EXAMPLE template copied almost verbatim, angle brackets and all —
e.g. "What's the main purpose? <what you described>, or something else?"
and "Which tools/apps should it appear to connect to? <2-3 example tools
that fit the domain you described> — simulated is fine." — instead of real,
filled-in content. Nothing previously caught this: _is_lazy_clarifying_
question only catches a question that echoes the person's REQUEST, not one
that echoes ask_user's own TEMPLATE.

Fix: _find_unfilled_placeholder() detects literal '<...>' spans in the
question and the tool-calling loop rejects/nudges exactly like every other
connector's placeholder guardrail on render_view content.
"""
import sys, os, types, json
sys.path.insert(0, "/home/claude/pilant-agent")

fake_openai = types.ModuleType("openai")
class FakeOpenAI:
    def __init__(self, *a, **k):
        pass
fake_openai.OpenAI = FakeOpenAI
sys.modules["openai"] = fake_openai

import agent_custom

# ---- Scenario 1: unit-level detection --------------------------------------
BROKEN_QUESTION = (
    "I'd love to help you build an interface! To make sure I build exactly what you need, "
    "could you clarify a few things:\n\n"
    "1. What's the main purpose? <what you described>, or something else?\n"
    "2. What data should it show? I can use realistic sample data, or you can describe what you have in mind.\n"
    "3. Any style or layout preference? Minimalist, bold, grid-based, or something else?\n"
    "4. Which tools/apps should it appear to connect to? <2-3 example tools that fit the domain you described> "
    "— simulated is fine.\n\n"
    "Once I have these, I'll build it for you!"
)
found = agent_custom._find_unfilled_placeholder(BROKEN_QUESTION)
assert found == ["<what you described>", "<2-3 example tools that fit the domain you described>"], found
print(f"PASSED — Scenario 1: _find_unfilled_placeholder catches the exact reported text: {found}")

CLEAN_QUESTION = (
    "I'd love to help you build an interface for a fitness app! To make sure I build exactly "
    "what you need, could you clarify a few things:\n\n"
    "1. **What's the main purpose?** A workout log or a nutrition tracker, or something else?\n"
    "2. **What data should it show?** I can use realistic sample data.\n"
    "3. **Any style or layout preference?** Minimalist, bold, or grid-based?\n"
    "4. **Which tools/apps should it appear to connect to?** Strava, Apple Health, or MyFitnessPal — simulated is fine.\n\n"
    "Once I have these, I'll build it for you!"
)
assert agent_custom._find_unfilled_placeholder(CLEAN_QUESTION) == []
print("PASSED — Scenario 1b: a genuinely filled-in question has no false positives.")

# ---- Scenario 2: end-to-end through run_agent — the model returns the
# broken, template-echoing question first; the guardrail must reject it and
# force a retry, which then succeeds with a real, filled-in question. -------
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


broken_step = FakeResp(
    FakeMessage(tool_calls=[FakeToolCall("tc1", "ask_user", json.dumps({"question": BROKEN_QUESTION}))]),
    "tool_calls",
)
clean_step = FakeResp(
    FakeMessage(tool_calls=[FakeToolCall("tc2", "ask_user", json.dumps({"question": CLEAN_QUESTION}))]),
    "tool_calls",
)

import unittest.mock as mock
client_mock = mock.Mock()
client_mock.chat.completions.create.side_effect = [broken_step, clean_step]

with mock.patch("agent_custom.client", client_mock):
    result = agent_custom.run_agent("fitness app", verbose=True)

assert "clarify" in result, result
assert result["clarify"] == CLEAN_QUESTION, result["clarify"]
assert "<" not in result["clarify"], "the returned clarifying question must never contain a raw placeholder"
assert client_mock.chat.completions.create.call_count == 2, "the broken question must have triggered a real retry, not been accepted"
print("PASSED — Scenario 2: the placeholder-laden question was rejected and retried;")
print("         only the real, filled-in question was ever returned to the user.")

print()
print("ALL ASK_USER PLACEHOLDER GUARDRAIL SCENARIOS PASSED")
