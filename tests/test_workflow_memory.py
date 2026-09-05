"""
Tests for the 2026-08-25 cross-request conversation memory feature: a
workflow remembers what it already built/discussed between separate
top-level requests (not just within one clarify round-trip), so a short
follow-up like "make it more compact" has real context instead of the agent
starting from nothing every time.

REWRITTEN 2026-08-26 for the NVIDIA -> Claude migration: this file used to
mock agent_gmail.client directly with OpenAI-shape chat.completions.create
responses. Since every connector's model calls now go through the shared
claude_engine.client (see claude_engine.py's docstring), this patches
claude_engine.client instead, with Claude Messages-API-shape responses
(resp.content = list of content blocks, resp.stop_reason). Scenarios 1 and 2
test studio.py's own memory bookkeeping (_render_memory_line,
_append_memory_exchange) directly and don't touch the model API at all, so
they're unchanged from the original. Scenarios 3-5 mock the model and are
updated for the one real shape difference that matters here: studio.py's
memory is still seeded/stored as [{"role": "system", ...}, ...] (unchanged
— see claude_engine._split_system's docstring on why that's still correct),
but what actually reaches the model is now `system=` as a separate kwarg
plus `messages=` WITHOUT the system entry, not a single flat messages list
with role="system" as the first item.
"""
import sys, json, unittest.mock as mock

sys.path.insert(0, "/home/claude/pilant-agent")
import os
os.environ.setdefault("GMAIL_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
os.environ.setdefault("GMAIL_CLIENT_SECRET", "test-client-secret")

import studio
import claude_engine as ce

studio.app.config["TESTING"] = True
client = studio.app.test_client()


def login():
    r = client.post("/login", data={"username": "kowsick", "password": "owner123"}, follow_redirects=True)
    assert r.status_code == 200, r.status_code


login()


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


def render_step(tc_id, heading, rows):
    return FakeResp(
        [FakeToolUseBlock(tc_id, "render_view", {
            "heading": heading,
            "components": [{"type": "list", "rows": rows}],
        })],
        "tool_use",
    )


def fetch_step(tc_id):
    return FakeResp(
        [FakeToolUseBlock(tc_id, "get_gmail_messages", {"unread_only": False, "query": "", "limit": 10})],
        "tool_use",
    )


FAKE_MESSAGES = [
    {"from": "a@x.com", "subject": "Alpha", "date": "Aug 25, 2026", "snippet": "First", "unread": True, "id": "m1"},
    {"from": "b@x.com", "subject": "Beta", "date": "Aug 25, 2026", "snippet": "Second", "unread": False, "id": "m2"},
]
studio.connectors_gmail.get_gmail_messages = mock.Mock(return_value=FAKE_MESSAGES)
studio.agent_gmail.get_gmail_messages = studio.connectors_gmail.get_gmail_messages

# ---- Scenario 1: _render_memory_line produces a compact, useful summary ---
line = studio._render_memory_line({
    "heading": "Unread emails",
    "components": [{"type": "list", "rows": [{"name": "a"}, {"name": "b"}, {"name": "c"}]}],
})
assert line == 'Built "Unread emails" — a list of 3 item(s).', line
print(f"PASSED — Scenario 1: _render_memory_line produces: {line!r}")

# ---- Scenario 2: _append_memory_exchange seeds with the system prompt and
# grows, then caps at MEMORY_MAX_EXCHANGES, always keeping the system msg --
wf = studio._create_workflow("gmail")
assert wf["memory"] is None
studio._append_memory_exchange(wf, "show unread", 'Built "Unread emails" — a list of 2 item(s).')
assert wf["memory"][0] == {"role": "system", "content": studio.agent_gmail.SYSTEM}
assert len(wf["memory"]) == 3  # system + 1 user + 1 assistant
for i in range(2, 10):
    studio._append_memory_exchange(wf, f"request {i}", f"Built request {i}")
# 1 (from above) + 9 more = 10 exchanges total; capped at MEMORY_MAX_EXCHANGES (6)
assert wf["memory"][0]["role"] == "system", "system message must survive trimming"
assert len(wf["memory"]) == 1 + studio.MEMORY_MAX_EXCHANGES * 2, (
    f"expected memory capped at 1 system + {studio.MEMORY_MAX_EXCHANGES} exchanges, got {len(wf['memory'])}"
)
user_contents = [m["content"] for m in wf["memory"] if m["role"] == "user"]
assert "show unread" not in user_contents, "the oldest exchange should have been trimmed away"
assert "request 9" in user_contents, "the most recent exchange must still be present"
print(f"PASSED — Scenario 2: memory grows, seeds with the system prompt, and caps at "
      f"{studio.MEMORY_MAX_EXCHANGES} exchanges (oldest dropped first, system always kept).")

# ---- Scenario 3: a real follow-up request through studio.py is seeded with
# the workflow's memory — proven by inspecting what was actually sent to the
# (mocked) model on the SECOND request: system= carries the SYSTEM prompt,
# messages= carries the prior exchange (NOT a role="system" first entry).
r = client.post("/studio/new/gmail", follow_redirects=True)
wf_id = studio.WORKFLOW_ORDER[0]

client_mock_1 = mock.Mock()
client_mock_1.messages.create.side_effect = [
    fetch_step("tc1"),
    render_step("tc2", "2 emails", [{"name": "Alpha"}, {"name": "Beta"}]),
]
with mock.patch.object(ce, "client", client_mock_1):
    client.post("/studio/message", data={"text": "show my inbox"}, follow_redirects=True)

wf = studio.WORKFLOWS[wf_id]
assert wf["memory"] is not None, "memory should be populated after the first successful build"
assert wf["memory"][0]["role"] == "system"
assert any("show my inbox" in m.get("content", "") for m in wf["memory"] if m["role"] == "user")
assert any("2 emails" in m.get("content", "") for m in wf["memory"] if m["role"] == "assistant")

captured_calls = []


def capture_create(*args, **kwargs):
    # Snapshot: kwargs["messages"] is the SAME list object claude_engine's loop keeps
    # appending to across steps, so a plain dict(kwargs) copy still aliases the live,
    # still-mutating list — copy the messages list itself too, one level deep.
    captured_calls.append({**kwargs, "messages": list(kwargs.get("messages") or [])})
    if len(captured_calls) == 1:
        return fetch_step("tc3")
    return render_step("tc4", "2 emails, compact", [{"name": "Alpha"}, {"name": "Beta"}])


client_mock_2 = mock.Mock()
client_mock_2.messages.create.side_effect = capture_create
with mock.patch.object(ce, "client", client_mock_2):
    client.post("/studio/message", data={"text": "make it more compact"}, follow_redirects=True)

# The FIRST call of this new exchange is what proves memory was actually
# seeded — the agent re-fetches fresh data on this new top-level request
# (fetched_data=False, by design — see _run_agent_for_workflow), but the
# system= AND messages= handed to the model on that very first call must
# already reflect the prior exchange.
first_call = captured_calls[0]
assert first_call.get("system") == studio.agent_gmail.SYSTEM, (
    "the follow-up's model call should carry the SAME system prompt via the system= kwarg, "
    "not embedded as a role='system' message"
)
sent_messages = list(first_call.get("messages") or [])
assert sent_messages and sent_messages[0]["role"] != "system", (
    "Claude's Messages API never accepts role='system' inside messages= — _split_system() "
    "must have stripped it out before the call"
)
assert any("show my inbox" in (m.get("content") or "") for m in sent_messages if isinstance(m.get("content"), str)), (
    "the follow-up's model call should include the PRIOR request in its context"
)
assert any("2 emails" in (m.get("content") or "") for m in sent_messages if isinstance(m.get("content"), str)), (
    "the follow-up's model call should include a summary of what was already built"
)
assert sent_messages[-1] == {"role": "user", "content": "make it more compact"}, (
    f"the follow-up's own new text should be the final message, got: {sent_messages[-1]}"
)
print("PASSED — Scenario 3: a real follow-up ('make it more compact') was seeded with the prior")
print("         request AND a summary of what was built (via system= + messages=), proving the")
print("         model actually saw it.")

# ---- Scenario 4: an error does NOT wipe out existing good memory ----------
wf2 = studio._create_workflow("gmail")
studio._append_memory_exchange(wf2, "show unread", 'Built "Unread" — a list of 2 item(s).')
memory_before = list(wf2["memory"])
with client.session_transaction() as sess:
    sess["current_workflow"] = wf2["id"]

client_mock_err = mock.Mock()
client_mock_err.messages.create.side_effect = Exception("boom")
with mock.patch.object(ce, "client", client_mock_err):
    client.post("/studio/message", data={"text": "do something that fails"}, follow_redirects=True)

assert studio.WORKFLOWS[wf2["id"]]["memory"] == memory_before, "a failed request should not touch existing good memory"
print("PASSED — Scenario 4: a failed follow-up request left the workflow's existing memory intact.")

# ---- Scenario 5: switching connectors clears memory ------------------------
# No model call needed here (connector-switch is a keyword-routing decision
# made before any run_agent() call) — patched anyway, with an error side
# effect, so this only passes if that's actually true.
wf3 = studio._create_workflow("gmail")
studio._append_memory_exchange(wf3, "show unread", 'Built "Unread" — a list of 2 item(s).')
assert wf3["memory"] is not None
with client.session_transaction() as sess:
    sess["current_workflow"] = wf3["id"]
client_mock_unused = mock.Mock()
client_mock_unused.messages.create.side_effect = AssertionError("no model call should happen on a bare connector-switch message")
with mock.patch.object(ce, "client", client_mock_unused):
    client.post("/studio/message", data={"text": "now connect slack"}, follow_redirects=True)
assert studio.WORKFLOWS[wf3["id"]]["connector"] == "slack"
assert studio.WORKFLOWS[wf3["id"]]["memory"] is None, "switching connectors should clear old memory"
print("PASSED — Scenario 5: switching a workflow to a different connector clears its old memory")
print("         (confirmed with no model call involved).")

print()
print("ALL WORKFLOW MEMORY SCENARIOS PASSED")
