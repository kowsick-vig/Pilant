import sys, json
from unittest.mock import patch, MagicMock

sys.path.insert(0, "/home/claude/pilant-agent")
import agent_helpdesk as ah

def make_tool_call(id, name, args):
    tc = MagicMock()
    tc.id = id
    tc.function.name = name
    tc.function.arguments = json.dumps(args)
    tc.model_dump.return_value = {"id": id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
    return tc

def make_resp(tool_calls=None, content=None):
    msg = MagicMock()
    msg.tool_calls = tool_calls
    msg.content = content
    resp = MagicMock()
    resp.choices = [MagicMock(message=msg)]
    return resp

print("=== TEST A: non-ambiguous straight-to-render ===")
with patch.object(ah.client.chat.completions, "create") as mock_create:
    mock_create.side_effect = [
        make_resp(tool_calls=[make_tool_call("1", "get_tickets", {"status": "open"})]),
        make_resp(tool_calls=[make_tool_call("2", "render_view", {
            "heading": "Open tickets needing attention",
            "meta": "3 tickets currently open",
            "components": [
                {"type": "list", "title": "Open tickets", "rows": [
                    {"name": "TK-1042: Can't reset password", "note": "priya@acme.io", "badge": {"text": "high", "tone": "warning"}},
                ]}
            ]
        })]),
    ]
    result = ah.run_agent("what open tickets need attention?", verbose=False)
    assert "render" in result, result
    assert "clarify" not in result
    print("PASS:", json.dumps(result)[:150])

print("\n=== TEST B: ask_user pause -> resume -> render round trip ===")
with patch.object(ah.client.chat.completions, "create") as mock_create:
    mock_create.side_effect = [
        make_resp(tool_calls=[make_tool_call("1", "ask_user", {"question": "Do you mean escalated tickets or all open ones?"})]),
    ]
    result = ah.run_agent("what needs attention?", verbose=False)
    assert "clarify" in result, result
    assert result["clarify"] == "Do you mean escalated tickets or all open ones?"
    print("PASS pause:", result["clarify"])

    messages = result["messages"] + [{"role": "user", "content": "escalated ones"}]
    mock_create.side_effect = [
        make_resp(tool_calls=[make_tool_call("2", "get_tickets", {"status": "escalated"})]),
        make_resp(tool_calls=[make_tool_call("3", "render_view", {
            "heading": "Escalated tickets",
            "meta": "3 tickets escalated",
            "components": [
                {"type": "list", "title": "Escalated tickets", "rows": [
                    {"name": "TK-1041: Invoice shows duplicate charge", "note": "marcus@globex.com", "badge": {"text": "critical", "tone": "critical"}},
                    {"name": "TK-1033: SSO login loop", "note": "devon@stark.io", "badge": {"text": "critical", "tone": "critical"}},
                    {"name": "TK-1018: Can't cancel subscription", "note": "eve@hooli.com", "badge": {"text": "high", "tone": "critical"}},
                ]}
            ]
        })]),
    ]
    result2 = ah.run_agent(messages=messages, fetched_data=result.get("fetched_data", False), verbose=False)
    assert "render" in result2, result2
    print("PASS resume+render:", json.dumps(result2)[:150])

print("\n=== TEST C: network error -> clean error, no hang, _network_error stripped ===")
import openai
with patch.object(ah.client.chat.completions, "create") as mock_create:
    mock_create.side_effect = openai.APITimeoutError(request=MagicMock())
    result = ah.run_agent("anything", verbose=False)
    assert "error" in result, result
    assert "_network_error" not in result
    print("PASS:", result["error"][:80], "| call_count:", mock_create.call_count)

print("\n=== TEST D: render_view rejected before data fetch -> nudged, then recovers ===")
with patch.object(ah.client.chat.completions, "create") as mock_create:
    mock_create.side_effect = [
        make_resp(tool_calls=[make_tool_call("1", "render_view", {"heading": "x", "components": []})]),
        make_resp(tool_calls=[make_tool_call("2", "get_tickets", {})]),
        make_resp(tool_calls=[make_tool_call("3", "render_view", {
            "heading": "All tickets",
            "components": [{"type": "list", "title": "All", "rows": [{"name": "TK-1042"}]}]
        })]),
    ]
    result = ah.run_agent("show everything", verbose=False)
    assert "render" in result, result
    print("PASS:", json.dumps(result)[:120])

print("\nALL TESTS PASSED")
