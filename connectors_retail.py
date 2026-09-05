"""
A real connector — reads live orders and inventory from a Shopify store via
Shopify's Admin REST API. This replaces the earlier fabricated "Harriet &
Co" order/inventory data with an actual external system, the same way
connectors_github.py replaced connectors.py's fabricated incidents with
real GitHub issues.

Setup (see .env.example) — Shopify retired the old one-click "custom app,
reveal a permanent token" flow for new apps, so this uses the client
credentials grant instead (verified against shopify.dev as of 2026-08-22 —
if Shopify has changed this again, check
https://shopify.dev/docs/apps/build/dev-dashboard/get-api-access-tokens):
  SHOPIFY_STORE_URL     required, e.g. your-store.myshopify.com
  SHOPIFY_CLIENT_ID     required — from an app you create at
                        dev.shopify.com/dashboard: Create app -> on the
                        Versions tab set Admin API scopes (needs at least
                        read_orders and read_products) -> Release -> from
                        Home, Install app on your store -> Settings tab ->
                        copy the Client ID and Client secret. Only works
                        when the app and the store are in the same Shopify
                        organization, which is the normal case for your
                        own store.
  SHOPIFY_CLIENT_SECRET required — see above. Keep this out of git same as
                        any other secret.
  SHOPIFY_API_VERSION   optional, defaults to a recent stable version below.
                        Shopify retires API versions roughly a year after
                        release — if requests start failing with a 400
                        about an unsupported version, check
                        https://shopify.dev/docs/api/usage/versioning and
                        set this to whatever's current.
  SHOPIFY_LOW_STOCK_THRESHOLD  optional, defaults to 5. Shopify doesn't
                        have a native "reorder point" on every plan/API
                        scope, so "low stock" here just means "at or below
                        this many units," store-wide. Raise or lower it to
                        match how your store actually thinks about
                        low stock.

Known simplifications, called out honestly rather than silently:
  - The client-credentials access token Shopify hands back is short-lived
    (24 hours) by design — not a bug here, that's how this grant works.
    _get_access_token() below caches it in memory and re-exchanges the
    client ID/secret for a fresh one automatically once it's close to
    expiring, so nothing manual is needed day to day; the cache just
    doesn't survive a process restart (fine — the very next request
    fetches a new one transparently).
  - Only the first page of orders/products is fetched (up to 100 each).
    Fine for "what needs attention right now"-style questions; a store
    with a very high order/SKU volume would need real pagination added.
  - Shopify's REST API doesn't expose a formal customer-initiated "return
    request" the way the old fake data pretended one existed — real
    returns are a GraphQL-only feature. What's exposed here instead is
    `has_refund` (whether any refund has been issued against the order),
    which is the closest real signal REST gives us.
  - inventory_quantity, as returned on a product variant, is the total
    across all of the store's locations, not broken out per-location.
"""

import os
import json
import time
from pathlib import Path
import urllib.request
import urllib.error
import urllib.parse

# Normally agent_retail.py loads .env before calling into this module (it
# imports connectors_retail, then loads .env, then only later actually
# calls get_orders()/get_inventory() — so by the time those run,
# os.environ is already populated). But running this file directly
# (`python3 connectors_retail.py`, e.g. for a standalone connectivity
# check) skips agent_retail.py entirely, so this module needs to be able
# to load .env itself too. setdefault() means this never overrides a
# variable already set some other way (e.g. by agent_retail.py, or by the
# shell), so it's safe to also run in the normal import path.
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

DEFAULT_API_VERSION = "2025-01"  # bump if Shopify has retired this — see module docstring

# In-memory only, per the module docstring's note above — re-fetched
# automatically on the next call after a restart or once it's stale.
_token_cache = {"token": None, "expires_at": 0}


def _get_access_token(store):
    """
    Client credentials grant: exchange SHOPIFY_CLIENT_ID/SECRET for a
    short-lived Admin API access token. Cached in memory and refreshed a
    minute before it actually expires, so callers never see the exchange
    happen unless the cache is cold or stale.
    """
    if _token_cache["token"] and time.time() < _token_cache["expires_at"] - 60:
        return _token_cache["token"]

    client_id = os.environ.get("SHOPIFY_CLIENT_ID", "").strip()
    client_secret = os.environ.get("SHOPIFY_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise RuntimeError(
            "SHOPIFY_CLIENT_ID and SHOPIFY_CLIENT_SECRET are not both set in .env. "
            "Create an app at dev.shopify.com/dashboard, set Admin API scopes "
            "(read_orders, read_products), release it, install it on your store, then "
            "copy its Client ID and Client secret from the app's Settings tab."
        )

    url = f"https://{store}/admin/oauth/access_token"
    body = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/x-www-form-urlencoded",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Shopify token exchange failed ({e.code}): {err_body[:300]} — check "
            "SHOPIFY_CLIENT_ID/SHOPIFY_CLIENT_SECRET, and that the app is installed on "
            "this store and in the same Shopify organization as it."
        ) from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Could not reach Shopify to exchange for an access token: {e.reason}") from e

    token = data.get("access_token")
    if not token:
        raise RuntimeError(f"Shopify token exchange succeeded but returned no access_token: {data}")

    _token_cache["token"] = token
    _token_cache["expires_at"] = time.time() + int(data.get("expires_in", 86399))
    return token


def _get(path, params=None):
    store = os.environ.get("SHOPIFY_STORE_URL", "").strip()
    if not store:
        raise RuntimeError(
            "SHOPIFY_STORE_URL is not set in .env. Add a line like "
            "SHOPIFY_STORE_URL=your-store.myshopify.com pointing at your real store."
        )
    store = store.replace("https://", "").replace("http://", "").rstrip("/")
    token = _get_access_token(store)
    version = os.environ.get("SHOPIFY_API_VERSION", "").strip() or DEFAULT_API_VERSION

    url = f"https://{store}/admin/api/{version}{path}"
    if params:
        query = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
        if query:
            url = f"{url}?{query}"

    headers = {
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json",
        "User-Agent": "pilant-agent",
    }

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        hint = ""
        if e.code == 401:
            hint = " (the access token may have been revoked mid-cache — retry once; if it persists, check SHOPIFY_CLIENT_ID/SHOPIFY_CLIENT_SECRET and the app's scopes)"
        elif e.code == 404:
            hint = " (check SHOPIFY_STORE_URL and SHOPIFY_API_VERSION)"
        raise RuntimeError(f"Shopify API error {e.code} for {path}{hint}: {body[:300]}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Could not reach Shopify: {e.reason}") from e


def _order_status(raw):
    """
    Normalize Shopify's two separate status fields (fulfillment_status,
    cancelled_at) into one label the agent can filter and reason about:
    "cancelled", "unfulfilled", "partial", "fulfilled", or "restocked".
    """
    if raw.get("cancelled_at"):
        return "cancelled"
    return raw.get("fulfillment_status") or "unfulfilled"


def get_orders(status=None, customer=None):
    """
    Fetch recent orders from the connected Shopify store, optionally
    filtered by status: "unfulfilled", "partial", "fulfilled",
    "restocked", or "cancelled". Returns real order data — customer,
    item count, total, when it was placed, and whether it has a refund —
    not scoped or sorted beyond what Shopify returns by default.

    `customer`, added 2026-08-27 for the customer-360 primitive
    (primitives.py's retail_orders): restricts the result to ONE
    customer's own orders, matched against either their real email
    (case-insensitive) or their Shopify numeric customer_id (as a
    string) — whichever the caller actually has. Email is the more
    universal join key across systems (Shopify always has it; a
    customer_id is Shopify-internal and won't mean anything to, say,
    connectors_helpdesk.py), so callers building a cross-system view
    should prefer passing email when they have it.
    """
    raw = _get("/orders.json", {"status": "any", "limit": 100})
    if isinstance(raw, dict) and raw.get("errors"):
        raise RuntimeError(f"Shopify API: {raw['errors']}")

    orders = []
    for o in raw.get("orders", []):
        customer_obj = o.get("customer") or {}
        name = " ".join(filter(None, [customer_obj.get("first_name"), customer_obj.get("last_name")])).strip()
        email = o.get("email") or customer_obj.get("email")
        orders.append({
            "id": o.get("name"),  # e.g. "#1001" — Shopify's human-facing order number
            "customer": name or (email or "unknown customer"),
            "customer_id": customer_obj.get("id"),
            "email": email,
            "items": sum(li.get("quantity", 0) for li in o.get("line_items", [])),
            "total": f"{o.get('total_price')} {o.get('currency', '')}".strip(),
            "status": _order_status(o),
            "financial_status": o.get("financial_status"),
            "has_refund": bool(o.get("refunds")),
            "placed": o.get("created_at"),
            "flag": o.get("cancel_reason"),
        })

    if status:
        orders = [o for o in orders if o["status"] == status]
    if customer:
        needle = str(customer).strip().lower()
        orders = [
            o for o in orders
            if needle == str(o.get("customer_id") or "").lower()
            or needle == (o.get("email") or "").lower()
        ]
    return orders


def get_inventory(low_stock_only=False, category=None):
    """
    Fetch product variants from the connected Shopify store as inventory
    items, optionally filtered to only low/out-of-stock items and/or by
    category (Shopify's product_type field — free text set per-store, so
    match whatever categories this store actually uses, e.g. "Dresses").
    """
    try:
        threshold = int(os.environ.get("SHOPIFY_LOW_STOCK_THRESHOLD", "5"))
    except ValueError:
        threshold = 5

    raw = _get("/products.json", {"limit": 100})
    if isinstance(raw, dict) and raw.get("errors"):
        raise RuntimeError(f"Shopify API: {raw['errors']}")

    items = []
    for p in raw.get("products", []):
        product_type = p.get("product_type") or "uncategorized"
        for v in p.get("variants", []):
            stock = v.get("inventory_quantity")
            stock = stock if isinstance(stock, int) else 0
            variant_title = v.get("title")
            name = p.get("title")
            if variant_title and variant_title != "Default Title":
                name = f"{name} — {variant_title}"
            items.append({
                "sku": v.get("sku") or f"variant-{v.get('id')}",
                "name": name,
                "category": product_type,
                "stock": stock,
                "status": "out" if stock <= 0 else ("low" if stock <= threshold else "ok"),
            })

    if low_stock_only:
        items = [i for i in items if i["status"] in ("low", "out")]
    if category:
        items = [i for i in items if i["category"].lower() == category.lower()]
    return items


if __name__ == "__main__":
    # Quick manual check: python3 connectors_retail.py
    import sys
    print(f"SHOPIFY_STORE_URL = {os.environ.get('SHOPIFY_STORE_URL') or '(not set)'}", file=sys.stderr)
    print(json.dumps({"orders": get_orders(), "inventory": get_inventory()}, indent=2))
