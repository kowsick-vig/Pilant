"""
approvals.py — added 2026-08-29. The human-approval gate the brief asks
for on sensitive actions ("Escalate" on an at-risk account, in the
Attention workspace's Acme example). Same JSON-file + lock persistence
idiom as saved_views.py/audit_log.py.

An approval record's lifecycle is deliberately narrow: created once
(status "pending"), decided at most once (status becomes "approved" or
"rejected", `decided_by`/`decided_at` set) — there is no re-opening or
editing a decided approval through this module's API. Every decision
also writes an audit_log.py entry in the same call, so "an approval
happened" and "it's in the audit trail" can never drift apart.
"""

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

import audit_log

_STORE_PATH = Path(__file__).parent / "approvals.json"
_LOCK = threading.Lock()


def _read_all():
    if not _STORE_PATH.exists():
        return {}
    try:
        return json.loads(_STORE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _write_all(data):
    _STORE_PATH.write_text(json.dumps(data, indent=2))


def create_approval(workspace_id, title, description, requested_by, role_required="sales_manager", action_key=None, target=None):
    """Register one pending approval tied to a workspace + a specific
    action on a specific primitive (`action_key`/`target`, both optional
    free-text identifiers workspace_engine.py sets so the UI can find its
    way back to the item that triggered this)."""
    approval = {
        "id": "appr_" + uuid.uuid4().hex[:10],
        "workspace_id": workspace_id,
        "title": title,
        "description": description,
        "requested_by": requested_by,
        "role_required": role_required,
        "action_key": action_key,
        "target": target,
        "status": "pending",
        "decided_by": None,
        "decided_at": None,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    with _LOCK:
        data = _read_all()
        data[approval["id"]] = approval
        _write_all(data)
    return approval


def get_approval(approval_id):
    return _read_all().get(approval_id)


def list_pending(workspace_id=None):
    data = _read_all().values()
    out = [a for a in data if a["status"] == "pending"]
    if workspace_id:
        out = [a for a in out if a["workspace_id"] == workspace_id]
    return sorted(out, key=lambda a: a["created_at"])


def list_for_workspace(workspace_id):
    return sorted(
        [a for a in _read_all().values() if a["workspace_id"] == workspace_id],
        key=lambda a: a["created_at"],
    )


def decide(approval_id, decision, decided_by):
    """decision is 'approved' or 'rejected'. Returns the updated record,
    or None if the id doesn't exist or was already decided (idempotency
    guard — a double-click on Approve can't flip a rejected approval back
    to approved). Writes an audit_log entry on every real decision."""
    if decision not in ("approved", "rejected"):
        raise ValueError("decision must be 'approved' or 'rejected'")
    with _LOCK:
        data = _read_all()
        approval = data.get(approval_id)
        if approval is None or approval["status"] != "pending":
            return None
        approval["status"] = decision
        approval["decided_by"] = decided_by
        approval["decided_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        _write_all(data)
    audit_log.record(
        f"approval_{decision}", decided_by, workspace_id=approval["workspace_id"],
        details={"approval_id": approval_id, "title": approval["title"]},
    )
    return approval


if __name__ == "__main__":
    a = create_approval("ws_test", "Escalate Acme Corp", "Escalate to renewal specialist", "kowsick")
    print(decide(a["id"], "approved", "kowsick"))
