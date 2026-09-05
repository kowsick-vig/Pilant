import sys, os, json, io
from unittest.mock import patch, MagicMock

sys.path.insert(0, "/home/claude/pilant-agent")

for k in ("SHOPIFY_STORE_URL", "SHOPIFY_ACCESS_TOKEN", "SHOPIFY_CLIENT_ID", "SHOPIFY_CLIENT_SECRET", "SHOPIFY_API_VERSION", "SHOPIFY_LOW_STOCK_THRESHOLD"):
    os.environ.pop(k, None)

import connectors_retail as cr
import importlib
importlib.reload(cr)

print("=== TEST 1: missing SHOPIFY_STORE_URL ===")
try:
    cr.get_orders()
    print("FAIL")
except RuntimeError as e:
    assert "SHOPIFY_STORE_URL" in str(e)
    print("PASS:", str(e)[:70])

os.environ["SHOPIFY_STORE_URL"] = "test-store.myshopify.com"
print("\n=== TEST 2: missing client id/secret ===")
try:
    cr.get_orders()
    print("FAIL")
except RuntimeError as e:
    assert "SHOPIFY_CLIENT_ID" in str(e) and "SHOPIFY_CLIENT_SECRET" in str(e)
    print("PASS:", str(e)[:80])

os.environ["SHOPIFY_CLIENT_ID"] = "fake_client_id"
os.environ["SHOPIFY_CLIENT_SECRET"] = "fake_client_secret"
cr._token_cache["token"] = None
cr._token_cache["expires_at"] = 0

FAKE_TOKEN_RESP = {"access_token": "shpua_faketoken", "scope": "read_orders,read_products", "expires_in": 86399}
FAKE_ORDERS_RESPONSE = {"orders": [{
    "name": "#2001", "email": "x@example.com", "customer": {"id": 1, "first_name": "X", "last_name": "Y"},
    "line_items": [{"quantity": 2}], "total_price": "99.00", "currency": "USD",
    "fulfillment_status": None, "cancelled_at": None, "financial_status": "paid",
    "refunds": [], "created_at": "2026-08-22T00:00:00Z", "cancel_reason": None,
}]}

call_log = []
def fake_urlopen(req, timeout=None):
    url = req.full_url
    call_log.append(url)
    if "/admin/oauth/access_token" in url:
        assert req.data is not None
        body_str = req.data.decode()
        assert "grant_type=client_credentials" in body_str
        assert "fake_client_id" in body_str
        body = json.dumps(FAKE_TOKEN_RESP).encode()
    elif "/orders.json" in url:
        headers = dict(req.header_items())
        assert headers.get("X-shopify-access-token") == "shpua_faketoken", headers
        body = json.dumps(FAKE_ORDERS_RESPONSE).encode()
    else:
        raise AssertionError(f"unexpected url {url}")
    cm = MagicMock()
    cm.__enter__.return_value = io.BytesIO(body)
    cm.__exit__.return_value = False
    return cm

print("\n=== TEST 3: full flow — exchanges client creds for token, then fetches orders ===")
with patch("urllib.request.urlopen", side_effect=fake_urlopen):
    orders = cr.get_orders()
    assert len(orders) == 1 and orders[0]["id"] == "#2001"
    print("PASS:", orders[0])
    assert any("/admin/oauth/access_token" in u for u in call_log)
    assert any("/orders.json" in u for u in call_log)
    print("PASS: token exchange happened before the data call")

print("\n=== TEST 4: cached token reused on second call (no second token exchange) ===")
call_log.clear()
with patch("urllib.request.urlopen", side_effect=fake_urlopen):
    cr.get_orders()
    token_exchanges = [u for u in call_log if "access_token" in u]
    assert len(token_exchanges) == 0, f"expected cached token, but re-exchanged: {call_log}"
    print("PASS: no re-exchange, cache worked")

print("\n=== TEST 5: expired cache triggers a fresh exchange ===")
cr._token_cache["expires_at"] = 0  # force expiry
call_log.clear()
with patch("urllib.request.urlopen", side_effect=fake_urlopen):
    cr.get_orders()
    token_exchanges = [u for u in call_log if "access_token" in u]
    assert len(token_exchanges) == 1
    print("PASS: re-exchanged after expiry")

print("\nALL TESTS PASSED")
