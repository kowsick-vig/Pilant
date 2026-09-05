import sys, os, json, io
from unittest.mock import patch, MagicMock

sys.path.insert(0, "/home/claude/pilant-agent")

# --- TEST 1: missing credentials raise clear errors ---
for k in ("SHOPIFY_STORE_URL", "SHOPIFY_ACCESS_TOKEN", "SHOPIFY_API_VERSION", "SHOPIFY_LOW_STOCK_THRESHOLD"):
    os.environ.pop(k, None)

import connectors_retail as cr
import importlib
importlib.reload(cr)

print("=== TEST 1: missing SHOPIFY_STORE_URL ===")
try:
    cr.get_orders()
    print("FAIL: should have raised")
except RuntimeError as e:
    assert "SHOPIFY_STORE_URL" in str(e)
    print("PASS:", str(e)[:80])

os.environ["SHOPIFY_STORE_URL"] = "test-store.myshopify.com"
print("\n=== TEST 2: missing SHOPIFY_ACCESS_TOKEN ===")
try:
    cr.get_orders()
    print("FAIL: should have raised")
except RuntimeError as e:
    assert "SHOPIFY_ACCESS_TOKEN" in str(e)
    print("PASS:", str(e)[:80])

os.environ["SHOPIFY_ACCESS_TOKEN"] = "shpat_faketoken123"

FAKE_ORDERS_RESPONSE = {
    "orders": [
        {
            "name": "#1001",
            "email": "a@example.com",
            "customer": {"id": 111, "first_name": "Lena", "last_name": "Ferreira"},
            "line_items": [{"quantity": 2}, {"quantity": 1}],
            "total_price": "212.00",
            "currency": "USD",
            "fulfillment_status": None,
            "cancelled_at": None,
            "financial_status": "paid",
            "refunds": [],
            "created_at": "2026-08-20T10:00:00Z",
            "cancel_reason": None,
        },
        {
            "name": "#1002",
            "email": "b@example.com",
            "customer": {"id": 112, "first_name": "J.", "last_name": "Okafor"},
            "line_items": [{"quantity": 1}],
            "total_price": "68.00",
            "currency": "USD",
            "fulfillment_status": "fulfilled",
            "cancelled_at": None,
            "financial_status": "partially_refunded",
            "refunds": [{"id": 999}],
            "created_at": "2026-08-18T10:00:00Z",
            "cancel_reason": None,
        },
        {
            "name": "#1003",
            "email": "c@example.com",
            "customer": {"id": 113, "first_name": "M.", "last_name": "Chen"},
            "line_items": [{"quantity": 3}],
            "total_price": "145.00",
            "currency": "USD",
            "fulfillment_status": None,
            "cancelled_at": "2026-08-19T10:00:00Z",
            "financial_status": "voided",
            "refunds": [],
            "created_at": "2026-08-17T10:00:00Z",
            "cancel_reason": "customer",
        },
    ]
}

FAKE_PRODUCTS_RESPONSE = {
    "products": [
        {
            "title": "Linen Wrap Dress",
            "product_type": "Dresses",
            "variants": [
                {"id": 1, "sku": "HC-DRS-014-SGE", "title": "Sage", "inventory_quantity": 2},
                {"id": 2, "sku": "HC-DRS-014-BLK", "title": "Black", "inventory_quantity": 34},
            ],
        },
        {
            "title": "Merino Crew Knit",
            "product_type": "Knitwear",
            "variants": [
                {"id": 3, "sku": "HC-KNT-021-OAT", "title": "Default Title", "inventory_quantity": 0},
            ],
        },
    ]
}

def fake_urlopen(req, timeout=None):
    url = req.full_url
    headers = dict(req.header_items())
    assert headers.get("X-shopify-access-token") == "shpat_faketoken123", headers
    assert "test-store.myshopify.com" in url
    if "/orders.json" in url:
        body = json.dumps(FAKE_ORDERS_RESPONSE).encode()
    elif "/products.json" in url:
        body = json.dumps(FAKE_PRODUCTS_RESPONSE).encode()
    else:
        raise AssertionError(f"unexpected URL: {url}")
    cm = MagicMock()
    cm.__enter__.return_value = io.BytesIO(body)
    cm.__exit__.return_value = False
    return cm

print("\n=== TEST 3: get_orders() normalizes real Shopify shapes correctly ===")
with patch("urllib.request.urlopen", side_effect=fake_urlopen):
    orders = cr.get_orders()
    assert len(orders) == 3
    o1, o2, o3 = orders
    assert o1["id"] == "#1001" and o1["customer"] == "Lena Ferreira" and o1["items"] == 3 and o1["status"] == "unfulfilled"
    assert o2["status"] == "fulfilled" and o2["has_refund"] is True
    assert o3["status"] == "cancelled" and o3["flag"] == "customer"
    print("PASS:", json.dumps(orders, indent=None)[:200])

print("\n=== TEST 4: get_orders(status='cancelled') filters correctly ===")
with patch("urllib.request.urlopen", side_effect=fake_urlopen):
    cancelled = cr.get_orders(status="cancelled")
    assert len(cancelled) == 1 and cancelled[0]["id"] == "#1003"
    print("PASS:", cancelled[0]["id"])

print("\n=== TEST 5: get_inventory() normalizes variants, low_stock_only, category, threshold ===")
os.environ["SHOPIFY_LOW_STOCK_THRESHOLD"] = "5"
with patch("urllib.request.urlopen", side_effect=fake_urlopen):
    inv = cr.get_inventory()
    assert len(inv) == 3
    sage = next(i for i in inv if i["sku"] == "HC-DRS-014-SGE")
    assert sage["status"] == "low" and sage["name"] == "Linen Wrap Dress — Sage" and sage["category"] == "Dresses"
    oat = next(i for i in inv if i["sku"] == "HC-KNT-021-OAT")
    assert oat["status"] == "out" and oat["name"] == "Merino Crew Knit"  # no "— Default Title" suffix
    print("PASS:", json.dumps(inv, indent=None)[:200])

    low_only = cr.get_inventory(low_stock_only=True)
    assert {i["sku"] for i in low_only} == {"HC-DRS-014-SGE", "HC-KNT-021-OAT"}
    print("PASS low_stock_only:", [i["sku"] for i in low_only])

    dresses = cr.get_inventory(category="dresses")  # case-insensitive match against "Dresses"
    assert {i["sku"] for i in dresses} == {"HC-DRS-014-SGE", "HC-DRS-014-BLK"}
    print("PASS category filter (case-insensitive):", [i["sku"] for i in dresses])

print("\n=== TEST 6: HTTP error surfaces a clean RuntimeError with a hint ===")
import urllib.error
def fake_urlopen_401(req, timeout=None):
    raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(b'{"errors":"Invalid API key"}'))
with patch("urllib.request.urlopen", side_effect=fake_urlopen_401):
    try:
        cr.get_orders()
        print("FAIL: should have raised")
    except RuntimeError as e:
        assert "401" in str(e) and "SHOPIFY_ACCESS_TOKEN" in str(e)
        print("PASS:", str(e)[:150])

print("\nALL CONNECTORS_RETAIL TESTS PASSED")
