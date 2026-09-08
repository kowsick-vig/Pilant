"""Append-only audit trail for agent workflow activity.

Every plan creation, every human approve/reject decision, and every step
execution attempt (success or failure) writes exactly one entry here:
who, what action, which tool/target, what was decided, when, against
which connected system, and what happened. Never a raw token or secret --
`result` is always a short, already-safe summary string, never the tool's
raw output. There is deliberately no update/delete API: entries are
written once and read many times.
"""
from datetime import datetime, timezone

MAX_RESULT_LEN = 600


def record(store, owner, app_id, plan_id, step_id, actor, action, tool, target, decision, connected_system, result):
    entry = {
        'app': app_id, 'plan_id': plan_id, 'step_id': step_id, 'actor': actor, 'action': action,
        'tool': tool, 'target': (target or '')[:200], 'decision': decision,
        'connected_system': connected_system, 'result': (str(result)[:MAX_RESULT_LEN] if result is not None else None),
        'at': datetime.now(timezone.utc).isoformat(),
    }
    store.save_audit_entry(owner, entry)
    return entry
