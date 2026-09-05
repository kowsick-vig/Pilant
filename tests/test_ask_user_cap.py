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

print("=== TEST: model tries to ask_user a SECOND time after already asking once -> rejected, forced to proceed ===")
with patch.object(ah.client.chat.completions, "create") as mock_create:
    mock_create.side_effect = [
        make_resp(tool_calls=[make_tool_call("1", "ask_user", {"question": "What kind of issues are you looking for?"})]),
    ]
    result = ah.run_agent("what's waiting to be documented?", verbose=True)
    assert "clarify" in result
    print("PASS: first ask_user paused as expected")

    # Person answers "critical" -- but the (weaker) model tries to ask_user AGAIN
    # instead of proceeding, exactly like the bug seen in production.
    messages = result["messages"] + [{"role": "user", "content": "critical"}]
    mock_create.side_effect = [
        make_resp(tool_calls=[make_tool_call("2", "ask_user", {"question": "What kind of issues are you looking for?"})]),
        make_resp(tool_calls=[make_tool_call("3", "get_tickets", {"priority": "critical"})]),
        make_resp(tool_calls=[make_tool_call("4", "render_view", {
            "heading": "Critical tickets",
            "components": [{"type": "list", "title": "Critical", "rows": [{"name": "TK-1041"}]}]
        })]),
    ]
    result2 = ah.run_agent(messages=messages, fetched_data=result.get("fetched_data", False), verbose=True)
    assert "render" in result2, result2
    assert "clarify" not in result2
    print("PASS: second ask_user attempt was rejected in code, model was forced to proceed, request completed")
    print(json.dumps(result2)[:150])

print("\nALL ASK_USER CAP TESTS PASSED")
