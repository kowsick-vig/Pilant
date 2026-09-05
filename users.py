"""
A mock identity directory — stands in for a real login/SSO system. Each
user has a label_scope: the set of issue labels they're authorized to see.
None means unrestricted (sees everything the connector returns).

The point of this file isn't to be a real auth system — it's to prove the
shape of the actual guarantee Pilant needs: "the software knows who's
asking, and what reaches the model is really restricted to that identity,"
not just personalized wording layered on top of the same unrestricted data.

Enforcement happens in scope_issues(), which runs in trusted code before
any data reaches the model. The model never sees unauthorized issues in
the first place — access isn't something a cleverly-phrased request could
talk it around, because it was never in its context to begin with.

Passwords below are demo-only plaintext, hashed at import time with
Werkzeug's password hashing (the same library Flask itself depends on —
no new requirement). These are mock accounts for a local prototype, not
real credentials; there's no user-facing signup, so there's nothing
sensitive being protected here yet.
"""

from werkzeug.security import generate_password_hash, check_password_hash

# username -> (display name, role, label_scope, plaintext demo password)
_USER_SEED = {
    "kowsick": ("Kowsick", "owner", None, "owner123"),
    "analyst": ("Jordan (analyst)", "analyst", ["bug", "critical", "security"], "analyst123"),
    "viewer": ("Sam (viewer)", "viewer", ["enhancement", "documentation"], "viewer123"),
}

USERS = {
    username: {
        "name": name,
        "role": role,
        "label_scope": label_scope,
        "password_hash": generate_password_hash(password),
    }
    for username, (name, role, label_scope, password) in _USER_SEED.items()
}

DEFAULT_USER = "kowsick"


def get_user(username):
    user = USERS.get(username)
    if user is None:
        raise RuntimeError(f"Unknown user '{username}'. Known users: {', '.join(USERS)}")
    return {"username": username, **user}


def verify_login(username, password):
    """
    Check a plaintext password against the stored hash. Returns the user
    dict (same shape as get_user()) on success, or None on any failure —
    unknown username and wrong password look identical to the caller, so
    a login form can't be used to enumerate which usernames exist.
    """
    user = USERS.get(username)
    if user is None:
        return None
    if not check_password_hash(user["password_hash"], password):
        return None
    return {"username": username, **user}


def scope_issues(issues, user):
    """
    Enforce user['label_scope'] on an already-fetched list of issues.
    scope=None means unrestricted. Otherwise, an issue is visible only if
    it has at least one label in the user's scope.
    """
    scope = user.get("label_scope")
    if scope is None:
        return issues
    allowed = set(scope)
    return [i for i in issues if allowed.intersection(i.get("labels", []))]
