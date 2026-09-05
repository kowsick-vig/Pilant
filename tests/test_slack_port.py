"""
REWRITTEN 2026-08-26 for the NVIDIA -> Claude migration. Mocks claude_engine.client
(the shared model client every connector now uses) with Claude Messages-API-shape
scripted responses instead of agent_slack's old OpenAI-shape
chat.completions.create.

Scenario C is genuinely different from the original, not just re-shaped: the
original tested _parse_tool_calls_from_text() recovering a whole tool call the
model wrote out as plain text instead of a real function call — a llama-3.1-8b
failure mode that doesn't exist for Claude (tool_use blocks arrive
already-structured; there's no "wrote it as text instead" to recover from, and
that whole recovery module is deleted). Replaced with a test of the mirror-image
capability Claude actually has that the old model didn't: multiple tool_use
blocks in ONE turn. The old NVIDIA model's chat template choked on more than one
tool_calls entry per turn (see the deleted "keep only the first tool_call"
workaround in agent_slack.py's pre-migration version) — Claude has no such
limitation, and claude_engine.py's dispatch loop handles a multi-block turn
correctly (each block dispatched in order, off shared closure state), which
this scenario now verifies directly.
"""
import sys, json, unittest.mock as mock

sys.path.insert(0, "/home/claude/pilant-agent")

import connectors_slack

FAKE_MESSAGES = [
    {"user": "Alice Smith", "text": "the deploy is stuck on staging again, anyone free to look?", "ts": "1.1", "reply_count": 2},
    {"user": "Bob Jones", "text": "on it — looks like it's the same DB migration timeout as last week", "ts": "1.2", "reply_count": 0},
    {"user": "Alice Smith", "text": "thanks, that'd be great — it's blocking the release", "ts": "1.3", "reply_count": 0},
]
connectors_slack.get_slack_messages = mock.Mock(return_value=FAKE_MESSAGES)

import agent_slack
import claude_engine as ce
agent_slack.get_slack_messages = connectors_slack.get_slack_messages


class FakeToolUseBlock:
    type = "tool_use"

    def __init__(self, id_, name, input_):
        self.id = id_
        self.name = name
        self.input = input_


class FakeTextBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class FakeResp:
    def __init__(self, content, stop_reason):
        self.content = content
        self.stop_reason = stop_reason


def reset_mock():
    connectors_slack.get_slack_messages.reset_mock(return_value=True)
    connectors_slack.get_slack_messages.return_value = FAKE_MESSAGES
    agent_slack.get_slack_messages = connectors_slack.get_slack_messages


# --- Scenario A: real tool_calls, clean happy path (fabrication guardrail
# must NOT reject grounded content) ---------------------------------------
reset_mock()
step1 = FakeResp([FakeToolUseBlock("tc1", "get_slack_messages", {"limit": 20})], "tool_use")
step2 = FakeResp([FakeToolUseBlock("tc2", "render_view", {
    "heading": "Recent channel activity",
    "components": [{
        "type": "list",
        "rows": [
            {"name": "Alice Smith", "note": "the deploy is stuck on staging again, anyone free to look?"},
            {"name": "Bob Jones", "note": "on it — looks like it's the same DB migration timeout as last week"},
            {"name": "Alice Smith", "note": "thanks, that'd be great — it's blocking the release"},
        ],
    }],
})], "tool_use")
client_mock = mock.Mock()
client_mock.messages.create.side_effect = [step1, step2]

with mock.patch.object(ce, "client", client_mock):
    result = agent_slack.run_agent("what's happening in the channel?", max_steps=8, verbose=True)
assert "render" in result, f"Scenario A: expected a render, got {result}"
rows = result["render"]["components"][0]["rows"]
assert len(rows) == 3, f"Scenario A: expected 3 rows, got {len(rows)}"
print("PASSED — Scenario A: real tool_calls, grounded content accepted cleanly.\n")


# --- Scenario B: fabrication guardrail actually catches invented content,
# and the model's eventual plain-text "giving up" reply is correctly treated
# as a genuine final answer (force_tool_choice=False), NOT a render --------
reset_mock()
step1b = FakeResp([FakeToolUseBlock("tc1", "get_slack_messages", {"limit": 20})], "tool_use")
step2b = FakeResp([FakeToolUseBlock("tc2", "render_view", {
    "heading": "Recent channel activity",
    "components": [{
        "type": "list",
        "rows": [
            {"name": "Totally Made Up Person", "note": "This is a completely fabricated message that never happened"},
        ],
    }],
})], "tool_use")
step3b = FakeResp([FakeTextBlock("ok, giving up for this test")], "end_turn")
client_mock2 = mock.Mock()
client_mock2.messages.create.side_effect = [step1b, step2b, step3b]

with mock.patch.object(ce, "client", client_mock2):
    result_b = agent_slack.run_agent("what's happening in the channel?", max_steps=8, verbose=True)
assert "render" not in result_b, f"Scenario B: fabricated content should have been rejected, got {result_b}"
assert result_b.get("text") == "ok, giving up for this test", (
    f"Scenario B: expected the model's plain-text follow-up to be returned as a genuine "
    f"conversational reply (force_tool_choice=False), got {result_b}"
)
# Confirm the rejection nudge actually landed in the conversation on the THIRD call —
# proof the guardrail fired, not that the model just happened to give up on its own.
third_call_messages = client_mock2.messages.create.call_args_list[2].kwargs["messages"]
tool_result_texts = [
    block.get("content") or ""
    for m in third_call_messages if m.get("role") == "user" and isinstance(m.get("content"), list)
    for block in m["content"] if isinstance(block, dict) and block.get("type") == "tool_result"
]
combined = " ".join(tool_result_texts)
assert "don't match" in combined or "was rejected" in combined, (
    f"Scenario B: expected the fabrication rejection nudge in the conversation, got: {tool_result_texts!r}"
)
print("PASSED — Scenario B: fabricated (ungrounded) render_view content correctly REJECTED,")
print("         and the model's subsequent plain-text reply was handled as a genuine answer.\n")


# --- Scenario C: multiple tool_use blocks in ONE turn (get_slack_messages +
# render_view together) are dispatched correctly, in order, off shared
# closure state — a capability the old NVIDIA model's chat template couldn't
# handle at all (see this file's module docstring) ------------------------
reset_mock()
step1c = FakeResp(
    [
        FakeToolUseBlock("tc1", "get_slack_messages", {"limit": 20}),
        FakeToolUseBlock("tc2", "render_view", {
            "heading": "Recent channel activity",
            "components": [{
                "type": "list",
                "rows": [
                    {"name": "Alice Smith", "note": "the deploy is stuck on staging again, anyone free to look?"},
                    {"name": "Bob Jones", "note": "on it — looks like it's the same DB migration timeout as last week"},
                ],
            }],
        }),
    ],
    "tool_use",
)
client_mock3 = mock.Mock()
client_mock3.messages.create.side_effect = [step1c]

with mock.patch.object(ce, "client", client_mock3):
    result_c = agent_slack.run_agent("what's happening in the channel?", max_steps=8, verbose=True)
assert "render" in result_c, f"Scenario C: expected a render from a single multi-block turn, got {result_c}"
rows_c = result_c["render"]["components"][0]["rows"]
assert len(rows_c) == 2, f"Scenario C: expected 2 rows, got {len(rows_c)}"
assert client_mock3.messages.create.call_count == 1, (
    f"Scenario C: expected exactly ONE model call — both the fetch and the render were "
    f"dispatched from a single turn's two tool_use blocks — got {client_mock3.messages.create.call_count}"
)
print("PASSED — Scenario C: a single model turn with TWO tool_use blocks (fetch + render)")
print("         was dispatched correctly in order, off shared closure state — no NVIDIA-era")
print("         'keep only the first tool_call' workaround needed for Claude.\n")


# --- Scenario D: placeholder-label denylist catches "Message 1" style
# invented labels even though they're single tokens (would slip the generic
# guardrail's 2-token minimum) ---------------------------------------------
reset_mock()
step1d = FakeResp([FakeToolUseBlock("tc1", "get_slack_messages", {"limit": 20})], "tool_use")
step2d = FakeResp([FakeToolUseBlock("tc2", "render_view", {
    "heading": "Recent channel activity",
    "components": [{
        "type": "list",
        "rows": [
            {"name": "Message 1", "note": "Sender"},
        ],
    }],
})], "tool_use")
step3d = FakeResp([FakeTextBlock("giving up for this test")], "end_turn")
client_mock4 = mock.Mock()
client_mock4.messages.create.side_effect = [step1d, step2d, step3d]

with mock.patch.object(ce, "client", client_mock4):
    result_d = agent_slack.run_agent("what's happening in the channel?", max_steps=8, verbose=True)
assert "render" not in result_d, f"Scenario D: placeholder labels should have been rejected, got {result_d}"
print("PASSED — Scenario D: placeholder labels ('Message 1', 'Sender') correctly REJECTED")
print("         by _find_placeholder_labels even though they're single tokens.\n")

print("ALL SLACK PORT SCENARIOS PASSED")
