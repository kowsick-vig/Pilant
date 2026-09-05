"""
A mock connector — stands in for a real read-only adapter into a customer's
security tool. Same shape a real connector would have: a couple of callable
functions returning structured data, nothing about rendering.
"""

INCIDENTS = [
    {
        "name": "Customer gateway compromise",
        "severity": "critical",
        "affects_customer_systems": True,
        "opened": "41 minutes ago",
        "status": "investigating",
        "systems": ["GATEWAY-03", "API-01", "CRM-01"],
        "details": {
            "Passed activity": "184 denied connections",
            "Network activity": "Encrypted outbound burst",
            "Threat intelligence": "High-confidence C2 match"
        }
    },
    {
        "name": "Exposed API credentials",
        "severity": "critical",
        "affects_customer_systems": True,
        "opened": "2 hours ago",
        "status": "queued",
        "systems": ["API-01"],
        "details": {"Passed activity": "Credential used from new region"}
    },
    {
        "name": "Unusual admin login pattern",
        "severity": "critical",
        "affects_customer_systems": False,
        "opened": "5 hours ago",
        "status": "queued",
        "systems": ["INTERNAL-ADMIN-02"],
        "details": {"Passed activity": "3 failed MFA attempts, then success"}
    },
    {
        "name": "Elevated background scan traffic",
        "severity": "low",
        "affects_customer_systems": False,
        "opened": "1 day ago",
        "status": "queued",
        "systems": ["EDGE-11"],
        "details": {}
    }
]

APPROVALS = [
    {
        "name": "Block suspicious IP address",
        "reason": "Exploiting credentials detected 4 hours ago",
        "requires": "senior approval"
    }
]


def get_security_incidents(customer_systems_only=False, severity=None):
    results = INCIDENTS
    if customer_systems_only:
        results = [i for i in results if i["affects_customer_systems"]]
    if severity:
        results = [i for i in results if i["severity"] == severity]
    return results


def get_pending_approvals():
    return APPROVALS
