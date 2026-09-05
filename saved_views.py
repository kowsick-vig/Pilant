"""
Persistence for the "saved views" feature — the piece that moves Pilant
from "answer one question, then forget it" toward DynamisOS's Studio idea
of a personal screen that sticks around. A saved view is deliberately NOT
a frozen screenshot: it stores the query and who owns it, and every time
it's opened, the real Composition Engine (agent_github.run_agent) runs
again against live data. What persists is WHICH screen is "yours" — not
a stale snapshot of it. That's the same distinction Gupta's RFS draws:
users converge on a personal, standing view, not a one-off answer.

Storage is a single JSON file on disk (saved_views.json), keyed by
username — enough for a local prototype with a handful of users, and it
keeps this module free of any new dependency (no sqlite, no ORM). A
process-wide lock guards every read-modify-write so concurrent requests
from the Flask dev server (which is multi-threaded) can't corrupt the
file or silently drop an update.

Ownership enforcement follows the same pattern as users.scope_issues():
get_view() and delete_view() both take the requesting username and only
ever operate on THAT user's own views. A guessed or leaked view_id for
someone else's saved view behaves exactly like a missing one — it does
not leak whether the id exists at all, let alone what it points to.
"""

import json
import threading
import uuid
from pathlib import Path

_STORE_PATH = Path(__file__).parent / "saved_views.json"
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


def list_views(username):
    """This user's saved views, oldest first. Never another user's."""
    with _LOCK:
        data = _read_all()
    return data.get(username, [])


def get_view(username, view_id):
    """
    A single saved view — but ONLY if it exists AND belongs to this
    user. The trust boundary: a view_id belonging to someone else
    returns None, identical to a view_id that was never saved at all.
    """
    for v in list_views(username):
        if v["id"] == view_id:
            return v
    return None


def save_view(username, name, query, primitive_ids=None):
    """`primitive_ids` (added 2026-08-26 for agent_composer.py's Composer)
    is OPTIONAL and additive — server.py's existing calls (positional,
    3-arg) are untouched, and get a view with no "primitive_ids" key at
    all, exactly as before. When given, it's a cross-connector Composer
    canvas selection (a list of primitives.py registry ids) that
    /composer/open/<id> re-passes to agent_composer.run_agent on every
    reopen, alongside `query` as the free-text instruction — the same
    "store what to re-run, not a snapshot of the result" philosophy this
    module's docstring already describes, just carrying one more field."""
    view = {"id": uuid.uuid4().hex[:12], "name": (name or "").strip() or query or "Untitled", "query": query}
    if primitive_ids:
        view["primitive_ids"] = list(primitive_ids)
    with _LOCK:
        data = _read_all()
        data.setdefault(username, []).append(view)
        _write_all(data)
    return view


def delete_view(username, view_id):
    """
    Removes the view only if it belongs to this user. Same enforcement
    as get_view() — a view_id for someone else's saved view is simply
    not found under this username's own list, so nothing happens.
    """
    with _LOCK:
        data = _read_all()
        views = data.get(username, [])
        data[username] = [v for v in views if v["id"] != view_id]
        _write_all(data)


def save_layout(username, view_id, order, total):
    """
    Stores the user's manually-reshaped layout for one saved view: the
    order they arranged the remaining components in, plus how many
    components existed in total when they reshaped. That "total" is what
    lets apply_layout() later tell "the user deliberately removed this"
    apart from "the model just generated a different number of
    components this time" — see apply_layout()'s docstring. Same
    ownership enforcement as delete_view(): a view_id belonging to
    someone else is a silent no-op, never an error that would confirm
    whether it exists.
    """
    clean_order = [i for i in order if isinstance(i, int) and i >= 0]
    with _LOCK:
        data = _read_all()
        for v in data.get(username, []):
            if v["id"] == view_id:
                v["layout"] = {"order": clean_order, "total": total}
                _write_all(data)
                return True
    return False


def apply_layout(components, layout):
    """
    Reorders/filters a freshly-generated components list according to a
    saved layout. Applied by POSITION, not by content — the agent
    composes a fresh components list from scratch on every open, so
    there's no stable identity to match a reshape against across
    regenerations.

    Two cases:
    - The regeneration returned exactly as many components as existed
      when the layout was saved (layout["total"] == len(components)):
      the saved order is authoritative, INCLUDING removal — an index the
      user didn't include is treated as deliberately hidden.
    - The count differs (the underlying data genuinely changed shape
      since the reshape): there's no reliable way to tell "removed on
      purpose" apart from "this index doesn't correspond to the same
      thing anymore," so anything not in the saved order is appended
      rather than hidden — erring toward never silently hiding real
      content the model is trying to show, at the cost of a removed item
      possibly reappearing if the data shape shifts.
    """
    if not layout or not layout.get("order"):
        return components

    order = layout["order"]
    n = len(components)
    seen = set()
    ordered = []
    for i in order:
        if isinstance(i, int) and 0 <= i < n and i not in seen:
            ordered.append(components[i])
            seen.add(i)

    if layout.get("total") == n:
        return ordered  # exact match: saved order is authoritative, removals included

    for i in range(n):
        if i not in seen:
            ordered.append(components[i])
    return ordered
