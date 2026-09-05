import sys, json, types, unittest.mock as mock

sys.path.insert(0, "/home/claude/pilant-agent")

fake_openai = types.ModuleType("openai")
class FakeOpenAI:
    def __init__(self, *a, **k):
        pass
fake_openai.OpenAI = FakeOpenAI
sys.modules["openai"] = fake_openai

import connectors_helpdesk

FAKE_TICKETS = [
    {"id": "TK-1042", "subject": "Can't reset password", "requester": "priya@acme.io", "status": "open", "priority": "high", "category": "account", "created": "2026-08-20"},
    {"id": "TK-1041", "subject": "Invoice shows duplicate charge", "requester": "marcus@globex.com", "status": "escalated", "priority": "critical", "category": "billing", "created": "2026-08-19"},
    {"id": "TK-1039", "subject": "Export button does nothing", "requester": "lee@initech.com", "status": "in_progress", "priority": "medium", "category": "bug", "created": "2026-08-18"},
]
connectors_helpdesk.get_tickets = mock.Mock(return_value=FAKE_TICKETS)

import agent_helpdesk
agent_helpdesk.get_tickets = connectors_helpdesk.get_tickets


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


def reset_mock():
    connectors_helpdesk.get_tickets.reset_mock(return_value=True)
    connectors_helpdesk.get_tickets.return_value = FAKE_TICKETS
    agent_helpdesk.get_tickets = connectors_helpdesk.get_tickets


# --- Scenario A: real tool_calls, clean happy path (fabrication guardrail
# must NOT reject grounded content) ---------------------------------------
reset_mock()
step1 = FakeResp(
    FakeMessage(tool_calls=[FakeToolCall("tc1", "get_tickets", json.dumps({"status": "open"}))]),
    "tool_calls",
)
step2_args = json.dumps({
    "heading": "Open tickets",
    "components": [{
        "type": "list",
        "rows": [
            {"name": "Can't reset password", "note": "priya@acme.io — open, high priority"},
            {"name": "Invoice shows duplicate charge", "note": "marcus@globex.com — escalated, critical priority"},
            {"name": "Export button does nothing", "note": "lee@initech.com — in progress, medium priority"},
        ],
    }],
})
step2 = FakeResp(
    FakeMessage(tool_calls=[FakeToolCall("tc2", "render_view", step2_args)]),
    "tool_calls",
)
client_mock = mock.Mock()
client_mock.chat.completions.create.side_effect = [step1, step2]
agent_helpdesk.client = client_mock

result = agent_helpdesk.run_agent("what tickets need attention?", max_steps=8, verbose=True)
assert "render" in result, f"Scenario A: expected a render, got {result}"
rows = result["render"]["components"][0]["rows"]
assert len(rows) == 3, f"Scenario A: expected 3 rows, got {len(rows)}"
print("PASSED — Scenario A: real tool_calls, grounded content accepted cleanly.\n")


# --- Scenario B: fabrication guardrail actually catches invented content --
reset_mock()
step1b = FakeResp(
    FakeMessage(tool_calls=[FakeToolCall("tc1", "get_tickets", json.dumps({"status": "open"}))]),
    "tool_calls",
)
step2b_args = json.dumps({
    "heading": "Open tickets",
    "components": [{
        "type": "list",
        "rows": [
            {"name": "Totally made up ticket about a server fire", "note": "This never happened, made up requester"},
        ],
    }],
})
step2b = FakeResp(
    FakeMessage(tool_calls=[FakeToolCall("tc2", "render_view", step2b_args)]),
    "tool_calls",
)
step3b = FakeResp(FakeMessage(content="ok, giving up for this test"), "stop")
client_mock2 = mock.Mock()
client_mock2.chat.completions.create.side_effect = [step1b, step2b, step3b]
agent_helpdesk.client = client_mock2

result_b = agent_helpdesk.run_agent("what tickets need attention?", max_steps=8, verbose=True)
assert "render" not in result_b, f"Scenario B: fabricated content should have been rejected, got {result_b}"
# The 3rd model call (index 2, requesting step3b's response) carries the
# conversation history AFTER the render_view rejection was appended as a
# tool-role message — confirm the fabrication guardrail's own nudge text
# actually landed there.
messages_after_rejection = client_mock2.chat.completions.create.call_args_list[2].kwargs["messages"]
tool_msgs = [m for m in messages_after_rejection if m.get("role") == "tool"]
assert any("don't match any real data" in (m.get("content") or "") for m in tool_msgs), (
    f"Scenario B: expected the fabrication guardrail's nudge in a tool-role message, got {tool_msgs}"
)
print("PASSED — Scenario B: fabricated (ungrounded) render_view content correctly REJECTED.\n")


# --- Scenario C: whole tool call written as plain text (Python-call syntax),
# recovered via _parse_tool_calls_from_text, guardrail still applied to it --
reset_mock()
step1c = FakeResp(
    FakeMessage(tool_calls=[FakeToolCall("tc1", "get_tickets", json.dumps({"status": "open"}))]),
    "tool_calls",
)
text_call = (
    "render_view(heading='Open tickets', components=[{'type': 'list', 'rows': ["
    "{'name': \"Can't reset password\", 'note': 'priya@acme.io — open, high priority'}, "
    "{'name': 'Invoice shows duplicate charge', 'note': \"marcus@globex.com — escalated, critical priority\"}"
    "]}])"
)
step2c = FakeResp(FakeMessage(content=text_call), "stop")
client_mock3 = mock.Mock()
client_mock3.chat.completions.create.side_effect = [step1c, step2c]
agent_helpdesk.client = client_mock3

result_c = agent_helpdesk.run_agent("what tickets need attention?", max_steps=8, verbose=True)
assert "render" in result_c, f"Scenario C: expected recovered render, got {result_c}"
rows_c = result_c["render"]["components"][0]["rows"]
assert len(rows_c) == 2, f"Scenario C: expected 2 recovered rows, got {len(rows_c)}"
print("PASSED — Scenario C: render_view written as plain Python-call-syntax text recovered,")
print("         guardrail-checked, and accepted.\n")


# --- Scenario D: placeholder-label denylist catches "Ticket 1"/"Status" style
# invented labels even though they're single tokens (would slip the generic
# guardrail's 2-token minimum) ---------------------------------------------
reset_mock()
step1d = FakeResp(
    FakeMessage(tool_calls=[FakeToolCall("tc1", "get_tickets", json.dumps({"status": "open"}))]),
    "tool_calls",
)
step2d_args = json.dumps({
    "heading": "Open tickets",
    "components": [{
        "type": "list",
        "rows": [
            {"name": "Ticket 1", "note": "Status"},
        ],
    }],
})
step2d = FakeResp(
    FakeMessage(tool_calls=[FakeToolCall("tc2", "render_view", step2d_args)]),
    "tool_calls",
)
step3d = FakeResp(FakeMessage(content="giving up for this test"), "stop")
client_mock4 = mock.Mock()
client_mock4.chat.completions.create.side_effect = [step1d, step2d, step3d]
agent_helpdesk.client = client_mock4

result_d = agent_helpdesk.run_agent("what tickets need attention?", max_steps=8, verbose=True)
assert "render" not in result_d, f"Scenario D: placeholder labels should have been rejected, got {result_d}"
print("PASSED — Scenario D: placeholder labels ('Ticket 1', 'Status') correctly REJECTED")
print("         by _find_placeholder_labels even though they're single tokens.\n")


# --- Scenario E: lazy clarifying question (just echoes the request back)
# gets rejected with LAZY_CLARIFY_NUDGE, and the model is nudged to
# proceed rather than being allowed to pause on a non-clarifying question --
reset_mock()
step1e = FakeResp(
    FakeMessage(tool_calls=[FakeToolCall(
        "tc1", "ask_user",
        json.dumps({"question": "What tickets need attention right now?"}),
    )]),
    "tool_calls",
)
step2e = FakeResp(
    FakeMessage(tool_calls=[FakeToolCall("tc2", "get_tickets", json.dumps({"status": "escalated"}))]),
    "tool_calls",
)
step3e_args = json.dumps({
    "heading": "Escalated tickets",
    "components": [{
        "type": "list",
        "rows": [
            {"name": "Invoice shows duplicate charge", "note": "marcus@globex.com — escalated, critical priority"},
        ],
    }],
})
step3e = FakeResp(
    FakeMessage(tool_calls=[FakeToolCall("tc3", "render_view", step3e_args)]),
    "tool_calls",
)
client_mock5 = mock.Mock()
client_mock5.chat.completions.create.side_effect = [step1e, step2e, step3e]
agent_helpdesk.client = client_mock5

result_e = agent_helpdesk.run_agent("what tickets need attention right now?", max_steps=8, verbose=True)
assert "clarify" not in result_e, f"Scenario E: lazy clarifying question should have been rejected, not surfaced, got {result_e}"
assert "render" in result_e, f"Scenario E: expected the model to proceed to a real render after rejection, got {result_e}"
# Confirm the rejection message (LAZY_CLARIFY_NUDGE) actually landed as the
# tool-role response to the rejected ask_user call.
first_call_messages = client_mock5.chat.completions.create.call_args_list[1].kwargs["messages"]
ask_user_tool_msgs = [m for m in first_call_messages if m.get("role") == "tool" and m.get("tool_call_id") == "tc1"]
assert len(ask_user_tool_msgs) == 1, "Scenario E: expected exactly one tool-role reply to the rejected ask_user call"
assert ask_user_tool_msgs[0]["content"] == agent_helpdesk.LAZY_CLARIFY_NUDGE, (
    f"Scenario E: expected LAZY_CLARIFY_NUDGE, got {ask_user_tool_msgs[0]['content']!r}"
)
print("PASSED — Scenario E: lazy clarifying question (echoing the original request back)")
print("         correctly REJECTED with LAZY_CLARIFY_NUDGE instead of being surfaced.\n")

print("ALL HELPDESK PORT SCENARIOS PASSED")
