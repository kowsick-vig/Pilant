"""
End-to-end reproduction of the live "Finance Summary" bug through run_agent itself
(not just the guardrail function in isolation): the model calls get_gmail_messages
for real, then tries to render_view a fabricated financial stat_grid — this must be
REJECTED and force a second, corrected attempt, exactly like every other fabrication
guardrail already does for list rows.

REWRITTEN 2026-08-26 for the NVIDIA -> Claude migration: mocks claude_engine.client
(every connector's shared model client — see claude_engine.py's docstring) with
Claude Messages-API-shape scripted responses instead of agent_gmail's old
OpenAI-shape chat.completions.create. No TOOL_FUNCTIONS dict to poke anymore —
agent_gmail.py's dispatch calls get_gmail_messages directly, via its module-level
name, which is enough to mock.
"""
import sys, json, unittest.mock as mock

sys.path.insert(0, "/home/claude/pilant-agent")

import connectors_gmail

FAKE_MESSAGES = [
    {"from": "billing@acme.com", "subject": "Your invoice is ready", "date": "Aug 20, 2026",
     "snippet": "Nothing due, your subscription renews automatically.", "unread": True, "id": "m1"},
    {"from": "hr@acme.com", "subject": "Payroll update", "date": "Aug 22, 2026",
     "snippet": "New payroll schedule starting next month.", "unread": True, "id": "m2"},
]
connectors_gmail.get_gmail_messages = mock.Mock(return_value=FAKE_MESSAGES)

import agent_gmail
import claude_engine as ce
agent_gmail.get_gmail_messages = connectors_gmail.get_gmail_messages


class FakeToolUseBlock:
    type = "tool_use"

    def __init__(self, id_, name, input_):
        self.id = id_
        self.name = name
        self.input = input_


class FakeResp:
    def __init__(self, content, stop_reason):
        self.content = content
        self.stop_reason = stop_reason


step1 = FakeResp(
    [FakeToolUseBlock("tc1", "get_gmail_messages", {"unread_only": False, "query": "finance", "limit": 10})],
    "tool_use",
)

# Step 2: the model fabricates a "Finance Summary" stat_grid — exactly the live bug.
step2 = FakeResp(
    [FakeToolUseBlock("tc2", "render_view", {
        "heading": "Finance Summary",
        "components": [{
            "type": "stat_grid",
            "title": "Finance Summary",
            "subtitle": "This week",
            "stats": [
                {"label": "Total", "value": "$10,000"},
                {"label": "Due Today", "value": "$500"},
            ],
        }],
    })],
    "tool_use",
)

# Step 3: corrected — an honest summary grounded in the real (financially-empty) data.
step3 = FakeResp(
    [FakeToolUseBlock("tc3", "render_view", {
        "heading": "2 emails from finance this week",
        "meta": "No amounts due — just a payroll update and an invoice notice.",
        "components": [{
            "type": "list",
            "rows": [
                {"name": "billing@acme.com: Your invoice is ready", "note": "Nothing due, your subscription renews automatically."},
                {"name": "hr@acme.com: Payroll update", "note": "New payroll schedule starting next month."},
            ],
        }],
    })],
    "tool_use",
)

client_mock = mock.Mock()
client_mock.messages.create.side_effect = [step1, step2, step3]

with mock.patch.object(ce, "client", client_mock):
    result = agent_gmail.run_agent("summarize what came in from finance this week", max_steps=8, verbose=True)

print()
print("RESULT KEYS:", list(result.keys()))
assert "render" in result, f"expected an eventual valid render after the rejection, got: {result}"
assert result["render"]["heading"] == "2 emails from finance this week"
assert client_mock.messages.create.call_count == 3, (
    f"expected exactly 3 model calls (fetch, rejected fabricated stat_grid, corrected render), "
    f"got {client_mock.messages.create.call_count} — the fabricated Finance Summary "
    f"should have been rejected, not accepted"
)

# Confirm the rejection nudge actually landed in the conversation handed back on the
# THIRD call — proof the correction was forced by a real guardrail rejection, not luck.
third_call_messages = client_mock.messages.create.call_args_list[2].kwargs["messages"]
tool_result_texts = []
for m in third_call_messages:
    if m.get("role") == "user" and isinstance(m.get("content"), list):
        for block in m["content"]:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                tool_result_texts.append(block.get("content") or "")
combined = " ".join(tool_result_texts)
assert "don't match" in combined or "was rejected" in combined, (
    f"expected the fabrication rejection nudge in the conversation before the corrected "
    f"render, got tool_result texts: {tool_result_texts!r}"
)

print("PASSED — the fabricated 'Finance Summary' ($10,000/$500) stat_grid was REJECTED, forcing")
print("         a real third model turn that produced an honest, grounded render.")
print()
print("ALL GMAIL FINANCE-SUMMARY E2E SCENARIOS PASSED")
