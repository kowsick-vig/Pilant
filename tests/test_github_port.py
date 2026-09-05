"""
REWRITTEN 2026-08-26 for the NVIDIA -> Claude migration. Mocks claude_engine.client
(the shared model client every connector now uses) with Claude Messages-API-shape
scripted responses instead of agent_github's old OpenAI-shape
chat.completions.create.

Scenario C: same replacement rationale as test_slack_port.py's — the original
tested recovering a tool call the model wrote as plain text
(_parse_tool_calls_from_text), a llama-3.1-8b failure mode that doesn't exist
for Claude and whose recovery code is deleted. Replaced with a multi-tool_use-
block-in-one-turn test (fetch + render together), which the old NVIDIA model's
chat template couldn't handle at all but Claude/claude_engine.py handles
correctly.

Scenario E: the original inspected scope_issues() filtering on the now-deleted
recovered-text fetch path specifically. Rewritten to inspect the SAME property
(an out-of-scope issue is filtered BEFORE it ever reaches the model, not just
absent from the final render) on the real tool_use dispatch path instead — by
capturing the tool_result content handed back to the model on the call
immediately after a real get_github_issues tool_use block.
"""
import sys, json, unittest.mock as mock

sys.path.insert(0, "/home/claude/pilant-agent")

import connectors_github

FAKE_ISSUES = [
    {"number": 101, "title": "deploy hangs on staging during DB migration", "state": "open",
     "labels": ["bug", "critical"], "opened": "2026-08-20T10:00:00Z", "comments": 3,
     "url": "https://github.com/acme/widgets/issues/101"},
    {"number": 102, "title": "add dark mode toggle to settings page", "state": "open",
     "labels": ["enhancement"], "opened": "2026-08-21T09:00:00Z", "comments": 1,
     "url": "https://github.com/acme/widgets/issues/102"},
    {"number": 103, "title": "login page leaks session token in query string", "state": "open",
     "labels": ["bug", "security"], "opened": "2026-08-22T11:00:00Z", "comments": 5,
     "url": "https://github.com/acme/widgets/issues/103"},
]
connectors_github.get_github_issues = mock.Mock(return_value=FAKE_ISSUES)

import agent_github
import claude_engine as ce
agent_github.get_github_issues = connectors_github.get_github_issues

from users import get_user, DEFAULT_USER


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
    connectors_github.get_github_issues.reset_mock(return_value=True)
    connectors_github.get_github_issues.return_value = FAKE_ISSUES
    agent_github.get_github_issues = connectors_github.get_github_issues


OWNER = get_user(DEFAULT_USER)  # label_scope=None, unrestricted


# --- Scenario A: real tool_calls, clean happy path (fabrication guardrail
# must NOT reject grounded content) ---------------------------------------
reset_mock()
step1 = FakeResp([FakeToolUseBlock("tc1", "get_github_issues", {"state": "open"})], "tool_use")
step2 = FakeResp([FakeToolUseBlock("tc2", "render_view", {
    "heading": "Open issues",
    "components": [{
        "type": "list",
        "rows": [
            {"name": "deploy hangs on staging during DB migration", "note": "#101 · bug, critical"},
            {"name": "add dark mode toggle to settings page", "note": "#102 · enhancement"},
            {"name": "login page leaks session token in query string", "note": "#103 · bug, security"},
        ],
    }],
})], "tool_use")
client_mock = mock.Mock()
client_mock.messages.create.side_effect = [step1, step2]

with mock.patch.object(ce, "client", client_mock):
    result = agent_github.run_agent("what open issues need attention?", user=OWNER, max_steps=8, verbose=True)
assert "render" in result, f"Scenario A: expected a render, got {result}"
rows = result["render"]["components"][0]["rows"]
assert len(rows) == 3, f"Scenario A: expected 3 rows, got {len(rows)}"
print("PASSED — Scenario A: real tool_calls, grounded content accepted cleanly.\n")


# --- Scenario B: fabrication guardrail actually catches invented content,
# and — since force_tool_choice=True here (unlike Slack/Gmail), there is NO
# plain-text escape hatch — a model that keeps re-submitting the same
# fabricated render eventually exhausts max_steps as a real {"error": ...} --
reset_mock()
step1b = FakeResp([FakeToolUseBlock("tc1", "get_github_issues", {"state": "open"})], "tool_use")
fabricated_args = {
    "heading": "Open issues",
    "components": [{
        "type": "list",
        "rows": [
            {"name": "Totally fabricated issue that was never filed", "note": "invented details, not real"},
        ],
    }],
}
step2b = FakeResp([FakeToolUseBlock("tc2", "render_view", fabricated_args)], "tool_use")
step3b = FakeResp([FakeToolUseBlock("tc3", "render_view", fabricated_args)], "tool_use")
client_mock2 = mock.Mock()
client_mock2.messages.create.side_effect = [step1b, step2b] + [step3b] * 6  # fills out max_steps=8

with mock.patch.object(ce, "client", client_mock2):
    result_b = agent_github.run_agent("what open issues need attention?", user=OWNER, max_steps=8, verbose=True)
assert "render" not in result_b, f"Scenario B: fabricated content should have been rejected, got {result_b}"
assert result_b.get("error") == "hit max_steps without a valid render_view", (
    f"Scenario B: force_tool_choice=True means a model that never corrects itself should "
    f"exhaust max_steps, got {result_b}"
)
last_call_messages = client_mock2.messages.create.call_args_list[-1].kwargs["messages"]
tool_result_texts = [
    block.get("content") or ""
    for m in last_call_messages if m.get("role") == "user" and isinstance(m.get("content"), list)
    for block in m["content"] if isinstance(block, dict) and block.get("type") == "tool_result"
]
assert any("don't match" in t for t in tool_result_texts), (
    f"Scenario B: expected the fabrication rejection nudge in the conversation, got: {tool_result_texts!r}"
)
print("PASSED — Scenario B: fabricated (ungrounded) render_view content correctly REJECTED,")
print("         repeatedly, with no plain-text escape (force_tool_choice=True) until max_steps.\n")


# --- Scenario C: multiple tool_use blocks in ONE turn (fetch + render
# together) dispatched correctly, in order — the old NVIDIA model's chat
# template couldn't handle more than one tool_calls entry per turn at all --
reset_mock()
step1c = FakeResp(
    [
        FakeToolUseBlock("tc1", "get_github_issues", {"state": "open"}),
        FakeToolUseBlock("tc2", "render_view", {
            "heading": "Open issues",
            "components": [{
                "type": "list",
                "rows": [
                    {"name": "deploy hangs on staging during DB migration", "note": "#101 · bug, critical"},
                    {"name": "login page leaks session token in query string", "note": "#103 · bug, security"},
                ],
            }],
        }),
    ],
    "tool_use",
)
client_mock3 = mock.Mock()
client_mock3.messages.create.side_effect = [step1c]

with mock.patch.object(ce, "client", client_mock3):
    result_c = agent_github.run_agent("what open issues need attention?", user=OWNER, max_steps=8, verbose=True)
assert "render" in result_c, f"Scenario C: expected a render from a single multi-block turn, got {result_c}"
rows_c = result_c["render"]["components"][0]["rows"]
assert len(rows_c) == 2, f"Scenario C: expected 2 rows, got {len(rows_c)}"
assert client_mock3.messages.create.call_count == 1, (
    f"Scenario C: expected exactly ONE model call, got {client_mock3.messages.create.call_count}"
)
print("PASSED — Scenario C: a single model turn with TWO tool_use blocks (fetch + render)")
print("         was dispatched correctly in order — no NVIDIA-era 'keep only the first")
print("         tool_call' workaround needed for Claude.\n")


# --- Scenario D: placeholder-label denylist catches "Issue 1" / "Title" style
# invented labels even though they're single tokens (would slip the generic
# guardrail's 2-token minimum) — same max_steps-exhaustion shape as B -------
reset_mock()
step1d = FakeResp([FakeToolUseBlock("tc1", "get_github_issues", {"state": "open"})], "tool_use")
placeholder_args = {
    "heading": "Open issues",
    "components": [{
        "type": "list",
        "rows": [
            {"name": "Issue 1", "note": "Title"},
        ],
    }],
}
step2d = FakeResp([FakeToolUseBlock("tc2", "render_view", placeholder_args)], "tool_use")
step3d = FakeResp([FakeToolUseBlock("tc3", "render_view", placeholder_args)], "tool_use")
client_mock4 = mock.Mock()
client_mock4.messages.create.side_effect = [step1d, step2d] + [step3d] * 6

with mock.patch.object(ce, "client", client_mock4):
    result_d = agent_github.run_agent("what open issues need attention?", user=OWNER, max_steps=8, verbose=True)
assert "render" not in result_d, f"Scenario D: placeholder labels should have been rejected, got {result_d}"
print("PASSED — Scenario D: placeholder labels ('Issue 1', 'Title') correctly REJECTED")
print("         by _find_placeholder_labels even though they're single tokens.\n")


# --- Scenario E: scope_issues identity filtering on the REAL tool_use
# dispatch path — the connector itself (mocked) returns all 3 issues
# regardless of who's asking; scope_issues() is the ONLY thing standing
# between that raw result and what the model ever sees. This inspects the
# tool_result content handed to the model on the call immediately AFTER the
# fetch, confirming the out-of-scope issue was filtered before that
# hand-back — not just that a later render happened to omit it. -----------
reset_mock()
ANALYST = get_user("analyst")  # label_scope = ["bug", "critical", "security"]
assert ANALYST["label_scope"] == ["bug", "critical", "security"]

step1e = FakeResp([FakeToolUseBlock("tc1", "get_github_issues", {"state": "open"})], "tool_use")
step2e = FakeResp([FakeToolUseBlock("tc2", "render_view", {
    "heading": "Open issues (in scope)",
    "components": [{
        "type": "list",
        "rows": [
            {"name": "deploy hangs on staging during DB migration", "note": "#101 · bug, critical"},
            {"name": "login page leaks session token in query string", "note": "#103 · bug, security"},
        ],
    }],
})], "tool_use")
client_mock5 = mock.Mock()
client_mock5.messages.create.side_effect = [step1e, step2e]

with mock.patch.object(ce, "client", client_mock5):
    result_e = agent_github.run_agent("what open issues need attention?", user=ANALYST, max_steps=8, verbose=True)

assert "render" in result_e, f"Scenario E: expected a render, got {result_e}"
assert client_mock5.messages.create.call_count == 2, (
    f"Scenario E: expected exactly 2 model calls, got {client_mock5.messages.create.call_count}"
)
second_call_messages = client_mock5.messages.create.call_args_list[1].kwargs["messages"]
tool_result_texts = [
    block.get("content") or ""
    for m in second_call_messages if m.get("role") == "user" and isinstance(m.get("content"), list)
    for block in m["content"] if isinstance(block, dict) and block.get("type") == "tool_result"
]
combined_nudge_text = " ".join(tool_result_texts)

assert '"number": 101' in combined_nudge_text, (
    f"Scenario E: expected in-scope issue #101 in the real data handed back to the model, "
    f"got: {combined_nudge_text!r}"
)
assert '"number": 103' in combined_nudge_text, (
    f"Scenario E: expected in-scope issue #103 in the real data handed back to the model, "
    f"got: {combined_nudge_text!r}"
)
assert "add dark mode toggle to settings page" not in combined_nudge_text, (
    "Scenario E: the enhancement-only issue #102 leaked into the fetch result handed back to "
    "the model — scope_issues() was not applied before the dispatch handed the tool_result back"
)
assert '"number": 102' not in combined_nudge_text, (
    "Scenario E: issue #102 (label 'enhancement', outside the analyst's label_scope) must be "
    "filtered out by scope_issues() before the fetch result is handed back to the model"
)
print("PASSED — Scenario E: get_github_issues's real result is filtered through scope_issues()")
print("         BEFORE it's ever handed back to the model — the analyst's out-of-scope issue")
print("         (#102, 'enhancement') never appeared in the tool_result content.\n")

print("ALL GITHUB PORT SCENARIOS PASSED")
