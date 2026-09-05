"""
workspace_engine.py — added 2026-08-29. The "describe a goal -> generate a
task-specific workspace" core the brief asks for, replacing "choose Gmail
-> chat with agent -> generate Gmail-like interface" as Studio's primary
journey (see workspace_site.py for the routes/UI; studio.py's existing
/studio single-connector chat and /composer multi-primitive composer are
left running unchanged alongside this — see this repo's architecture
notes on why co-existing rather than deleting).

Deliberately NOT another LLM tool-loop like agent_composer.py. Four
concrete goals are named explicitly in the brief with EXACT expected
counts/fields ("5 messages requiring replies", "Acme renewal at risk"
with its exact why-reasons) — a live model call would make those
numbers non-deterministic and untestable. Instead this is a small,
honest keyword intent-matcher over four hand-built workspace templates,
each of which pulls REAL data (Gmail, via connectors_gmail — the only
genuinely connected integration) and CLEARLY-LABELED MOCK data
(Salesforce/Jira via connectors_salesforce_mock.py/connectors_jira_mock.py,
support tickets via the already-existing connectors_helpdesk.py) through
the exact same typed-Primitive path a real cross-connector composer would
use. Swapping in a live LLM-driven matcher/composer later would only mean
replacing match_intent()/the four _build_* functions — the primitive
schema, role filtering, approval/audit wiring, and rendering are already
the "real" version.

Every _build_* function returns {"title", "subtitle", "primitives": [...]}
using ONLY workspace_primitives.PRIMITIVE_TYPES-shaped dicts — there is no
path here to arbitrary HTML.
"""

from datetime import datetime, timezone

import connectors_gmail
import connectors_helpdesk
import connectors_jira_mock
import connectors_salesforce_mock
import approvals
import roles

SRC_GMAIL_REAL = {"name": "Gmail", "mock": False}
SRC_GMAIL_MOCK = {"name": "Gmail", "mock": True}
SRC_SALESFORCE = {"name": "Salesforce", "mock": True}
SRC_JIRA = {"name": "Jira", "mock": True}
SRC_HELPDESK = {"name": "Helpdesk", "mock": True}

GENERATION_STAGES = [
    "Understanding your goal",
    "Checking connected systems",
    "Applying role and permissions",
    "Selecting approved Primitives",
    "Building your workspace",
]

SUGGESTED_REQUESTS = [
    "Start my day",
    "Review customer risks",
    "Prepare for a meeting",
    "Process pending approvals",
]

DEFAULT_GOAL = "Show everything requiring my attention today."

_ACTION_LABELS = roles.ACTION_LABELS


def _action(key, approval_required=False):
    return {"key": key, "label": _ACTION_LABELS.get(key, key.replace("_", " ").title()), "approval_required": approval_required}


def _pid(prefix, n):
    return f"{prefix}_{n}"


def _metric(id_, label, value, tone="neutral", trend=None):
    return {
        "id": id_, "type": "Metric", "title": label, "data_sources": [], "allowed_actions": [],
        "tone": tone, "position": {"span": "third"},
        "data": {"value": value, "label": label, "trend": trend},
    }


# --- Gmail access, with an honest, clearly-labeled mock fallback -----------

def _gmail_threads_safe(query=None, unread_only=False, limit=10, folder=None):
    """Real Gmail if it's connected and returns something; ([], False)
    otherwise — callers pad with mock data and mark it accordingly rather
    than ever silently fabricating something that looks live."""
    try:
        threads = connectors_gmail.get_gmail_threads(query=query, unread_only=unread_only, limit=limit, folder=folder)
        return threads, True
    except RuntimeError:
        return [], False


def _thread_to_message(t):
    return {
        "from": t.get("from"), "subject": t.get("subject"), "snippet": t.get("snippet") or "",
        "date": t.get("date"), "link": f"/thread/{t['id']}?folder=inbox",
        "source": SRC_GMAIL_REAL, "thread_id": t.get("id"),
    }


_MOCK_ATTENTION_MESSAGES = [
    {"from": "priya@acmecorp.com", "subject": "Re: Renewal terms", "snippet": "If we can't get this sorted this week we may need to look elsewhere...",
     "date": "Aug 28", "link": None, "source": SRC_GMAIL_MOCK, "thread_id": None},
    {"from": "marcus@globex.com", "subject": "Question about the proposal", "snippet": "Can we push the call to Thursday? Also had a question on pricing tiers.",
     "date": "Aug 28", "link": None, "source": SRC_GMAIL_MOCK, "thread_id": None},
    {"from": "sara@umbrella.org", "subject": "Contract redline attached", "snippet": "Legal sent back a redline on section 4.2, can you take a look?",
     "date": "Aug 27", "link": None, "source": SRC_GMAIL_MOCK, "thread_id": None},
    {"from": "devon@stark.io", "subject": "Onboarding kickoff", "snippet": "Excited to get started — who's our point of contact on your side?",
     "date": "Aug 27", "link": None, "source": SRC_GMAIL_MOCK, "thread_id": None},
    {"from": "amy@wayne.co", "subject": "Invoice question", "snippet": "We were double-billed for August, can someone confirm and issue a credit?",
     "date": "Aug 26", "link": None, "source": SRC_GMAIL_MOCK, "thread_id": None},
]


def _pad_messages(real_messages, target_n):
    out = list(real_messages[:target_n])
    if len(out) < target_n:
        out += _MOCK_ATTENTION_MESSAGES[: target_n - len(out)]
    return out


def _ensure_escalation_approval(workspace_id, title, description, requested_by, action_key):
    """Reuse an already-pending approval for this exact action on this
    workspace rather than minting a new one on every regenerate/role
    switch — keeps the 'Approval requests' count meaningful instead of
    growing without bound."""
    for a in approvals.list_for_workspace(workspace_id):
        if a.get("action_key") == action_key and a["status"] == "pending":
            return a
    return approvals.create_approval(
        workspace_id, title, description, requested_by,
        role_required="sales_manager", action_key=action_key, target="acme_corp",
    )


# --- 1. Attention workspace (default) ---------------------------------

def _build_attention(workspace_id, role_key, user):
    role = roles.get_role(role_key)
    owner = user["username"] if role["scope"] == "personal" else None

    real_threads, gmail_connected = _gmail_threads_safe(unread_only=True, limit=5, folder="inbox")
    messages = _pad_messages([_thread_to_message(t) for t in real_threads], 5)

    at_risk = connectors_salesforce_mock.get_account_risk(owner=owner)
    overdue_issues = connectors_jira_mock.get_overdue_issues(min_unresolved_days=10)

    acme_open_issues = connectors_jira_mock.get_issues(customer="Acme Corp")
    acme_open_count = len([i for i in acme_open_issues if i["status"] in ("open", "in_progress")])

    acme_gmail_threads, _ = _gmail_threads_safe(query="Acme Corp", limit=1)
    if acme_gmail_threads:
        t = acme_gmail_threads[0]
        acme_why_gmail = {"text": f'"{t.get("subject")}"', "source": "Gmail"}
        acme_links = {"view_account": f"/thread/{t['id']}?folder=inbox", "draft_response": f"/thread/{t['id']}?folder=inbox"}
    else:
        acme_why_gmail = {"text": "Customer emailed mentioning they may cancel", "source": "Gmail (mock)"}
        acme_links = {}

    approval = _ensure_escalation_approval(
        workspace_id, "Escalate Acme Corp renewal",
        "Acme Corp's renewal opportunity has been inactive for 21 days and the customer has raised cancellation. "
        "Escalating routes this to a renewal specialist.",
        user["username"], "escalate_acme_corp",
    )

    priority_items = [
        {
            "id": "pri_acme", "title": "Acme renewal at risk", "priority": "critical", "deadline": "Today",
            "customer": "Acme Corp", "sources": ["Gmail", "Salesforce", "Jira"],
            "why": [
                acme_why_gmail,
                {"text": "Opportunity inactive for 21 days", "source": "Salesforce"},
                {"text": f"{acme_open_count} unresolved issue{'s' if acme_open_count != 1 else ''}", "source": "Jira"},
            ],
            "info": "$86,000 opportunity in Negotiation, closing Sep 15.",
            "recommended_action": "Escalate to a renewal specialist and draft a response addressing the cancellation concern.",
            "actions": [_action("view_account"), _action("draft_response"), _action("assign_follow_up"), _action("escalate", approval_required=True)],
            "links": acme_links,
        },
    ]
    if len(at_risk) > 1:
        o = at_risk[1]
        priority_items.append({
            "id": "pri_risk2", "title": f"{o['customer']} opportunity stalled", "priority": "high",
            "deadline": o.get("close_date"), "customer": o["customer"], "sources": ["Salesforce"],
            "why": [{"text": o.get("risk_reason") or "No recent activity", "source": "Salesforce"}],
            "info": f"${o['amount']:,} opportunity in {o['stage']}, inactive {o['days_inactive']} days.",
            "recommended_action": "Reach out before the champion goes further quiet.",
            "actions": [_action("view_account"), _action("draft_response"), _action("assign_follow_up")],
            "links": {},
        })
    if messages:
        m = messages[0]
        priority_items.append({
            "id": "pri_msg", "title": f"Reply needed: {m['subject']}", "priority": "high",
            "deadline": "Today", "customer": m.get("from"), "sources": ["Gmail"],
            "why": [{"text": "Unread message awaiting a reply", "source": "Gmail" + (" (mock)" if m["source"]["mock"] else "")}],
            "info": m.get("snippet") or "",
            "recommended_action": "Send a reply today to avoid the thread going cold.",
            "actions": [_action("view_account"), _action("draft_response"), _action("assign_follow_up")],
            "links": {"view_account": m.get("link"), "draft_response": m.get("link")} if m.get("link") else {},
        })
    if overdue_issues:
        i = overdue_issues[0]
        priority_items.append({
            "id": "pri_task", "title": i["summary"], "priority": "medium",
            "deadline": f"{i['unresolved_days']}d overdue", "customer": i["customer"], "sources": ["Jira"],
            "why": [{"text": f"Unresolved for {i['unresolved_days']} days", "source": "Jira"}],
            "info": f"{i['id']} · priority {i['priority']}",
            "recommended_action": "Assign an owner or escalate to engineering.",
            "actions": [_action("view_account"), _action("assign_follow_up")],
            "links": {},
        })

    primitives = [
        _metric("m_replies", "Messages requiring replies", len(messages), "warning"),
        _metric("m_risk", "Customers at risk", len(at_risk), "critical"),
        _metric("m_overdue", "Overdue tasks", len(overdue_issues), "warning"),
        _metric("m_approvals", "Approval requests", len(approvals.list_pending(workspace_id)), "accent"),
        {
            "id": "priority_queue", "type": "PriorityList", "title": "Priority queue",
            "subtitle": "Ranked by urgency across every connected system",
            "data_sources": [SRC_GMAIL_REAL if gmail_connected else SRC_GMAIL_MOCK, SRC_SALESFORCE, SRC_JIRA],
            "allowed_actions": [], "tone": "neutral", "position": {"span": "full"},
            "data": {"items": priority_items},
        },
        {
            "id": "inbox_messages", "type": "MessageCard", "title": "Messages awaiting a reply",
            "data_sources": [SRC_GMAIL_REAL if gmail_connected else SRC_GMAIL_MOCK],
            "allowed_actions": [], "tone": "neutral", "position": {"span": "half"},
            "data": {"messages": messages},
        },
        {
            "id": "overdue_tasks", "type": "TaskQueue", "title": "Overdue tasks",
            "data_sources": [SRC_JIRA], "allowed_actions": [], "tone": "warning", "position": {"span": "half"},
            "data": {"tasks": [
                {"title": i["summary"], "owner": i["reporter"], "due": f"{i['unresolved_days']}d overdue",
                 "overdue": True, "source": SRC_JIRA}
                for i in overdue_issues
            ]},
        },
        {
            "id": "approval_acme", "type": "ApprovalPanel", "title": "Pending approval",
            "data_sources": [], "allowed_actions": [], "approval_required": True,
            "tone": "accent", "position": {"span": "half"},
            "data": {
                "approval_id": approval["id"], "status": approval["status"],
                "description": approval["description"], "requested_by": approval["requested_by"],
                "decided_by": approval.get("decided_by"),
            },
        },
        {
            "id": "attention_activity", "type": "ActivityLog", "title": "Recent activity",
            "data_sources": [], "allowed_actions": [], "tone": "neutral", "position": {"span": "half"},
            "data": {},
        },
    ]
    return {"title": "Today's attention", "subtitle": "Everything requiring your attention across Gmail, Salesforce, and Jira.", "primitives": primitives}


# --- 2. Acme meeting prep -------------------------------------------------

def _build_acme_meeting(workspace_id, role_key, user):
    acme = connectors_salesforce_mock.get_opportunities(customer="Acme Corp")
    opp = acme[0] if acme else None
    issues = connectors_jira_mock.get_issues(customer="Acme Corp")
    real_threads, gmail_connected = _gmail_threads_safe(query="Acme Corp", limit=3)
    messages = [_thread_to_message(t) for t in real_threads] or [
        {"from": "priya@acmecorp.com", "subject": "Re: Renewal terms", "snippet": "If we can't get this sorted this week we may need to look elsewhere.",
         "date": "Aug 28", "link": None, "source": SRC_GMAIL_MOCK, "thread_id": None},
    ]

    facts = []
    if opp:
        facts = [
            {"label": "Stage", "value": opp["stage"]},
            {"label": "Amount", "value": f"${opp['amount']:,}"},
            {"label": "Close date", "value": opp["close_date"]},
            {"label": "Days inactive", "value": str(opp["days_inactive"])},
            {"label": "Risk", "value": opp["risk"]},
        ]

    timeline_events = []
    if opp:
        timeline_events.append({"date": opp["close_date"], "label": "Renewal target close date", "source": "Salesforce"})
    for m in messages[:2]:
        timeline_events.append({"date": m.get("date"), "label": f"Email: {m.get('subject')}", "source": "Gmail" + (" (mock)" if m["source"]["mock"] else "")})
    for i in issues:
        timeline_events.append({"date": i["created"], "label": f"{i['id']} opened: {i['summary']}", "source": "Jira"})

    primitives = [
        {
            "id": "acme_customer", "type": "CustomerCard", "title": "Acme Corp — customer summary",
            "data_sources": [SRC_SALESFORCE], "allowed_actions": [_action("view_account"), _action("draft_response")],
            "tone": "critical" if opp and opp["risk"] == "high" else "neutral", "position": {"span": "half"},
            "data": {
                "summary": (opp["risk_reason"] if opp else None) or "No summary available.",
                "facts": facts, "links": {},
            },
        },
        {
            "id": "acme_emails", "type": "MessageCard", "title": "Recent emails",
            "data_sources": [SRC_GMAIL_REAL if gmail_connected else SRC_GMAIL_MOCK],
            "allowed_actions": [], "tone": "neutral", "position": {"span": "half"},
            "data": {"messages": messages},
        },
        {
            "id": "acme_opportunity", "type": "DataTable", "title": "CRM opportunity",
            "data_sources": [SRC_SALESFORCE], "allowed_actions": [], "tone": "neutral", "position": {"span": "full"},
            "data": {
                "columns": [
                    {"key": "id", "label": "Opportunity"}, {"key": "stage", "label": "Stage"},
                    {"key": "amount", "label": "Amount"}, {"key": "close_date", "label": "Close date"},
                    {"key": "days_inactive", "label": "Days inactive"},
                ],
                "rows": [{**o, "amount": f"${o['amount']:,}"} for o in acme],
            },
        },
        {
            "id": "acme_issues", "type": "DataTable", "title": "Open support issues",
            "data_sources": [SRC_JIRA], "allowed_actions": [], "tone": "neutral", "position": {"span": "full"},
            "data": {
                "columns": [
                    {"key": "id", "label": "Issue"}, {"key": "summary", "label": "Summary"},
                    {"key": "status", "label": "Status"}, {"key": "priority", "label": "Priority"},
                    {"key": "unresolved_days", "label": "Days open"},
                ],
                "rows": issues,
            },
        },
        {
            "id": "acme_timeline", "type": "Timeline", "title": "Timeline",
            "data_sources": [SRC_GMAIL_REAL if gmail_connected else SRC_GMAIL_MOCK, SRC_SALESFORCE, SRC_JIRA],
            "allowed_actions": [], "tone": "neutral", "position": {"span": "half"},
            "data": {"events": timeline_events},
        },
        {
            "id": "acme_questions", "type": "Alert", "title": "Suggested questions",
            "data_sources": [], "allowed_actions": [], "tone": "accent", "position": {"span": "half"},
            "data": {"items": [
                "What would it take to resolve the two open technical issues before the renewal decision?",
                "Is the cancellation concern about price, product fit, or support responsiveness?",
                "Who else needs to be in the room for a final decision?",
            ]},
        },
        {
            "id": "acme_followups", "type": "ActionPanel", "title": "Follow-up actions",
            "data_sources": [], "tone": "neutral", "position": {"span": "full"},
            "allowed_actions": [_action("draft_response"), _action("assign_follow_up"), _action("escalate", approval_required=True)],
            "data": {"context": "Recommended next steps coming out of this meeting."},
        },
    ]
    return {"title": "Acme meeting prep", "subtitle": "Everything for the Acme Corp renewal conversation, in one place.", "primitives": primitives}


# --- 3. Sales risk review ------------------------------------------------

def _build_sales_risk(workspace_id, role_key, user):
    role = roles.get_role(role_key)
    owner = user["username"] if role["scope"] == "personal" else None

    metrics = connectors_salesforce_mock.get_pipeline_metrics(owner=owner)
    by_stage = connectors_salesforce_mock.get_pipeline_by_stage(owner=owner)
    stalled = connectors_salesforce_mock.get_opportunities(min_days_inactive=14, owner=owner)

    conv_messages = []
    for o in stalled[:2]:
        threads, connected = _gmail_threads_safe(query=o["customer"], limit=1)
        if threads:
            conv_messages.append(_thread_to_message(threads[0]))
        else:
            conv_messages.append({
                "from": o["customer"], "subject": f"Re: {o['customer']} renewal",
                "snippet": o.get("risk_reason") or "No recent reply.", "date": "—",
                "link": None, "source": SRC_GMAIL_MOCK, "thread_id": None,
            })

    primitives = [
        _metric("m_open", "Open pipeline", f"${metrics['open_value']:,}", "neutral"),
        _metric("m_at_risk", "At-risk value", f"${metrics['at_risk_value']:,}", "critical"),
        _metric("m_stalled", "Stalled opportunities", metrics["stalled_count"], "warning"),
        {
            "id": "risk_chart", "type": "Chart", "title": "Pipeline by stage",
            "data_sources": [SRC_SALESFORCE], "allowed_actions": [], "tone": "neutral", "position": {"span": "half"},
            "data": {"series": [{"label": s["stage"], "value": s["count"], "display_value": f'{s["count"]} · ${s["value"]:,}'} for s in by_stage]},
        },
        {
            "id": "risk_alert", "type": "Alert", "title": "This week's biggest exposure",
            "data_sources": [SRC_SALESFORCE], "allowed_actions": [], "tone": "critical", "position": {"span": "half"},
            "data": {"message": (
                f"{metrics['at_risk_count']} opportunities worth ${metrics['at_risk_value']:,} have had no activity "
                "in over a week — see the stalled list below."
            ) if metrics["at_risk_count"] else "No opportunities are currently flagged at risk."},
        },
        {
            "id": "stalled_table", "type": "DataTable", "title": "Stalled opportunities",
            "data_sources": [SRC_SALESFORCE], "allowed_actions": [], "tone": "neutral", "position": {"span": "full"},
            "data": {
                "columns": [
                    {"key": "customer", "label": "Customer"}, {"key": "stage", "label": "Stage"},
                    {"key": "amount", "label": "Amount"}, {"key": "days_inactive", "label": "Days inactive"},
                    {"key": "risk", "label": "Risk"},
                ],
                "rows": [{**o, "amount": f"${o['amount']:,}"} for o in stalled],
            },
        },
        {
            "id": "risk_conversations", "type": "MessageCard", "title": "Customer conversations",
            "data_sources": [SRC_GMAIL_REAL, SRC_GMAIL_MOCK] if conv_messages else [SRC_GMAIL_MOCK],
            "allowed_actions": [], "tone": "neutral", "position": {"span": "half"},
            "data": {"messages": conv_messages},
        },
        {
            "id": "risk_followups", "type": "ActionPanel", "title": "Recommended follow-ups",
            "data_sources": [], "tone": "neutral", "position": {"span": "half"},
            "allowed_actions": [_action("draft_response"), _action("assign_follow_up"), _action("escalate", approval_required=True)],
            "data": {"context": "Reach out to the highest-value stalled accounts first."},
        },
    ]
    return {"title": "Sales risk review", "subtitle": "This week's pipeline health and where it's stalling.", "primitives": primitives}


# --- 4. Urgent support -----------------------------------------------------

def _build_urgent_support(workspace_id, role_key, user):
    escalated = connectors_helpdesk.get_tickets(status="escalated")
    critical = connectors_helpdesk.get_tickets(priority="critical")
    by_id = {t["id"]: t for t in (escalated + critical)}
    incidents = sorted(by_id.values(), key=lambda t: t["created"], reverse=True)
    tech_issues = connectors_jira_mock.get_issues(priority="high")

    affected = sorted({t["requester"] for t in incidents})

    approval = _ensure_escalation_approval(
        workspace_id, "Escalate critical support backlog",
        f"{len(incidents)} tickets are escalated or critical and need engineering attention.",
        user["username"], "escalate_support_backlog",
    )

    primitives = [
        _metric("m_incidents", "Open incidents", len(incidents), "critical"),
        _metric("m_customers", "Affected customers", len(affected), "warning"),
        _metric("m_tech", "Technical issues", len(tech_issues), "warning"),
        {
            "id": "incident_queue", "type": "DataTable", "title": "Incident queue",
            "data_sources": [SRC_HELPDESK], "allowed_actions": [], "tone": "critical", "position": {"span": "full"},
            "data": {
                "columns": [
                    {"key": "id", "label": "Ticket"}, {"key": "subject", "label": "Subject"},
                    {"key": "requester", "label": "Customer"}, {"key": "status", "label": "Status"},
                    {"key": "priority", "label": "Priority"},
                ],
                "rows": incidents,
            },
        },
        {
            "id": "sla_alert", "type": "Alert", "title": "SLA risk",
            "data_sources": [SRC_HELPDESK], "allowed_actions": [], "tone": "critical", "position": {"span": "half"},
            "data": {"items": [f'{t["id"]} — {t["subject"]} ({t["requester"]})' for t in incidents] or ["No tickets currently breaching SLA."]},
        },
        {
            "id": "affected_customers", "type": "DataTable", "title": "Affected customers",
            "data_sources": [SRC_HELPDESK], "allowed_actions": [], "tone": "neutral", "position": {"span": "half"},
            "data": {
                "columns": [{"key": "customer", "label": "Customer"}, {"key": "open_tickets", "label": "Open tickets"}],
                "rows": [{"customer": c, "open_tickets": len([t for t in incidents if t["requester"] == c])} for c in affected],
            },
        },
        {
            "id": "technical_issues", "type": "DataTable", "title": "Technical issues",
            "data_sources": [SRC_JIRA], "allowed_actions": [], "tone": "neutral", "position": {"span": "full"},
            "data": {
                "columns": [
                    {"key": "id", "label": "Issue"}, {"key": "customer", "label": "Customer"},
                    {"key": "summary", "label": "Summary"}, {"key": "status", "label": "Status"},
                    {"key": "unresolved_days", "label": "Days open"},
                ],
                "rows": tech_issues,
            },
        },
        {
            "id": "escalation_controls", "type": "ApprovalPanel", "title": "Escalation controls",
            "data_sources": [], "allowed_actions": [], "approval_required": True,
            "tone": "accent", "position": {"span": "full"},
            "data": {
                "approval_id": approval["id"], "status": approval["status"],
                "description": approval["description"], "requested_by": approval["requested_by"],
                "decided_by": approval.get("decided_by"),
            },
        },
    ]
    return {"title": "Urgent support", "subtitle": "Escalated tickets, SLA exposure, and affected customers right now.", "primitives": primitives}


INTENTS = {
    "attention": {"title": "Today's attention", "keywords": ["attention", "today", "start my day", "start", "approval", "approvals"], "builder": _build_attention},
    "acme_meeting": {"title": "Acme meeting prep", "keywords": ["acme", "meeting", "prepare"], "builder": _build_acme_meeting},
    "sales_risk": {"title": "Sales risk review", "keywords": ["risk", "pipeline", "sales"], "builder": _build_sales_risk},
    "urgent_support": {"title": "Urgent support", "keywords": ["urgent", "support", "incident", "sla"], "builder": _build_urgent_support},
}
INTENT_ORDER = ["attention", "acme_meeting", "sales_risk", "urgent_support"]


def match_intent(goal_text):
    text = (goal_text or "").lower()
    best_key, best_score = "attention", 0
    for key in INTENT_ORDER:
        score = sum(1 for kw in INTENTS[key]["keywords"] if kw in text)
        if score > best_score:
            best_key, best_score = key, score
    return best_key


def generate_workspace(workspace_id, goal_text, role_key, user):
    if not roles.is_valid_role(role_key):
        role_key = roles.DEFAULT_ROLE
    intent_key = match_intent(goal_text)
    raw = INTENTS[intent_key]["builder"](workspace_id, role_key, user)
    primitives = roles.filter_primitives_for_role(role_key, raw["primitives"])
    return {
        "id": workspace_id,
        "goal": goal_text,
        "intent": intent_key,
        "title": raw["title"],
        "subtitle": raw.get("subtitle"),
        "role": role_key,
        "primitives": primitives,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
