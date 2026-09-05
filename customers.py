"""
Customer identity for the customer-facing portal (customer_site.py) —
deliberately a SEPARATE module from users.py, which handles staff identity
for server.py / retail_site.py. Staff accounts and customer accounts are
different trust domains in any real system; keeping them in separate
identity stores (rather than merging "is this a person who can log in"
into one table) is itself part of the access-control design, not just
code organization.

scope_orders() is the enforcement point: it runs in trusted Python code,
before any order data reaches the model, and returns ONLY the orders
belonging to the logged-in customer — regardless of how their request is
phrased. Same principle as users.scope_issues(), applied to a real
end-customer instead of an internal analyst/viewer role.
"""

from werkzeug.security import generate_password_hash, check_password_hash

# email -> (display name, customer_id, password). customer_id must match
# the "customer_id" field on orders in connectors_retail.ORDERS.
_CUSTOMER_SEED = {
    "lferreira@example.com": ("L. Ferreira", "cust_ferreira", "wraps123"),
    "jokafor@example.com": ("J. Okafor", "cust_okafor", "return456"),
    "mchen@example.com": ("M. Chen", "cust_chen", "trousers789"),
    "apetrova@example.com": ("A. Petrova", "cust_petrova", "linen000"),
}

CUSTOMERS = {
    email: {
        "email": email,
        "name": name,
        "customer_id": customer_id,
        "password_hash": generate_password_hash(pw),
    }
    for email, (name, customer_id, pw) in _CUSTOMER_SEED.items()
}

# A precomputed dummy hash so verify_login() can run a check_password_hash
# call even for an unknown email — unknown-email and wrong-password both
# take the same code path and return the same None, so neither the
# response nor its rough timing reveals whether an email is registered.
_DUMMY_HASH = generate_password_hash("not-a-real-password")


def verify_login(email, password):
    email = (email or "").strip().lower()
    customer = CUSTOMERS.get(email)
    if not customer:
        check_password_hash(_DUMMY_HASH, password or "")
        return None
    if not check_password_hash(customer["password_hash"], password or ""):
        return None
    return customer


def get_customer(customer_id):
    for c in CUSTOMERS.values():
        if c["customer_id"] == customer_id:
            return c
    raise RuntimeError(f"Unknown customer '{customer_id}'")


def scope_orders(orders, customer):
    """
    The trust boundary. Filters raw order data down to ONLY this
    customer's own orders, in plain Python, before it is ever handed to
    the model — so no phrasing of a request can surface another
    customer's order.
    """
    return [o for o in orders if o.get("customer_id") == customer["customer_id"]]
