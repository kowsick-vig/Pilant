"""
connectors_jira_mock.py — added 2026-08-29, sibling to
connectors_salesforce_mock.py (see that file's module docstring for the
full reasoning on why this is a static, clearly-labeled adapter rather
than a real API integration). Same field shapes a real `jira` Python
client would return (issue key, project, status, priority, customer,
unresolved age) so this can be swapped for a real connector later without
touching workspace_engine.py's calling code.

`customer` on each issue is what lets a single account (Acme Corp) show
up simultaneously in Gmail, Salesforce, and here — the actual
cross-application composition the brief's "Acme renewal at risk" example
demonstrates.
"""

ISSUES = [
    {
        "id": "ACME-118", "project": "ACME", "customer": "Acme Corp",
        "summary": "SSO login intermittently fails for enterprise-tier users",
        "status": "open", "priority": "high", "reporter": "it@acmecorp.com",
        "created": "2026-08-10", "unresolved_days": 19,
    },
    {
        "id": "ACME-121", "project": "ACME", "customer": "Acme Corp",
        "summary": "Bulk export job times out for exports over 50k rows",
        "status": "in_progress", "priority": "medium", "reporter": "ops@acmecorp.com",
        "created": "2026-08-15", "unresolved_days": 14,
    },
    {
        "id": "GLBX-44", "project": "GLBX", "customer": "Globex Inc",
        "summary": "Duplicate invoice line items on renewal accounts",
        "status": "open", "priority": "high", "reporter": "marcus@globex.com",
        "created": "2026-08-19", "unresolved_days": 10,
    },
    {
        "id": "UMB-77", "project": "UMB", "customer": "Umbrella Corp",
        "summary": "API rate limiting kicks in below the documented threshold",
        "status": "open", "priority": "medium", "reporter": "dev@umbrella.org",
        "created": "2026-08-21", "unresolved_days": 8,
    },
    {
        "id": "STRK-9", "project": "STRK", "customer": "Stark Industries",
        "summary": "Requesting SAML metadata refresh",
        "status": "resolved", "priority": "low", "reporter": "sec@stark.io",
        "created": "2026-08-24", "unresolved_days": 0,
    },
]


def get_issues(customer=None, status=None, priority=None, min_unresolved_days=None):
    """Fetch mock Jira issues, optionally filtered by customer, status
    (open/in_progress/resolved), priority (low/medium/high), or a minimum
    unresolved-age threshold. Returns copies."""
    results = ISSUES
    if customer:
        results = [i for i in results if i["customer"].lower() == customer.lower()]
    if status:
        results = [i for i in results if i["status"] == status]
    if priority:
        results = [i for i in results if i["priority"] == priority]
    if min_unresolved_days is not None:
        results = [i for i in results if i["unresolved_days"] >= min_unresolved_days]
    return [dict(i) for i in results]


def get_open_issue_count(customer=None):
    return len(get_issues(customer=customer, status="open")) + len(get_issues(customer=customer, status="in_progress"))


def get_overdue_issues(min_unresolved_days=10):
    """Unresolved issues past a staleness threshold — this is what backs
    the Attention workspace's 'overdue tasks' and the urgent-support
    workspace's technical-issue list."""
    results = [i for i in ISSUES if i["status"] in ("open", "in_progress") and i["unresolved_days"] >= min_unresolved_days]
    return sorted([dict(i) for i in results], key=lambda i: -i["unresolved_days"])


if __name__ == "__main__":
    import json
    print(json.dumps(get_overdue_issues(), indent=2))
