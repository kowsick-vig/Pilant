"""
connectors_salesforce_mock.py — added 2026-08-29 for the Pilant Studio
workspace-generation rewrite (see workspace_engine.py's module docstring).

Deliberately static/mock, same shape and same reasoning as
connectors_helpdesk.py: no real Salesforce org is connected to this
prototype, so rather than fake a real API integration this is an honest,
clearly-labeled adapter with realistic CRM data baked in. Every function
here returns the same field shapes a real Salesforce connector would
(opportunity id, account, stage, amount, days since last activity, risk
flag) so a real `simple_salesforce`-backed version could replace this file
without changing a single caller in workspace_engine.py — the whole point
of "structure the data layer so mock sources can later be replaced with
real connectors" from the brief.

`owner` on each opportunity is a username from users.py — this is what
lets roles.py's scoping ("a Sales Rep sees personal customers, a Sales
Manager sees the team") actually filter something real instead of being
decorative.
"""

OPPORTUNITIES = [
    {
        "id": "OPP-1042", "account_id": "ACC-ACME", "customer": "Acme Corp",
        "amount": 86000, "stage": "Negotiation", "owner": "kowsick",
        "days_inactive": 21, "close_date": "2026-09-15",
        "risk": "high", "risk_reason": "No pipeline activity in 21 days; last contact raised cancellation.",
    },
    {
        "id": "OPP-1055", "account_id": "ACC-GLOBEX", "customer": "Globex Inc",
        "amount": 41000, "stage": "Proposal", "owner": "kowsick",
        "days_inactive": 9, "close_date": "2026-09-05",
        "risk": "medium", "risk_reason": "Champion has gone quiet since the last proposal revision.",
    },
    {
        "id": "OPP-1061", "account_id": "ACC-INITECH", "customer": "Initech",
        "amount": 18500, "stage": "Discovery", "owner": "analyst",
        "days_inactive": 2, "close_date": "2026-09-22",
        "risk": "low", "risk_reason": None,
    },
    {
        "id": "OPP-1049", "account_id": "ACC-UMBRELLA", "customer": "Umbrella Corp",
        "amount": 124000, "stage": "Negotiation", "owner": "analyst",
        "days_inactive": 16, "close_date": "2026-09-01",
        "risk": "high", "risk_reason": "Procurement has missed two scheduled calls this month.",
    },
    {
        "id": "OPP-1070", "account_id": "ACC-STARK", "customer": "Stark Industries",
        "amount": 62000, "stage": "Proposal", "owner": "kowsick",
        "days_inactive": 4, "close_date": "2026-09-18",
        "risk": "low", "risk_reason": None,
    },
    {
        "id": "OPP-1033", "account_id": "ACC-WAYNE", "customer": "Wayne Enterprises",
        "amount": 205000, "stage": "Negotiation", "owner": "analyst",
        "days_inactive": 1, "close_date": "2026-08-30",
        "risk": "low", "risk_reason": None,
    },
]


def get_opportunities(owner=None, customer=None, risk=None, min_days_inactive=None):
    """Fetch mock opportunities, optionally filtered by owner (username),
    customer name, risk tier (low/medium/high), or a minimum days-inactive
    threshold. Returns copies — callers can't mutate the module list."""
    results = OPPORTUNITIES
    if owner:
        results = [o for o in results if o["owner"] == owner]
    if customer:
        results = [o for o in results if o["customer"].lower() == customer.lower()]
    if risk:
        results = [o for o in results if o["risk"] == risk]
    if min_days_inactive is not None:
        results = [o for o in results if o["days_inactive"] >= min_days_inactive]
    return [dict(o) for o in results]


def get_account_risk(owner=None):
    """The at-risk subset (risk in {medium, high}), sorted worst-first —
    this is what backs the Attention workspace's 'customers at risk'
    metric and the sales-risk workspace's stalled-opportunities list."""
    order = {"high": 0, "medium": 1, "low": 2}
    results = get_opportunities(owner=owner)
    at_risk = [o for o in results if o["risk"] in ("medium", "high")]
    return sorted(at_risk, key=lambda o: (order[o["risk"]], -o["days_inactive"]))


def get_pipeline_metrics(owner=None):
    """Aggregate pipeline numbers — what an Executive/Sales Manager role
    sees instead of row-level opportunity detail."""
    opps = get_opportunities(owner=owner)
    at_risk = [o for o in opps if o["risk"] in ("medium", "high")]
    return {
        "open_count": len(opps),
        "open_value": sum(o["amount"] for o in opps),
        "at_risk_count": len(at_risk),
        "at_risk_value": sum(o["amount"] for o in at_risk),
        "stalled_count": len([o for o in opps if o["days_inactive"] >= 14]),
    }


def get_pipeline_by_stage(owner=None):
    """Stage-bucketed counts/values for a Chart primitive."""
    opps = get_opportunities(owner=owner)
    stages = ["Discovery", "Proposal", "Negotiation"]
    return [
        {
            "stage": s,
            "count": len([o for o in opps if o["stage"] == s]),
            "value": sum(o["amount"] for o in opps if o["stage"] == s),
        }
        for s in stages
    ]


if __name__ == "__main__":
    import json
    print(json.dumps(get_account_risk(), indent=2))
