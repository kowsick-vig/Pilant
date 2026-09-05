import sys, os, json, io
from unittest.mock import patch, MagicMock

sys.path.insert(0, "/home/claude/pilant-agent")
os.environ["SHOPIFY_STORE_URL"] = "test-store.myshopify.com"
os.environ["SHOPIFY_ACCESS_TOKEN"] = "shpat_faketoken123"

import agent_retail as ar

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

FAKE_ORDERS = {"orders": [
    {"name": "#1001", "email": "a@x.com", "customer": {"id": 1, "first_name": "A", "last_name": "B"},
     "line_items": [{"quantity": 1}], "total_price": "50.00", "currency": "USD",
     "fulfillment_status": None, "cancelled_at": None, "financial_status": "paid",
     "refunds": [], "created_at": "2026-08-20T10:00:00Z", "cancel_reason": None},
]}

def fake_urlopen(req, timeout=None):
    body = json.dumps(FAKE_ORDERS).encode()
    cm = MagicMock()
    cm.__enter__.return_value = io.BytesIO(body)
    cm.__exit__.return_value = False
    return cm

print("=== E2E: real (mocked-network) Shopify data flows through the full agent loop ===")
with patch("urllib.request.urlopen", side_effect=fake_urlopen), \
     patch.object(ar.client.chat.completions, "create") as mock_create:
    mock_create.side_effect = [
        make_resp(tool_calls=[make_tool_call("1", "get_orders", {"status": "unfulfilled"})]),
        make_resp(tool_calls=[make_tool_call("2", "render_view", {
            "heading": "Unfulfilled orders",
            "meta": "1 order awaiting fulfillment",
            "components": [{"type": "list", "title": "Unfulfilled", "rows": [
                {"name": "#1001: A B", "note": "$50.00 USD", "badge": {"text": "unfulfilled", "tone": "warning"}}
            ]}]
        })]),
    ]
    result = ar.run_agent("what orders need attention right now?", verbose=True)
    assert "error" not in result, result
    assert result["heading"] == "Unfulfilled orders"
    print("PASS:", json.dumps(result)[:200])

print("\nALL E2E TESTS PASSED")
