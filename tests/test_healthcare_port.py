import sys, json, types, unittest.mock as mock

sys.path.insert(0, "/home/claude/pilant-agent")

fake_openai = types.ModuleType("openai")
class FakeOpenAI:
    def __init__(self, *a, **k):
        pass
fake_openai.OpenAI = FakeOpenAI
sys.modules["openai"] = fake_openai

import connectors_healthcare

FAKE_APPOINTMENTS = [
    {"id": "a1", "patient": "Camila Lopez", "provider": "Dr. Sarah Bennett", "start": "2026-08-25T09:00:00Z", "end": "2026-08-25T09:30:00Z", "status": "checked_in", "reason": "Annual physical"},
    {"id": "a2", "patient": "Derrick Lin", "provider": "Dr. Marcus Reyes", "start": "2026-08-25T10:00:00Z", "end": "2026-08-25T10:30:00Z", "status": "booked", "reason": "Follow-up"},
    {"id": "a3", "patient": "Camila Lopez", "provider": "Dr. Sarah Bennett", "start": "2026-08-25T11:00:00Z", "end": "2026-08-25T11:30:00Z", "status": "no_show", "reason": "Lab review"},
]
connectors_healthcare.get_appointments = mock.Mock(return_value=FAKE_APPOINTMENTS)

import agent_healthcare
agent_healthcare.get_appointments = connectors_healthcare.get_appointments
agent_healthcare.TOOL_FUNCTIONS["get_appointments"] = connectors_healthcare.get_appointments


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
    connectors_healthcare.get_appointments.reset_mock(return_value=True)
    connectors_healthcare.get_appointments.return_value = FAKE_APPOINTMENTS
    agent_healthcare.get_appointments = connectors_healthcare.get_appointments
    agent_healthcare.TOOL_FUNCTIONS["get_appointments"] = connectors_healthcare.get_appointments


# --- Scenario A: real tool_calls, clean happy path (fabrication guardrail
# must NOT reject grounded content) ---------------------------------------
reset_mock()
step1 = FakeResp(
    FakeMessage(tool_calls=[FakeToolCall("tc1", "get_appointments", json.dumps({}))]),
    "tool_calls",
)
step2_args = json.dumps({
    "heading": "Today's appointments",
    "components": [{
        "type": "list",
        "rows": [
            {"name": "Camila Lopez", "note": "Dr. Sarah Bennett — checked in for Annual physical"},
            {"name": "Derrick Lin", "note": "Dr. Marcus Reyes — booked for Follow-up"},
            {"name": "Camila Lopez", "note": "Dr. Sarah Bennett — no-show for Lab review"},
        ],
    }],
})
step2 = FakeResp(
    FakeMessage(tool_calls=[FakeToolCall("tc2", "render_view", step2_args)]),
    "tool_calls",
)
client_mock = mock.Mock()
client_mock.chat.completions.create.side_effect = [step1, step2]
agent_healthcare.client = client_mock

result = agent_healthcare.run_agent("what appointments are there today?", max_steps=8, verbose=True)
assert "render" in result, f"Scenario A: expected a render, got {result}"
rows = result["render"]["components"][0]["rows"]
assert len(rows) == 3, f"Scenario A: expected 3 rows, got {len(rows)}"
print("PASSED — Scenario A: real tool_calls, grounded content accepted cleanly.\n")


# --- Scenario B: fabrication guardrail actually catches invented content --
reset_mock()
step1b = FakeResp(
    FakeMessage(tool_calls=[FakeToolCall("tc1", "get_appointments", json.dumps({}))]),
    "tool_calls",
)
step2b_args = json.dumps({
    "heading": "Today's appointments",
    "components": [{
        "type": "list",
        "rows": [
            {"name": "Totally Made Up Patient", "note": "This appointment never actually happened"},
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
agent_healthcare.client = client_mock2

result_b = agent_healthcare.run_agent("what appointments are there today?", max_steps=8, verbose=True)
assert "render" not in result_b, f"Scenario B: fabricated content should have been rejected, got {result_b}"
tool_msgs = [m for m in client_mock2.chat.completions.create.call_args_list[-1].kwargs["messages"] if m.get("role") == "tool"]
assert any("rejected" in (m.get("content") or "").lower() for m in tool_msgs)
print("PASSED — Scenario B: fabricated (ungrounded) render_view content correctly REJECTED.\n")


# --- Scenario C: whole tool call written as plain text (Python-call syntax),
# recovered via _parse_tool_calls_from_text, guardrail still applied to it --
reset_mock()
step1c = FakeResp(
    FakeMessage(tool_calls=[FakeToolCall("tc1", "get_appointments", json.dumps({}))]),
    "tool_calls",
)
text_call = (
    "render_view(heading='Today\\'s appointments', components=[{'type': 'list', 'rows': ["
    "{'name': 'Camila Lopez', 'note': 'Dr. Sarah Bennett — checked in for Annual physical'}, "
    "{'name': 'Derrick Lin', 'note': \"Dr. Marcus Reyes — booked for Follow-up\"}"
    "]}])"
)
step2c = FakeResp(FakeMessage(content=text_call), "stop")
client_mock3 = mock.Mock()
client_mock3.chat.completions.create.side_effect = [step1c, step2c]
agent_healthcare.client = client_mock3

result_c = agent_healthcare.run_agent("what appointments are there today?", max_steps=8, verbose=True)
assert "render" in result_c, f"Scenario C: expected recovered render, got {result_c}"
rows_c = result_c["render"]["components"][0]["rows"]
assert len(rows_c) == 2, f"Scenario C: expected 2 recovered rows, got {len(rows_c)}"
print("PASSED — Scenario C: render_view written as plain Python-call-syntax text recovered,")
print("         guardrail-checked, and accepted.\n")


# --- Scenario D: placeholder-label denylist catches "Patient 1" style
# invented labels even though they're single tokens (would slip the generic
# guardrail's 2-token minimum) ---------------------------------------------
reset_mock()
step1d = FakeResp(
    FakeMessage(tool_calls=[FakeToolCall("tc1", "get_appointments", json.dumps({}))]),
    "tool_calls",
)
step2d_args = json.dumps({
    "heading": "Today's appointments",
    "components": [{
        "type": "list",
        "rows": [
            {"name": "Patient 1", "note": "Provider"},
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
agent_healthcare.client = client_mock4

result_d = agent_healthcare.run_agent("what appointments are there today?", max_steps=8, verbose=True)
assert "render" not in result_d, f"Scenario D: placeholder labels should have been rejected, got {result_d}"
print("PASSED — Scenario D: placeholder labels ('Patient 1', 'Provider') correctly REJECTED")
print("         by _find_placeholder_labels even though they're single tokens.\n")


# --- Scenario E: existing lazy-clarify-question rejection still works after
# the refactor — a question that just echoes the user's request back should
# still be rejected with LAZY_CLARIFY_NUDGE ---------------------------------
reset_mock()
step1e = FakeResp(
    FakeMessage(tool_calls=[FakeToolCall(
        "tc1", "ask_user",
        json.dumps({"question": "what appointments need attention right now?"}),
    )]),
    "tool_calls",
)
step2e_args = json.dumps({})
step2e = FakeResp(
    FakeMessage(tool_calls=[FakeToolCall("tc2", "get_appointments", step2e_args)]),
    "tool_calls",
)
step3e_args = json.dumps({
    "heading": "Today's appointments",
    "components": [{
        "type": "list",
        "rows": [
            {"name": "Camila Lopez", "note": "Dr. Sarah Bennett — checked in for Annual physical"},
        ],
    }],
})
step3e = FakeResp(
    FakeMessage(tool_calls=[FakeToolCall("tc3", "render_view", step3e_args)]),
    "tool_calls",
)
client_mock5 = mock.Mock()
client_mock5.chat.completions.create.side_effect = [step1e, step2e, step3e]
agent_healthcare.client = client_mock5

result_e = agent_healthcare.run_agent("what appointments need attention right now?", max_steps=8, verbose=True)
# The ask_user call should have been rejected (lazy clarify), and the model
# should have proceeded straight to fetching + rendering instead of pausing
# for clarification.
assert "clarify" not in result_e, f"Scenario E: lazy clarify question should have been rejected, not surfaced, got {result_e}"
assert "render" in result_e, f"Scenario E: expected the agent to proceed to a real render after rejection, got {result_e}"
first_call_messages = client_mock5.chat.completions.create.call_args_list[1].kwargs["messages"]
tool_msgs_e = [m for m in first_call_messages if m.get("role") == "tool"]
assert any(agent_healthcare.LAZY_CLARIFY_NUDGE in (m.get("content") or "") for m in tool_msgs_e), \
    f"Scenario E: expected LAZY_CLARIFY_NUDGE in the tool-role rejection message, got {tool_msgs_e}"
print("PASSED — Scenario E: lazy clarifying question (echoing the original request)")
print("         still correctly REJECTED with LAZY_CLARIFY_NUDGE after the refactor.\n")


print("ALL HEALTHCARE PORT SCENARIOS PASSED")
