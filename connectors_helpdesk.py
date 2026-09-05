"""
A third connector — this one entirely STATIC/dummy on purpose, unlike
connectors_github.py. No network call, no repo, no rate limit, no
external dependency of any kind: TICKETS below is just a Python list baked
into this file. This exists specifically so there's a demo of the full
conversational flow (agent_helpdesk.py + helpdesk_site.py) that can never
fail because of GitHub being unreachable or rate-limited — the only
external dependency left is the NVIDIA model call itself.

This is still real domain-independence proof, same as connectors_retail.py
was for orders/inventory: a fictional software company's support desk
instead of security incidents, GitHub issues, or retail orders. Nothing in
agent_helpdesk.py or helpdesk_site.py changes shape because of that — same
schema, same guardrails, same conversational ask_user flow as
agent_github.py.
"""

TICKETS = [
    {"id": "TK-1042", "subject": "Can't reset password", "requester": "priya@acme.io", "status": "open", "priority": "high", "category": "account", "created": "2026-08-20"},
    {"id": "TK-1041", "subject": "Invoice shows duplicate charge", "requester": "marcus@globex.com", "status": "escalated", "priority": "critical", "category": "billing", "created": "2026-08-19"},
    {"id": "TK-1039", "subject": "Export button does nothing", "requester": "lee@initech.com", "status": "in_progress", "priority": "medium", "category": "bug", "created": "2026-08-18"},
    {"id": "TK-1036", "subject": "Feature request: dark mode", "requester": "sara@umbrella.org", "status": "open", "priority": "low", "category": "feature_request", "created": "2026-08-17"},
    {"id": "TK-1033", "subject": "SSO login loop", "requester": "devon@stark.io", "status": "escalated", "priority": "critical", "category": "account", "created": "2026-08-16"},
    {"id": "TK-1030", "subject": "Billing page 500 error", "requester": "amy@wayne.co", "status": "resolved", "priority": "high", "category": "bug", "created": "2026-08-14"},
    {"id": "TK-1028", "subject": "Need seats added to plan", "requester": "noah@hooli.com", "status": "in_progress", "priority": "medium", "category": "billing", "created": "2026-08-13"},
    {"id": "TK-1025", "subject": "API rate limit too low for our use case", "requester": "julia@piedpiper.com", "status": "open", "priority": "medium", "category": "feature_request", "created": "2026-08-12"},
    {"id": "TK-1021", "subject": "Duplicate emails on signup", "requester": "ravi@aviato.com", "status": "resolved", "priority": "low", "category": "bug", "created": "2026-08-10"},
    {"id": "TK-1018", "subject": "Can't cancel subscription", "requester": "eve@hooli.com", "status": "escalated", "priority": "high", "category": "billing", "created": "2026-08-08"},
]


def get_tickets(status=None, priority=None, category=None):
    """
    Fetch support tickets, optionally filtered by status
    (open/in_progress/resolved/escalated), priority (low/medium/high/critical),
    and/or category (account/billing/bug/feature_request). Returns a copy
    of each matching ticket — callers can't accidentally mutate TICKETS.
    """
    results = TICKETS
    if status:
        results = [t for t in results if t["status"] == status]
    if priority:
        results = [t for t in results if t["priority"] == priority]
    if category:
        results = [t for t in results if t["category"] == category]
    return [dict(t) for t in results]


if __name__ == "__main__":
    # Quick manual check: python3 connectors_helpdesk.py
    import json
    print(json.dumps(get_tickets(), indent=2))
