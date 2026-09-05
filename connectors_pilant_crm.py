"""
connectors_pilant_crm.py — real, persistent CRM/finance data for Pilant's
OWN business (customers, sales pipeline, revenue, expenses). Added
2026-08-30 at the user's direct request — they want to actually track
sales/customers/revenue/finance for Pilant itself, and confirmed there's
no real external CRM or accounting tool connected yet ("no real source
yet").

Unlike every other connectors_*.py in this project (connectors_gmail.py,
connectors_healthcare.py, connectors_salesforce.py, ...), this one isn't
standing in for an external API and it isn't demo/sample data either
(contrast connectors_helpdesk.py / connectors_salesforce_mock.py, which
are honestly-labeled FAKE data for a demo). This is the real, only source
of truth for Pilant's own numbers — every customer, deal, revenue entry,
and expense here exists because a person actually entered it through
pilant_crm.py's forms. This module never invents or seeds anything on its
own; an empty store just means nothing has been entered yet, and every
read function returns exactly what was written, nothing more.

Same JSON-file + threading.Lock persistence idiom as saved_views.py /
audit_log.py (see either's module docstring for the full reasoning) — no
new dependency, safe under Flask's multi-threaded dev server, and it's a
single human-readable file you can back up, diff, or hand-edit if you
ever need to.
"""

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

_STORE_PATH = Path(__file__).parent / "pilant_crm_data.json"
_LOCK = threading.Lock()

DEAL_STAGES = ["Lead", "Qualified", "Proposal", "Negotiation", "Won", "Lost"]
OPEN_STAGES = ["Lead", "Qualified", "Proposal", "Negotiation"]
CUSTOMER_STATUSES = ["lead", "prospect", "active", "churned"]
EXPENSE_CATEGORIES = ["Infrastructure", "Tools & Software", "Contractors", "Marketing", "Travel", "Other"]


def _empty_store():
    return {"customers": [], "deals": [], "revenue": [], "expenses": []}


def _read():
    if not _STORE_PATH.exists():
        return _empty_store()
    try:
        data = json.loads(_STORE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return _empty_store()
    for key in ("customers", "deals", "revenue", "expenses"):
        data.setdefault(key, [])
    return data


def _write(data):
    _STORE_PATH.write_text(json.dumps(data, indent=2))


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------

def list_customers():
    with _LOCK:
        return list(_read()["customers"])


def get_customer(customer_id):
    for c in list_customers():
        if c["id"] == customer_id:
            return c
    return None


def add_customer(name, company="", email="", phone="", status="lead", notes=""):
    if status not in CUSTOMER_STATUSES:
        status = "lead"
    customer = {
        "id": _new_id("CUST"),
        "name": (name or "").strip(),
        "company": (company or "").strip(),
        "email": (email or "").strip(),
        "phone": (phone or "").strip(),
        "status": status,
        "notes": (notes or "").strip(),
        "created_at": _now(),
    }
    with _LOCK:
        data = _read()
        data["customers"].append(customer)
        _write(data)
    return customer


def update_customer_status(customer_id, status):
    if status not in CUSTOMER_STATUSES:
        raise ValueError(f"unknown status {status!r}")
    with _LOCK:
        data = _read()
        for c in data["customers"]:
            if c["id"] == customer_id:
                c["status"] = status
                _write(data)
                return c
    return None


# ---------------------------------------------------------------------------
# Deals (sales pipeline)
# ---------------------------------------------------------------------------

def list_deals():
    with _LOCK:
        return list(_read()["deals"])


def get_deal(deal_id):
    for d in list_deals():
        if d["id"] == deal_id:
            return d
    return None


def add_deal(customer_id, title, amount, stage="Lead", expected_close_date="", notes=""):
    if stage not in DEAL_STAGES:
        stage = "Lead"
    try:
        amount = round(float(amount), 2)
    except (TypeError, ValueError):
        amount = 0.0
    deal = {
        "id": _new_id("DEAL"),
        "customer_id": customer_id,
        "title": (title or "").strip(),
        "amount": amount,
        "stage": stage,
        "expected_close_date": (expected_close_date or "").strip(),
        "notes": (notes or "").strip(),
        "created_at": _now(),
        "updated_at": _now(),
    }
    with _LOCK:
        data = _read()
        data["deals"].append(deal)
        _write(data)
    return deal


def update_deal_stage(deal_id, stage):
    """
    Moving a deal to "Won" automatically logs a matching revenue entry
    (source="deal", deal_id=<this deal>) so real revenue flows from the
    real pipeline instead of needing the same number typed in twice —
    idempotent: moving a deal to "Won" a second time (or back and forth)
    never creates a duplicate revenue entry for the same deal.
    """
    if stage not in DEAL_STAGES:
        raise ValueError(f"unknown stage {stage!r}")
    with _LOCK:
        data = _read()
        deal = None
        for d in data["deals"]:
            if d["id"] == deal_id:
                d["stage"] = stage
                d["updated_at"] = _now()
                deal = d
                break
        if deal is None:
            return None
        if stage == "Won" and not any(r.get("deal_id") == deal_id for r in data["revenue"]):
            data["revenue"].append({
                "id": _new_id("REV"),
                "amount": deal["amount"],
                "date": datetime.now(timezone.utc).date().isoformat(),
                "source": deal["title"] or "Won deal",
                "deal_id": deal_id,
                "note": "",
                "created_at": _now(),
            })
        _write(data)
        return deal


# ---------------------------------------------------------------------------
# Revenue
# ---------------------------------------------------------------------------

def list_revenue():
    with _LOCK:
        return list(_read()["revenue"])


def add_revenue(amount, date="", source="", note=""):
    """For real revenue that isn't tied to a tracked deal (a renewal, a
    one-off invoice, etc.) — a deal moved to "Won" logs its own entry
    automatically (see update_deal_stage), so this is for everything
    else."""
    try:
        amount = round(float(amount), 2)
    except (TypeError, ValueError):
        amount = 0.0
    entry = {
        "id": _new_id("REV"),
        "amount": amount,
        "date": (date or "").strip() or datetime.now(timezone.utc).date().isoformat(),
        "source": (source or "").strip(),
        "deal_id": None,
        "note": (note or "").strip(),
        "created_at": _now(),
    }
    with _LOCK:
        data = _read()
        data["revenue"].append(entry)
        _write(data)
    return entry


# ---------------------------------------------------------------------------
# Expenses
# ---------------------------------------------------------------------------

def list_expenses():
    with _LOCK:
        return list(_read()["expenses"])


def add_expense(amount, date="", category="Other", note=""):
    if category not in EXPENSE_CATEGORIES:
        category = "Other"
    try:
        amount = round(float(amount), 2)
    except (TypeError, ValueError):
        amount = 0.0
    entry = {
        "id": _new_id("EXP"),
        "amount": amount,
        "date": (date or "").strip() or datetime.now(timezone.utc).date().isoformat(),
        "category": category,
        "note": (note or "").strip(),
        "created_at": _now(),
    }
    with _LOCK:
        data = _read()
        data["expenses"].append(entry)
        _write(data)
    return entry


# ---------------------------------------------------------------------------
# Dashboard summary — every number here is computed straight from the real
# stored records above, nothing derived from anywhere else.
# ---------------------------------------------------------------------------

def dashboard_summary():
    with _LOCK:
        data = _read()
    customers = data["customers"]
    deals = data["deals"]
    revenue = data["revenue"]
    expenses = data["expenses"]

    customers_by_status = {s: 0 for s in CUSTOMER_STATUSES}
    for c in customers:
        customers_by_status[c.get("status", "lead")] = customers_by_status.get(c.get("status", "lead"), 0) + 1

    pipeline_value = sum(d["amount"] for d in deals if d.get("stage") in OPEN_STAGES)
    deals_by_stage = {s: 0 for s in DEAL_STAGES}
    value_by_stage = {s: 0.0 for s in DEAL_STAGES}
    for d in deals:
        stage = d.get("stage", "Lead")
        deals_by_stage[stage] = deals_by_stage.get(stage, 0) + 1
        value_by_stage[stage] = value_by_stage.get(stage, 0.0) + d["amount"]

    total_revenue = sum(r["amount"] for r in revenue)
    total_expenses = sum(e["amount"] for e in expenses)

    this_month = datetime.now(timezone.utc).strftime("%Y-%m")
    revenue_this_month = sum(r["amount"] for r in revenue if str(r.get("date", "")).startswith(this_month))
    expenses_this_month = sum(e["amount"] for e in expenses if str(e.get("date", "")).startswith(this_month))

    return {
        "total_customers": len(customers),
        "customers_by_status": customers_by_status,
        "total_deals": len(deals),
        "pipeline_value": round(pipeline_value, 2),
        "deals_by_stage": deals_by_stage,
        "value_by_stage": {k: round(v, 2) for k, v in value_by_stage.items()},
        "total_revenue": round(total_revenue, 2),
        "total_expenses": round(total_expenses, 2),
        "net": round(total_revenue - total_expenses, 2),
        "revenue_this_month": round(revenue_this_month, 2),
        "expenses_this_month": round(expenses_this_month, 2),
        "net_this_month": round(revenue_this_month - expenses_this_month, 2),
    }
