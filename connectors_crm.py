"""A sixth source, same role as connectors_helpdesk.py / connectors_splunk.py:
entirely static/fictional sample data, no network call, no API key, nothing
that can fail. Requested directly ("build a face crm with crowded tasks and
add it into my new connector" — a sample CRM, not a real CRM integration).
All companies, contacts, reps, and task text below are fictional.
"""
from datetime import datetime, timedelta, timezone

STATUSES = ['open', 'in_progress', 'overdue', 'done']
PRIORITIES = ['high', 'medium', 'low']
STAGES = ['Prospecting', 'Qualification', 'Proposal', 'Negotiation', 'Closed Won', 'Closed Lost']


def get_tasks(now=None):
    """Fictional CRM follow-up/task queue — calls, emails, demos, and
    renewals spread across a deliberately crowded set of deals so a
    generated app has something to actually organize. Returns a copy of
    each row — callers can't mutate the fixture."""
    now = now or datetime.now(timezone.utc)
    reps = ['Jordan Lee', 'Ava Thompson', 'Marcus Webb', 'Sofia Rinaldi', 'Derek Chan', None]
    contacts = ['Owen Blake', 'Sarah Kim', 'James Okafor', 'Elena Vasquez', 'Priya Desai',
        'Noah Bennett', 'Rachel Ortiz', 'Tariq Malik', 'Chloe Bergstrom', 'Diego Santos',
        'Amara Obi', 'Lucas Ferreira', 'Hana Suzuki', 'Freya Nilsen', 'Victor Osei',
        'Mei Lin Tan', 'Aiden Murphy', 'Isabela Rocha', 'Yusuf Karim', 'Grace Whitfield',
        'Oscar Delgado', 'Nadia Hassan', 'Ben Coleman', 'Clara Fontaine']
    companies = ['Northwind Traders', 'Globex Corp', 'Initech', 'Umbrella Corp', 'Stark Industries',
        'Wayne Enterprises', 'Hooli', 'Piedpiper', 'Aviato', 'Massive Dynamic', 'Soylent Corp',
        'Cyberdyne Systems', 'Acme Co', 'Contoso Ltd', 'Wonka Industries', 'Gringotts Financial',
        'Oscorp', 'Tyrell Corp', 'Vandelay Industries', 'Prestige Worldwide', 'Buy n Large',
        'Duff Brewing', 'Sirius Cybernetics', 'Weyland-Yutani']
    # (task, type, priority, status, stage, hours_from_now, deal_value)
    # negative hours = due in the past
    examples = [
        ('Follow up on pricing proposal', 'call', 'high', 'overdue', 'Proposal', -30, 42000),
        ('Send updated contract for signature', 'email', 'high', 'overdue', 'Negotiation', -18, 128000),
        ('Schedule product demo', 'demo', 'medium', 'open', 'Qualification', 20, 15000),
        ('Call to discuss renewal terms', 'call', 'high', 'in_progress', 'Negotiation', 4, 64000),
        ('Send onboarding welcome email', 'email', 'low', 'done', 'Closed Won', -72, 22000),
        ('Confirm meeting time for Thursday', 'email', 'low', 'open', 'Proposal', 30, 18500),
        ('Follow up after trial expiration', 'call', 'high', 'overdue', 'Qualification', -12, 9000),
        ('Send case study for enterprise plan', 'email', 'medium', 'open', 'Prospecting', 40, 95000),
        ('Qualify inbound lead from webinar', 'call', 'medium', 'open', 'Prospecting', 8, 12000),
        ('Schedule discovery call', 'call', 'medium', 'in_progress', 'Prospecting', 16, 27000),
        ('Send proposal for Q4 renewal', 'email', 'high', 'in_progress', 'Negotiation', 6, 210000),
        ('Check in after implementation', 'call', 'low', 'done', 'Closed Won', -96, 31000),
        ('Send thank you note post-demo', 'email', 'low', 'done', 'Proposal', -48, 15000),
        ('Follow up on unanswered proposal', 'email', 'high', 'overdue', 'Proposal', -24, 58000),
        ('Prepare quote for expanded seats', 'task', 'medium', 'open', 'Closed Won', 28, 33000),
        ('Call about invoice discrepancy', 'call', 'high', 'overdue', 'Closed Won', -6, 0),
        ('Send NDA for review', 'email', 'low', 'open', 'Prospecting', 50, 0),
        ('Schedule executive business review', 'meeting', 'medium', 'open', 'Closed Won', 60, 0),
        ('Follow up on trial feedback', 'call', 'medium', 'in_progress', 'Qualification', 10, 21000),
        ('Confirm decision maker for deal', 'call', 'high', 'open', 'Qualification', 14, 47000),
        ('Send competitive comparison sheet', 'email', 'medium', 'open', 'Proposal', 34, 39000),
        ('Reach out to cold lead from conference', 'call', 'low', 'open', 'Prospecting', 70, 8000),
        ('Schedule renewal call', 'call', 'high', 'in_progress', 'Negotiation', 2, 87000),
        ('Send updated pricing sheet', 'email', 'medium', 'done', 'Proposal', -60, 26000),
        ('Follow up on support escalation', 'call', 'high', 'overdue', 'Closed Won', -3, 0),
        ('Prepare custom demo for enterprise prospect', 'demo', 'high', 'in_progress', 'Qualification', 12, 175000),
        ('Send meeting recap and next steps', 'email', 'low', 'done', 'Proposal', -12, 19000),
        ('Call to re-engage stalled deal', 'call', 'medium', 'overdue', 'Negotiation', -40, 52000),
    ]
    rows = []
    for i, (title, task_type, priority, status, stage, hours, value) in enumerate(examples):
        rows.append({
            'id': f'CRM-{4001 + i}', 'title': title, 'task_type': task_type, 'priority': priority,
            'status': status, 'owner': reps[i % len(reps)] or 'Unassigned',
            'contact': contacts[i % len(contacts)], 'company': companies[i % len(companies)],
            'stage': stage, 'deal_value': value,
            'due': (now + timedelta(hours=hours)).isoformat(),
            'notes': f'{task_type.capitalize()} with {contacts[i % len(contacts)]} at {companies[i % len(companies)]} ({stage}).',
            'data_origin': 'Fictional sample data',
        })
    return rows


if __name__ == "__main__":
    # Quick manual check: python3 connectors_crm.py
    import json
    print(json.dumps(get_tasks(), indent=2))
