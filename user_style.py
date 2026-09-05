"""
user_style.py — added 2026-08-26 to close a real gap called out live in
this session: the Composer (agent_composer.py) genuinely composes a
screen's LAYOUT per request, bound to real data — but nothing about that
composition depended on WHO was asking. Two different users typing the
identical request with the identical primitives selected got the
identical screen. That's not what dynamisos.in's site actually claims
("my email client looks more like a task list, and a student's looks more
like an events calendar") — that's the same primitives producing
DIFFERENT FORM per person, not just different DATA (which
users.py/rag_scope.py's identity scoping already handled).

This module is the missing piece: a per-user, persistent, plain-language
description of how THAT person wants their interface shaped — set once
(or updated any time) on the Composer page, then read on every single
compose/reopen and folded into agent_composer._build_system() as a real
instruction the model has to actually honor, not decoration. Two users
with different style notes, given the identical primitives and identical
underlying data, should end up with visibly different compositions — one
might get a compact list, another a stat_grid up top with a panel below —
because the model was actually told how each of them likes it, not
because their data differs.

Deliberately NOT an inferred/ML-driven preference model — that's a much
bigger, fuzzier undertaking (silently guessing "how someone works" from
behavior) that doesn't belong in a prototype meant to prove out the
mechanic honestly. This is explicit: the person states their own
preference in their own words, same trust model as any other user-facing
setting.

Storage mirrors saved_views.py exactly on purpose — a single JSON file on
disk (user_style.json), keyed by username, guarded by a process-wide lock
for the same Flask-dev-server-is-multithreaded reason saved_views.py's own
docstring explains. Not a new persistence pattern, just the same one
already proven out here, applied to a new small piece of state.
"""

import json
import threading
from pathlib import Path

_STORE_PATH = Path(__file__).parent / "user_style.json"
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


def get_style(username):
    """This user's stored style note, or "" if they've never set one —
    empty string, not None, so every caller can treat it as
    truthy/falsy-and-safe-to-interpolate without a None check."""
    with _LOCK:
        data = _read_all()
    return data.get(username, "")


def set_style(username, text):
    """Overwrites (not appends to) this user's style note — the settings
    box on the Composer page always shows the CURRENT full value and edits
    it in place, so overwrite is the correct semantic, same as any other
    single-value settings field."""
    with _LOCK:
        data = _read_all()
        data[username] = (text or "").strip()
        _write_all(data)
