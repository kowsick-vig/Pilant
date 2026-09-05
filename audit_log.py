"""
audit_log.py — added 2026-08-29. An append-only event log, same JSON-file
+ threading.Lock persistence idiom as saved_views.py (see that file's
module docstring). Nothing in this app has ever needed to remember "who
did what, when" before now — approvals are the first feature where that
matters, so this exists specifically to back approvals.py's decision
trail, though it's written generically enough (event_type/actor/details)
that anything else could log to it later.

Append-only on purpose: entries are never edited or deleted through this
module's API (there's no update_entry/delete_entry function at all) — an
audit trail that could be quietly rewritten wouldn't be one.
"""

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

_STORE_PATH = Path(__file__).parent / "audit_log.json"
_LOCK = threading.Lock()


def _read_all():
    if not _STORE_PATH.exists():
        return []
    try:
        return json.loads(_STORE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def _write_all(entries):
    _STORE_PATH.write_text(json.dumps(entries, indent=2))


def record(event_type, actor, workspace_id=None, details=None):
    """Append one entry. `event_type` is a short machine key (e.g.
    'approval_approved', 'workspace_saved', 'workspace_restored');
    `actor` is the acting username; `details` is a small free-form dict
    of whatever's relevant (approval title, version number, etc.) —
    kept as data, never interpolated into anything executable."""
    entry = {
        "id": uuid.uuid4().hex[:12],
        "event_type": event_type,
        "actor": actor,
        "workspace_id": workspace_id,
        "details": details or {},
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    with _LOCK:
        entries = _read_all()
        entries.append(entry)
        _write_all(entries)
    return entry


def list_for_workspace(workspace_id, limit=100):
    entries = [e for e in _read_all() if e.get("workspace_id") == workspace_id]
    return list(reversed(entries))[:limit]


def list_recent(limit=200):
    return list(reversed(_read_all()))[:limit]


if __name__ == "__main__":
    print(record("test_event", "kowsick", details={"note": "manual check"}))
