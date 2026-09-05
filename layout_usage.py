"""
layout_usage.py — added 2026-08-30, answering the user's question directly:
"does Pilant learn which interface layouts, information and actions are
most useful for different roles and goals?" Before this file the honest
answer was no — see roles.py (a fixed, hand-written rule table for what a
role can SEE, not a record of what turned out useful), user_style.py (an
explicit, person-typed preference, its own docstring says outright
"Deliberately NOT an inferred/ML-driven preference model"), and
saved_views.py (a manual bookmark, not an observed pattern). Nothing
anywhere recorded what actually got built and kept versus discarded, per
role, per connector.

This module is the lightweight version of that missing feedback loop —
deliberately lightweight, matching what was scoped with the user: not a
real ML system, not silent behavioral inference about individuals. It's a
plain, inspectable event log (record_render, append-only, same JSON-file +
threading.Lock idiom as audit_log.py/saved_views.py/user_style.py) plus a
frequency summary over it (summarize_usage) that turns into one sentence
folded into the relevant connector's system prompt for the NEXT request —
the same mechanism user_style.py already proved out for an individual's
stated preference, just computed from role-level history instead of typed
by a person. Two things keep this honest:

  1. It's role-level, not per-person. This logs which SHAPE (stat_grid vs
     list vs panel vs suggestions) a role's requests tend to end up using
     for a given connector — not what any one named individual clicked,
     dwelt on, or asked for. There's no per-user profile here to creep
     into being a surveillance log.
  2. The hint it produces is explicitly advisory, worded the same way
     every time (see _hint_sentence): "lean toward X, never override what
     was actually asked." It's folded into the system prompt as one more
     signal the model weighs, not a rule that silently reshapes output
     regardless of the request — same posture user_style.py's own
     preference instruction takes.

What's logged: every time a connector's render_view call is ACCEPTED (past
guardrails — see each agent_*.py's dispatch) for a workflow in studio.py,
_run_agent_for_workflow() calls record_render() with the signed-in user's
role (users.py), the connector key, the request text, and the ordered list
of component "type"s the accepted view actually used. Nothing about the
underlying DATA is stored — no email content, no ticket text, no customer
names — just the shape of the screen and the connector/role/request text
that produced it, which is what "which layout is most useful" actually
needs.

Capped at MAX_EVENTS most-recent entries (oldest trimmed first) — a
role's most RECENT pattern is what should drive the hint, not an
ever-growing file that gives 2026-08-30's one experimental request the
same weight forever.
"""

import json
import threading
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

_STORE_PATH = Path(__file__).parent / "layout_usage.json"
_LOCK = threading.Lock()

MAX_EVENTS = 500

# Below this many matching (role, connector) events, summarize_usage()
# returns None rather than a hint — a pattern from 1-2 requests is noise,
# not a real signal, and a hint built from noise would just be theater
# (same "never offer something that isn't real" principle studio.py's own
# module docstring already applies to fake connect buttons).
MIN_EVENTS_FOR_HINT = 5

# Of the events that qualify, only mention a shape in the hint if it shows
# up at least this often — otherwise a role that's genuinely inconsistent
# (no real pattern yet) would get a hint implying more confidence than the
# data supports.
MIN_SHARE_FOR_MENTION = 0.34


def _read_all():
    if not _STORE_PATH.exists():
        return []
    try:
        return json.loads(_STORE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def _write_all(events):
    _STORE_PATH.write_text(json.dumps(events, indent=2))


def record_render(role, connector, request_text, component_types):
    """Append one usage event. `component_types` is the ordered list of
    `type` values from the accepted render_view's `components` (e.g.
    ["stat_grid", "list"]) — see this module's docstring for exactly what
    is and isn't stored. Safe to call with role=None (falls back to
    "unknown" rather than raising) so a caller that can't resolve a role
    yet doesn't have to guard every call site."""
    event = {
        "role": role or "unknown",
        "connector": connector,
        "request_text": (request_text or "")[:200],
        "component_types": [t for t in (component_types or []) if isinstance(t, str)],
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    with _LOCK:
        events = _read_all()
        events.append(event)
        if len(events) > MAX_EVENTS:
            events = events[-MAX_EVENTS:]
        _write_all(events)
    return event


def _hint_sentence(role, top_shapes):
    shapes = ", ".join(top_shapes)
    return (
        f'Observed pattern, not a rule: past "{role}" requests on this connector most often '
        f"ended up using {shapes}. When this request is genuinely open about how to lay things "
        "out, leaning toward one of those shapes is a reasonable default — but never let this "
        "override what THIS request actually asks for, and never let it justify omitting real "
        "data or padding the screen with anything unrequested."
    )


def summarize_usage(role, connector):
    """Returns a one-sentence hint string to append to that connector's
    system prompt for this role's next request, or None when there isn't
    enough history yet to say anything real (see MIN_EVENTS_FOR_HINT).
    Deliberately recomputed fresh on every call (same reasoning as
    agent_composer.py's _build_system building fresh per request) rather
    than cached — cheap over at most MAX_EVENTS rows, and always reflects
    the latest pattern rather than a stale snapshot."""
    if not role or not connector:
        return None
    matching = [
        e for e in _read_all()
        if e.get("role") == role and e.get("connector") == connector
    ]
    if len(matching) < MIN_EVENTS_FOR_HINT:
        return None
    counts = Counter()
    for e in matching:
        # Count each shape once per render, not once per row — a screen
        # with three stat_grids shouldn't outweigh three separate screens
        # that each used a different shape once.
        counts.update(set(e.get("component_types") or []))
    total = len(matching)
    top = [
        shape for shape, n in counts.most_common(3)
        if (n / total) >= MIN_SHARE_FOR_MENTION
    ]
    if not top:
        return None
    return _hint_sentence(role, top)


def stats_for(role=None, connector=None):
    """Small inspection helper (used by tests and available for a future
    'why did it suggest that' admin view) — returns {"total": n,
    "by_shape": {...}} over events matching the given filters (either may
    be None to mean "any")."""
    matching = [
        e for e in _read_all()
        if (role is None or e.get("role") == role)
        and (connector is None or e.get("connector") == connector)
    ]
    counts = Counter()
    for e in matching:
        counts.update(set(e.get("component_types") or []))
    return {"total": len(matching), "by_shape": dict(counts)}


if __name__ == "__main__":
    print(record_render("owner", "gmail", "what unread emails do I have?", ["list"]))
    print(stats_for(role="owner", connector="gmail"))
