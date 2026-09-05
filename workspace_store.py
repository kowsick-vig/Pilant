"""
workspace_store.py — added 2026-08-29. Save/reopen/rename/regenerate for
generated workspaces, with real version history — the one place this
needed a genuinely new module rather than extending saved_views.py (see
that file's docstring): saved_views.py is flat and append-only-of-new-
views, with no concept of "this same view, an earlier version of it."

Same JSON-file + threading.Lock + per-username ownership-filter idioms as
saved_views.py/approvals.py. A stored workspace is:

    {
        "id": str,                 # == the live workspace_id used for
                                    # approvals.py/audit_log.py records, so
                                    # a saved workspace's approval/audit
                                    # trail is never orphaned by saving
        "name": str,
        "owner": username,
        "created_at": iso8601,
        "current_version": int,    # 1-based
        "versions": [
            {"version": int, "goal": str, "role": str, "title": str, "created_at": iso8601}
        ],
    }

Like saved_views.py, a version stores WHAT TO RE-RUN (goal + role), not a
frozen render — reopening/restoring always calls
workspace_engine.generate_workspace() again, so Gmail data is always
live.
"""

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

_STORE_PATH = Path(__file__).parent / "workspace_store.json"
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


def list_workspaces(username):
    with _LOCK:
        data = _read_all()
    return data.get(username, [])


def get_workspace(username, workspace_id):
    for w in list_workspaces(username):
        if w["id"] == workspace_id:
            return w
    return None


def save_workspace(username, workspace_id, name, goal, role, title):
    """Create a new stored workspace at version 1, or — if workspace_id
    already belongs to this user — append a new version to it instead
    (this is what 'Save' after a role switch/regenerate does: the same
    live id, one more version, not a duplicate workspace)."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _LOCK:
        data = _read_all()
        existing = next((w for w in data.get(username, []) if w["id"] == workspace_id), None)
        if existing:
            next_version = len(existing["versions"]) + 1
            existing["versions"].append({"version": next_version, "goal": goal, "role": role, "title": title, "created_at": now})
            existing["current_version"] = next_version
            if name:
                existing["name"] = name
            _write_all(data)
            return existing
        record = {
            "id": workspace_id, "name": (name or "").strip() or title or "Untitled workspace",
            "owner": username, "created_at": now, "current_version": 1,
            "versions": [{"version": 1, "goal": goal, "role": role, "title": title, "created_at": now}],
        }
        data.setdefault(username, []).append(record)
        _write_all(data)
    return record


def rename_workspace(username, workspace_id, new_name):
    new_name = (new_name or "").strip()
    if not new_name:
        return False
    with _LOCK:
        data = _read_all()
        for w in data.get(username, []):
            if w["id"] == workspace_id:
                w["name"] = new_name
                _write_all(data)
                return True
    return False


def restore_version(username, workspace_id, version_number):
    """Point current_version back at an earlier version. Returns that
    version's {goal, role, title} dict (what the caller re-runs through
    workspace_engine.generate_workspace), or None if not found."""
    with _LOCK:
        data = _read_all()
        for w in data.get(username, []):
            if w["id"] != workspace_id:
                continue
            for v in w["versions"]:
                if v["version"] == version_number:
                    w["current_version"] = version_number
                    _write_all(data)
                    return v
    return None


def current_version_of(username, workspace_id):
    w = get_workspace(username, workspace_id)
    if not w:
        return None
    return next((v for v in w["versions"] if v["version"] == w["current_version"]), None)
