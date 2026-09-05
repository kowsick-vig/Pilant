"""
Regression coverage for the 2026-08-29 real Salesforce and Jira connectors
(connectors_salesforce.py, connectors_jira.py) — built the same "real API,
no fabricated data" way as connectors_retail.py/connectors_gmail.py, and
tested the same way: urllib.request.urlopen mocked with fake but
realistically-shaped API responses, no live org/site required to run this.

Covers: missing-credential errors, the Salesforce refresh-token flow
(including the 401-triggers-one-retry pattern, since Salesforce doesn't
hand back an expires_in), SOQL query construction/escaping, Jira's Basic
Auth header, and the /search/jql + /search/approximate-count request
shapes (the endpoints that replaced Jira's now-removed /rest/api/3/search).

Run directly: python3 tests/test_connectors_salesforce_jira.py
"""
import sys
import os
import json
import io
import urllib.error
from unittest.mock import patch

sys.path.insert(0, "/home/claude/pilant-agent")


class _FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _http_error(code, payload):
    return urllib.error.HTTPError(
        url="https://fake", code=code, msg="err",
        hdrs=None, fp=io.BytesIO(json.dumps(payload).encode("utf-8")),
    )


# ============================= Salesforce ==================================

def _clear_sf_env():
    for k in list(os.environ):
        if k.startswith("SALESFORCE_"):
            os.environ.pop(k, None)


def test_salesforce_missing_credentials_raise_clear_errors():
    _clear_sf_env()
    import connectors_salesforce as sf
    import importlib
    importlib.reload(sf)
    sf._token_cache["token"] = None
    sf._token_cache["instance_url"] = None
    try:
        sf.get_pipeline_metrics()
        raise AssertionError("should have raised")
    except RuntimeError as e:
        assert "SALESFORCE_CLIENT_ID" in str(e)


def test_salesforce_refresh_and_soql_query_parse():
    _clear_sf_env()
    os.environ["SALESFORCE_CLIENT_ID"] = "fake_id"
    os.environ["SALESFORCE_CLIENT_SECRET"] = "fake_secret"
    os.environ["SALESFORCE_REFRESH_TOKEN"] = "fake_refresh"
    import connectors_salesforce as sf
    import importlib
    importlib.reload(sf)
    sf._token_cache["token"] = None
    sf._token_cache["instance_url"] = None

    calls = []

    def fake_urlopen(req, timeout=None):
        url = req.full_url
        calls.append(url)
        if "/services/oauth2/token" in url:
            return _FakeResponse({
                "access_token": "00Dfaketoken",
                "instance_url": "https://fake-instance.my.salesforce.com",
                "token_type": "Bearer",
            })
        if "/query" in url:
            assert "Owner.Email" not in url or "kowsick%40acme.com" in url or "kowsick@acme.com" in url
            return _FakeResponse({
                "records": [
                    {
                        "Id": "006A1",
                        "Name": "Acme renewal",
                        "StageName": "Negotiation",
                        "Amount": 50000,
                        "Probability": 60,
                        "CloseDate": "2026-09-15",
                        "LastActivityDate": "2026-08-08",  # 21 days before 2026-08-29
                        "Account": {"Name": "Acme Corp", "Id": "001A1"},
                        "Owner": {"Name": "Kowsick", "Email": "kowsick@acme.com"},
                    },
                ]
            })
        raise AssertionError(f"unexpected URL: {url}")

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        opps = sf.get_opportunities(owner_email="kowsick@acme.com")

    assert len(opps) == 1
    o = opps[0]
    assert o["account"] == "Acme Corp"
    assert o["owner_email"] == "kowsick@acme.com"
    assert o["amount"] == 50000
    assert o["days_since_activity"] is not None and o["days_since_activity"] >= 20
    # token + instance_url were cached from the refresh call, not re-fetched
    assert sf._token_cache["token"] == "00Dfaketoken"
    token_calls = [c for c in calls if "/oauth2/token" in c]
    assert len(token_calls) == 1, "should only refresh once, then reuse the cached token"


def test_salesforce_401_triggers_one_refresh_and_retry():
    _clear_sf_env()
    os.environ["SALESFORCE_CLIENT_ID"] = "fake_id"
    os.environ["SALESFORCE_CLIENT_SECRET"] = "fake_secret"
    os.environ["SALESFORCE_REFRESH_TOKEN"] = "fake_refresh"
    import connectors_salesforce as sf
    import importlib
    importlib.reload(sf)
    # Pretend a stale token is already cached (e.g. session expired since last call).
    sf._token_cache["token"] = "stale_token"
    sf._token_cache["instance_url"] = "https://fake-instance.my.salesforce.com"

    state = {"query_attempts": 0}

    def fake_urlopen(req, timeout=None):
        url = req.full_url
        if "/services/oauth2/token" in url:
            return _FakeResponse({
                "access_token": "fresh_token",
                "instance_url": "https://fake-instance.my.salesforce.com",
            })
        if "/query" in url:
            state["query_attempts"] += 1
            if state["query_attempts"] == 1:
                raise _http_error(401, [{"errorCode": "INVALID_SESSION_ID", "message": "expired"}])
            return _FakeResponse({"records": [{"opp_count": 3, "total_amount": 150000}]})
        raise AssertionError(f"unexpected URL: {url}")

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        metrics = sf.get_pipeline_metrics()

    assert metrics == {"open_opportunity_count": 3, "open_pipeline_amount": 150000}
    assert state["query_attempts"] == 2, "first 401 should trigger exactly one retry after refresh"
    assert sf._token_cache["token"] == "fresh_token"


def test_salesforce_soql_string_escaping():
    import connectors_salesforce as sf
    assert sf._soql_string("O'Brien") == "O\\'Brien"
    assert sf._soql_string("back\\slash") == "back\\\\slash"


def test_salesforce_account_risk_sorts_oldest_activity_first():
    _clear_sf_env()
    os.environ["SALESFORCE_CLIENT_ID"] = "fake_id"
    os.environ["SALESFORCE_CLIENT_SECRET"] = "fake_secret"
    os.environ["SALESFORCE_REFRESH_TOKEN"] = "fake_refresh"
    import connectors_salesforce as sf
    import importlib
    importlib.reload(sf)
    sf._token_cache["token"] = None
    sf._token_cache["instance_url"] = None

    def fake_urlopen(req, timeout=None):
        url = req.full_url
        if "/services/oauth2/token" in url:
            return _FakeResponse({"access_token": "t", "instance_url": "https://fake.my.salesforce.com"})
        if "/query" in url:
            return _FakeResponse({"records": [
                {"Id": "1", "Name": "Fresh deal", "StageName": "Prospecting", "Amount": 1000,
                 "LastActivityDate": "2026-08-27", "Account": {"Name": "FreshCo"}, "Owner": {"Name": "K"}},
                {"Id": "2", "Name": "Stale deal", "StageName": "Prospecting", "Amount": 2000,
                 "LastActivityDate": "2026-07-01", "Account": {"Name": "StaleCo"}, "Owner": {"Name": "K"}},
                {"Id": "3", "Name": "No activity ever", "StageName": "Prospecting", "Amount": 3000,
                 "LastActivityDate": None, "Account": {"Name": "GhostCo"}, "Owner": {"Name": "K"}},
            ]})
        raise AssertionError(url)

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        at_risk = sf.get_account_risk(at_risk_days=14)

    names = [o["account"] for o in at_risk]
    assert "FreshCo" not in names  # only 2 days stale, under the 14-day bar
    assert names[0] == "GhostCo"  # no activity at all sorts as the worst case
    assert names[1] == "StaleCo"


# ================================ Jira ======================================

def _clear_jira_env():
    for k in list(os.environ):
        if k.startswith("JIRA_"):
            os.environ.pop(k, None)


def test_jira_missing_credentials_raise_clear_error():
    _clear_jira_env()
    import connectors_jira as jira
    import importlib
    importlib.reload(jira)
    try:
        jira.get_issues()
        raise AssertionError("should have raised")
    except RuntimeError as e:
        assert "JIRA_SITE_URL" in str(e)


def test_jira_basic_auth_header_and_search_jql_shape():
    _clear_jira_env()
    os.environ["JIRA_SITE_URL"] = "https://fake.atlassian.net"
    os.environ["JIRA_EMAIL"] = "kowsick@acme.com"
    os.environ["JIRA_API_TOKEN"] = "fake_token"
    import connectors_jira as jira
    import importlib
    importlib.reload(jira)

    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["auth"] = req.headers.get("Authorization")
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _FakeResponse({
            "issues": [
                {
                    "key": "ACME-118", "id": "10001",
                    "fields": {
                        "summary": "Sync failing intermittently",
                        "status": {"name": "In Progress", "statusCategory": {"key": "indeterminate"}},
                        "assignee": {"displayName": "Kowsick", "emailAddress": "kowsick@acme.com"},
                        "reporter": {"displayName": "Alex"},
                        "project": {"key": "ACME", "name": "Acme Corp"},
                        "priority": {"name": "High"},
                        "issuetype": {"name": "Bug"},
                        "duedate": "2026-08-20",
                        "created": "2026-08-01T09:00:00.000+0000",
                        "updated": "2026-08-15T09:00:00.000+0000",
                    },
                },
            ]
        })

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        issues = jira.get_issues(project="ACME", assignee_email="kowsick@acme.com")

    assert captured["url"].endswith("/rest/api/3/search/jql")
    assert captured["auth"].startswith("Basic ")
    assert 'project = "ACME"' in captured["body"]["jql"]
    assert 'assignee = "kowsick@acme.com"' in captured["body"]["jql"]
    assert captured["body"]["fields"] == jira._ISSUE_FIELDS

    assert len(issues) == 1
    issue = issues[0]
    assert issue["key"] == "ACME-118"
    assert issue["status_category"] == "indeterminate"
    assert issue["is_done"] is False
    assert issue["overdue"] is True  # duedate 2026-08-20 is before "today" (2026-08-29) and not done
    assert issue["days_stale"] is not None and issue["days_stale"] >= 13


def test_jira_open_issue_count_uses_approximate_count_endpoint():
    _clear_jira_env()
    os.environ["JIRA_SITE_URL"] = "https://fake.atlassian.net"
    os.environ["JIRA_EMAIL"] = "kowsick@acme.com"
    os.environ["JIRA_API_TOKEN"] = "fake_token"
    import connectors_jira as jira
    import importlib
    importlib.reload(jira)

    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _FakeResponse({"count": 7})

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        count = jira.get_open_issue_count(project="ACME")

    assert count == 7
    assert captured["url"].endswith("/rest/api/3/search/approximate-count")
    assert 'statusCategory != "Done"' in captured["body"]["jql"]
    assert 'project = "ACME"' in captured["body"]["jql"]


def test_jira_401_error_includes_hint():
    _clear_jira_env()
    os.environ["JIRA_SITE_URL"] = "https://fake.atlassian.net"
    os.environ["JIRA_EMAIL"] = "kowsick@acme.com"
    os.environ["JIRA_API_TOKEN"] = "wrong_token"
    import connectors_jira as jira
    import importlib
    importlib.reload(jira)

    def fake_urlopen(req, timeout=None):
        raise _http_error(401, {"errorMessages": ["Unauthorized"]})

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        try:
            jira.get_issues()
            raise AssertionError("should have raised")
        except RuntimeError as e:
            assert "JIRA_EMAIL" in str(e) or "JIRA_API_TOKEN" in str(e)


def test_jira_stale_and_overdue_jql_clauses():
    _clear_jira_env()
    os.environ["JIRA_SITE_URL"] = "https://fake.atlassian.net"
    os.environ["JIRA_EMAIL"] = "kowsick@acme.com"
    os.environ["JIRA_API_TOKEN"] = "fake_token"
    import connectors_jira as jira
    import importlib
    importlib.reload(jira)

    captured = []

    def fake_urlopen(req, timeout=None):
        captured.append(json.loads(req.data.decode("utf-8"))["jql"])
        return _FakeResponse({"issues": []})

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        jira.get_overdue_issues(project="ACME")
        jira.get_stale_issues(project="ACME", min_days=10)

    assert "duedate < now()" in captured[0] and "duedate is not EMPTY" in captured[0]
    assert "updated <= -10d" in captured[1]


if __name__ == "__main__":
    test_salesforce_missing_credentials_raise_clear_errors()
    test_salesforce_refresh_and_soql_query_parse()
    test_salesforce_401_triggers_one_refresh_and_retry()
    test_salesforce_soql_string_escaping()
    test_salesforce_account_risk_sorts_oldest_activity_first()
    test_jira_missing_credentials_raise_clear_error()
    test_jira_basic_auth_header_and_search_jql_shape()
    test_jira_open_issue_count_uses_approximate_count_endpoint()
    test_jira_401_error_includes_hint()
    test_jira_stale_and_overdue_jql_clauses()
    print("all salesforce/jira connector tests passed")
