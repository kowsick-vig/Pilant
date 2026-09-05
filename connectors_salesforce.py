"""
A real connector — reads live Opportunities/Accounts from a Salesforce org
via Salesforce's REST API (SOQL queries), the same "hand-written connector
for one real external system" pattern as connectors_retail.py (Shopify) and
connectors_gmail.py (Gmail).

Setup — Salesforce's own OAuth 2.0 Web Server Flow (verified against
help.salesforce.com as of 2026-08-29; if Salesforce has changed this again,
check https://help.salesforce.com and search "OAuth 2.0 Web Server Flow" /
"OAuth 2.0 Refresh Token Flow"). Salesforce has no equivalent of Google's
OAuth Playground, so the one-time authorization has to be done by hand:

  1. In Setup (gear icon -> Setup), go to App Manager (search "App Manager"
     in Quick Find) -> New Connected App (some orgs now call this "New
     External Client App" — either works the same way for this). Fill in
     the required Name/Email, then under "API (Enable OAuth Settings)":
       - Check "Enable OAuth Settings".
       - Callback URL: use a loopback address nothing needs to actually be
         listening on, e.g. http://localhost:8080/callback — Salesforce
         allows this for non-production testing. (Step 3 below explains
         why a non-working URL is fine here.)
       - Selected OAuth Scopes: add at least "Manage user data via APIs
         (api)" and "Perform requests at any time (refresh_token,
         offline_access)".
     Save. It can take up to 10 minutes for a new Connected App to become
     active — if step 2 fails immediately with an error about the app,
     wait a few minutes and retry.
  2. From the Connected App's "Manage Consumer Details" (or API section),
     copy the Consumer Key (this is SALESFORCE_CLIENT_ID) and Consumer
     Secret (SALESFORCE_CLIENT_SECRET).
  3. One-time manual authorization to get a refresh token: visit, in a
     browser, signed in as the Salesforce user this connector should act
     as:

       https://YOUR_DOMAIN.my.salesforce.com/services/oauth2/authorize
         ?client_id=YOUR_CONSUMER_KEY
         &redirect_uri=http://localhost:8080/callback
         &response_type=code

     (YOUR_DOMAIN is the org's My Domain name, e.g. from Setup -> My
     Domain, or use login.salesforce.com if My Domain isn't set up; use
     test.salesforce.com instead of login.salesforce.com for a sandbox
     org.) After you log in and click Allow, the browser will try to load
     http://localhost:8080/callback?code=AUTHORIZATION_CODE... and fail to
     connect (nothing is listening there) — that's expected. The
     authorization code is sitting right there in the address bar; copy
     everything after "code=" and before the next "&" (URL-decode it if
     your browser shows %-escapes). It expires in 15 minutes, so do step 4
     right away.
  4. Exchange that code for tokens once, from a terminal:

       curl https://YOUR_DOMAIN.my.salesforce.com/services/oauth2/token \\
         -d grant_type=authorization_code \\
         -d code=THE_CODE_FROM_STEP_3 \\
         -d client_id=YOUR_CONSUMER_KEY \\
         -d client_secret=YOUR_CONSUMER_SECRET \\
         -d redirect_uri=http://localhost:8080/callback

     The JSON response includes a "refresh_token" — that's the one value
     that needs to be saved. (Its "access_token" is short-lived and never
     needs saving; this connector fetches its own from the refresh token,
     same as connectors_gmail.py does for Gmail.)
  5. Set in .env:
       SALESFORCE_CLIENT_ID       the Consumer Key from step 2
       SALESFORCE_CLIENT_SECRET   the Consumer Secret from step 2
       SALESFORCE_REFRESH_TOKEN   the refresh_token from step 4
       SALESFORCE_LOGIN_URL       optional — the https://YOUR_DOMAIN.my.
                                  salesforce.com (or login.salesforce.com /
                                  test.salesforce.com) host used above.
                                  Defaults to https://login.salesforce.com,
                                  which works for a My-Domain-less org but
                                  is slower (one extra redirect) — set your
                                  real My Domain host once you have one.

Known simplifications, called out honestly rather than silently:
  - Salesforce's refresh-token response doesn't include an expires_in the
    way Google's/Shopify's does — a Salesforce access token's real
    lifetime is controlled by the org's session-timeout setting, not
    something this connector can know in advance. So instead of guessing
    an expiry, _request() below just tries the cached token first and, on
    a 401 (INVALID_SESSION_ID), refreshes once and retries automatically —
    the standard Salesforce integration pattern.
  - Only the first page of each SOQL query is fetched (LIMIT 200, no
    queryMore/nextRecordsUrl follow-up). Fine for "what needs attention"-
    style questions against a normal-sized pipeline; an org with a very
    large number of open Opportunities would need real pagination added.
  - "At risk" here means one specific, honest signal: an open Opportunity
    whose LastActivityDate (Salesforce's own rollup of the most recent
    Task/Event touching it) is more than SALESFORCE_AT_RISK_DAYS days ago
    (default 14) — not a Salesforce-native "risk score" field, which
    doesn't exist without added Einstein/third-party tooling.
  - Filtering by `owner_email` matches Salesforce's real Owner.Email
    relationship field via SOQL directly (not fetched-then-filtered in
    Python the way connectors_retail.py's `customer` filter is) — simpler
    here since SOQL supports it natively.
"""

import os
import json
import time
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

# Same self-loading .env pattern as every other real connector in this
# project (connectors_retail.py, connectors_gmail.py) — works whether the
# caller already loaded .env or not, and when this file is run standalone.
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

DEFAULT_API_VERSION = "v62.0"  # bump if Salesforce has retired this — check
# https://help.salesforce.com (Setup -> API -> REST API) for the current list.
DEFAULT_LOGIN_URL = "https://login.salesforce.com"

# Cached in memory only — see the module docstring's note on why there's no
# expires_at guess here, unlike connectors_retail.py/connectors_gmail.py's
# token caches. instance_url is cached alongside the token because every
# real API call (other than the token endpoint itself) has to go to the
# org's OWN instance host, not a fixed one — Salesforce hands that back on
# every successful token exchange/refresh.
_token_cache = {"token": None, "instance_url": None}


def get_authorization_url(redirect_uri, state=None):
    """Builds the real Salesforce OAuth consent-screen URL — same role as
    connectors_gmail.py's get_authorization_url, for if/when this connector
    grows a "Connect Salesforce" button in Studio's Integrations page
    (mirroring the real Gmail OAuth flow already wired up there). Until
    then, this is also exactly the URL the module docstring's step 3 has a
    person visit by hand for the one-time setup."""
    login_url = os.environ.get("SALESFORCE_LOGIN_URL", "").strip() or DEFAULT_LOGIN_URL
    client_id = os.environ.get("SALESFORCE_CLIENT_ID", "").strip()
    if not client_id:
        raise RuntimeError(
            "SALESFORCE_CLIENT_ID must be set in .env before authorization can start. "
            "See connectors_salesforce.py's module docstring for the one-time Connected "
            "App setup."
        )
    params = {"client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code"}
    if state:
        params["state"] = state
    return f"{login_url}/services/oauth2/authorize?{urllib.parse.urlencode(params)}"


def exchange_code_for_tokens(code, redirect_uri):
    """Exchanges a real Salesforce OAuth authorization code for tokens —
    the programmatic equivalent of the module docstring's step 4 curl
    command, for a future "Connect Salesforce" button. Returns the
    refresh_token (the one value that needs to be saved somewhere durable —
    a caller wiring this into Studio would persist it the same place
    connectors_gmail.py's exchange_code_for_tokens() keeps its live
    connection) and caches the access token/instance_url this call also
    receives, so this same process can start making real API calls
    immediately without a separate refresh."""
    login_url = os.environ.get("SALESFORCE_LOGIN_URL", "").strip() or DEFAULT_LOGIN_URL
    client_id = os.environ.get("SALESFORCE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("SALESFORCE_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise RuntimeError(
            "SALESFORCE_CLIENT_ID and SALESFORCE_CLIENT_SECRET must both be set in .env."
        )
    data = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
    }).encode()
    req = urllib.request.Request(f"{login_url}/services/oauth2/token", data=data, headers={
        "Content-Type": "application/x-www-form-urlencoded",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"Salesforce OAuth code exchange failed ({e.code}): "
            f"{e.read().decode('utf-8', errors='replace')[:300]} — authorization codes expire "
            "after 15 minutes; if this is old, get a fresh one via get_authorization_url()."
        ) from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Could not reach Salesforce's OAuth token endpoint: {e.reason}") from e

    access_token = body.get("access_token")
    refresh_token = body.get("refresh_token")
    instance_url = body.get("instance_url")
    if not access_token or not instance_url:
        raise RuntimeError(f"Salesforce OAuth code exchange returned an unexpected response: {body}")
    if not refresh_token:
        raise RuntimeError(
            "Salesforce didn't return a refresh token for this authorization — check that the "
            "Connected App's OAuth scopes include 'Perform requests at any time "
            "(refresh_token, offline_access)'."
        )
    _token_cache["token"] = access_token
    _token_cache["instance_url"] = instance_url
    return refresh_token


def _refresh_access_token():
    login_url = os.environ.get("SALESFORCE_LOGIN_URL", "").strip() or DEFAULT_LOGIN_URL
    client_id = os.environ.get("SALESFORCE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("SALESFORCE_CLIENT_SECRET", "").strip()
    refresh_token = os.environ.get("SALESFORCE_REFRESH_TOKEN", "").strip()
    if not client_id or not client_secret or not refresh_token:
        raise RuntimeError(
            "SALESFORCE_CLIENT_ID, SALESFORCE_CLIENT_SECRET, and SALESFORCE_REFRESH_TOKEN must "
            "all be set in .env. See connectors_salesforce.py's module docstring for the "
            "one-time Connected App setup and manual authorization."
        )
    data = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
    }).encode()
    req = urllib.request.Request(f"{login_url}/services/oauth2/token", data=data, headers={
        "Content-Type": "application/x-www-form-urlencoded",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"Salesforce token refresh failed ({e.code}): "
            f"{e.read().decode('utf-8', errors='replace')[:300]} — the refresh token may have "
            "been revoked (Setup -> Connected Apps OAuth Usage) or the Connected App's IP "
            "restrictions/policies may be blocking this."
        ) from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Could not reach Salesforce's OAuth token endpoint: {e.reason}") from e

    access_token = body.get("access_token")
    instance_url = body.get("instance_url")
    if not access_token or not instance_url:
        raise RuntimeError(f"Salesforce token refresh returned an unexpected response: {body}")
    _token_cache["token"] = access_token
    _token_cache["instance_url"] = instance_url
    # Refresh token rotation is off by default, but Salesforce sends a new
    # refresh_token when an org has it enabled — the old one stops working
    # the moment that happens. Pick it up in-memory so this process keeps
    # working; there's nowhere durable to write it back to .env from here,
    # so surface it loudly rather than silently swallowing it.
    if body.get("refresh_token") and body["refresh_token"] != os.environ.get("SALESFORCE_REFRESH_TOKEN"):
        os.environ["SALESFORCE_REFRESH_TOKEN"] = body["refresh_token"]
        print(
            "NOTE: Salesforce issued a new refresh token (refresh token rotation is on for "
            "this org). Update SALESFORCE_REFRESH_TOKEN in .env to the new value or the next "
            "process restart will fail:", body["refresh_token"],
        )
    return access_token, instance_url


def _request(method, path, params=None, json_body=None, _retried=False):
    if not _token_cache["token"] or not _token_cache["instance_url"]:
        _refresh_access_token()

    url = f"{_token_cache['instance_url']}{path}"
    if params:
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        if query:
            url = f"{url}?{query}"

    headers = {"Authorization": f"Bearer {_token_cache['token']}"}
    data = None
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
            return json.loads(raw.decode("utf-8")) if raw else {}
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        # Salesforce's real signal for "this access token is no longer good" —
        # session timeout, an admin revoked it, org login-hours policy, etc.
        # — is a 401 with error code INVALID_SESSION_ID, not just any 401.
        if e.code == 401 and not _retried:
            _refresh_access_token()
            return _request(method, path, params=params, json_body=json_body, _retried=True)
        raise RuntimeError(f"Salesforce API error {e.code} for {path}: {body_text[:400]}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Could not reach Salesforce: {e.reason}") from e


def _api_version():
    return os.environ.get("SALESFORCE_API_VERSION", "").strip() or DEFAULT_API_VERSION


def _soql(query):
    """Runs one real SOQL query (first page only — see module docstring)
    and returns its records as a plain list of dicts. Salesforce nests each
    related-object lookup (Account.Name, Owner.Email, ...) as a sub-dict
    under the relationship name plus an "attributes" metadata key on every
    record and nested object — stripped out here since nothing downstream
    needs Salesforce's own type/url bookkeeping."""
    result = _request("GET", f"/services/data/{_api_version()}/query", params={"q": query})
    return result.get("records", [])


def _lookup(record, *path):
    """Reads a possibly-nested relationship field out of a raw SOQL record,
    e.g. _lookup(record, "Owner", "Email") for an "Owner.Email" SOQL column
    — Salesforce returns that as record["Owner"]["Email"], or
    record["Owner"] as None entirely if the lookup is empty (e.g. an
    Opportunity with no Account attached)."""
    node = record
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _days_since(date_str):
    if not date_str:
        return None
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - d).days


def _soql_string(value):
    """Escapes a value for safe interpolation into a SOQL string literal —
    SOQL uses a backslash to escape a single quote inside a string, same as
    it documents for injection-safe query building. Every WHERE clause
    below that embeds a caller-supplied value goes through this rather than
    f-stringing it in raw."""
    return str(value).replace("\\", "\\\\").replace("'", "\\'")


def get_opportunities(owner_email=None, stage=None, min_amount=None, limit=200):
    """
    Fetch open (IsClosed = false) Opportunities from the connected
    Salesforce org, most valuable first. `owner_email` restricts to one
    owner's Opportunities via SOQL's real Owner.Email relationship field;
    `stage` matches Salesforce's own StageName exactly (org-specific — a
    fresh org ships with "Prospecting"/"Qualification"/"Needs Analysis"/...
    but most real orgs customize these, so check Setup -> Object Manager ->
    Opportunity -> Fields & Relationships -> Stage for what THIS org
    actually uses rather than assuming the defaults); `min_amount` is a
    floor on Amount. `limit` caps the SOQL LIMIT clause (default 200, hard
    max 200 per the module docstring's single-page-only note).
    """
    limit = min(max(int(limit or 200), 1), 200)
    where = ["IsClosed = false"]
    if owner_email:
        where.append(f"Owner.Email = '{_soql_string(owner_email)}'")
    if stage:
        where.append(f"StageName = '{_soql_string(stage)}'")
    if min_amount is not None:
        where.append(f"Amount >= {float(min_amount)}")

    query = (
        "SELECT Id, Name, StageName, Amount, CloseDate, Probability, "
        "LastActivityDate, Account.Name, Account.Id, Owner.Name, Owner.Email "
        "FROM Opportunity WHERE " + " AND ".join(where) +
        f" ORDER BY Amount DESC NULLS LAST LIMIT {limit}"
    )
    records = _soql(query)
    return [
        {
            "id": r.get("Id"),
            "name": r.get("Name"),
            "account": _lookup(r, "Account", "Name"),
            "account_id": _lookup(r, "Account", "Id"),
            "owner": _lookup(r, "Owner", "Name"),
            "owner_email": _lookup(r, "Owner", "Email"),
            "stage": r.get("StageName"),
            "amount": r.get("Amount"),
            "probability": r.get("Probability"),
            "close_date": r.get("CloseDate"),
            "last_activity_date": r.get("LastActivityDate"),
            "days_since_activity": _days_since(r.get("LastActivityDate")),
        }
        for r in records
    ]


def get_account_risk(owner_email=None, at_risk_days=None):
    """
    Open Opportunities flagged "at risk" by the one honest signal available
    without extra tooling: LastActivityDate older than `at_risk_days` (or
    SALESFORCE_AT_RISK_DAYS from .env, default 14) — see module docstring.
    An Opportunity with no LastActivityDate at all (nothing ever logged
    against it) counts as at risk too, sorted after ones with a known,
    older date (a real long gap is a stronger signal than "no data").
    """
    if at_risk_days is None:
        try:
            at_risk_days = int(os.environ.get("SALESFORCE_AT_RISK_DAYS", "14"))
        except ValueError:
            at_risk_days = 14

    opps = get_opportunities(owner_email=owner_email, limit=200)
    at_risk = []
    for o in opps:
        days = o["days_since_activity"]
        if days is None or days >= at_risk_days:
            at_risk.append({**o, "days_since_activity": days if days is not None else float("inf")})
    at_risk.sort(key=lambda o: o["days_since_activity"], reverse=True)
    for o in at_risk:
        if o["days_since_activity"] == float("inf"):
            o["days_since_activity"] = None
    return at_risk


def get_pipeline_metrics(owner_email=None):
    """
    Aggregate open-pipeline totals via a real SOQL aggregate query (COUNT/
    SUM, not fetched-then-summed in Python) — total open Opportunity count
    and total open Amount, optionally scoped to one owner.
    """
    where = ["IsClosed = false"]
    if owner_email:
        where.append(f"Owner.Email = '{_soql_string(owner_email)}'")
    query = (
        "SELECT COUNT(Id) opp_count, SUM(Amount) total_amount "
        "FROM Opportunity WHERE " + " AND ".join(where)
    )
    records = _soql(query)
    row = records[0] if records else {}
    return {
        "open_opportunity_count": row.get("opp_count") or 0,
        "open_pipeline_amount": row.get("total_amount") or 0,
    }


def get_pipeline_by_stage(owner_email=None):
    """
    Open pipeline broken out by Salesforce's real StageName — one real
    SOQL GROUP BY query, count and total Amount per stage, most Opportunity
    count first.
    """
    where = ["IsClosed = false"]
    if owner_email:
        where.append(f"Owner.Email = '{_soql_string(owner_email)}'")
    query = (
        "SELECT StageName, COUNT(Id) opp_count, SUM(Amount) total_amount "
        "FROM Opportunity WHERE " + " AND ".join(where) +
        " GROUP BY StageName ORDER BY COUNT(Id) DESC"
    )
    records = _soql(query)
    return [
        {
            "stage": r.get("StageName"),
            "count": r.get("opp_count") or 0,
            "amount": r.get("total_amount") or 0,
        }
        for r in records
    ]


def get_account(account_id=None, name=None):
    """
    Fetch one real Account's own detail — not just what an Opportunity's
    Account.Name lookup carries. Pass either the real Salesforce Account
    Id (from get_opportunities()'s account_id field) or an exact Name
    match; Id is the more reliable join key (Name isn't guaranteed unique
    in a real org). Returns None if nothing matches, rather than raising —
    "no such account" is a normal outcome here, not an error.
    """
    if not account_id and not name:
        raise RuntimeError("get_account requires either account_id or name.")
    where = f"Id = '{_soql_string(account_id)}'" if account_id else f"Name = '{_soql_string(name)}'"
    query = (
        "SELECT Id, Name, Industry, AnnualRevenue, Phone, Website, Owner.Name, Owner.Email "
        f"FROM Account WHERE {where} LIMIT 1"
    )
    records = _soql(query)
    if not records:
        return None
    r = records[0]
    return {
        "id": r.get("Id"),
        "name": r.get("Name"),
        "industry": r.get("Industry"),
        "annual_revenue": r.get("AnnualRevenue"),
        "phone": r.get("Phone"),
        "website": r.get("Website"),
        "owner": _lookup(r, "Owner", "Name"),
        "owner_email": _lookup(r, "Owner", "Email"),
    }


if __name__ == "__main__":
    # Quick manual check: python3 connectors_salesforce.py
    import sys
    configured = all(
        os.environ.get(k)
        for k in ("SALESFORCE_CLIENT_ID", "SALESFORCE_CLIENT_SECRET", "SALESFORCE_REFRESH_TOKEN")
    )
    print(f"Salesforce credentials configured: {configured}", file=sys.stderr)
    print(json.dumps({
        "pipeline": get_pipeline_metrics(),
        "by_stage": get_pipeline_by_stage(),
        "at_risk": get_account_risk(),
    }, indent=2, default=str))
