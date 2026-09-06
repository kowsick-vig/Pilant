"""
A fourth STATIC/dummy connector, same spirit as connectors_helpdesk.py: no
network call, no external dependency, ISSUES below is just a Python list
baked into this file. Added 2026-08-31 at the user's request for "fake Jira
with a crowded interface" — the point of this one specifically is that real
Jira boards are famously dense: every issue carries a project, type, status,
priority, assignee, reporter, sprint, epic link, story points, labels,
components, fix version, due date, and watcher/comment counts, all at once.
That clutter is the whole point here — it's the "before" a person is
drowning in, so that agent_jira.py rendering a clean, request-scoped screen
from it is a visible, honest demonstration of what Pilant is actually for,
not a marketing claim.

Added 2026-09-01: get_issue()/set_issue_status() give this connector a
real (if in-memory/ephemeral) write path — a single issue lookup and a
"mark resolved" mutation — for Studio's real Jira detail panel. Still no
network call; the write lands in the same ISSUES list get_issues() already
reads from, so it's genuinely reflected everywhere, not simulated.

Same domain-independence proof as every other mock connector (retail
orders, support tickets): a fictional two-project engineering org instead
of GitHub issues or support tickets. Nothing about agent_jira.py's shape
changes because of that — same schema, same guardrails, same conversational
ask_user flow as agent_helpdesk.py.
"""

import datetime

ISSUES = [
    {"key": "ENG-482", "project": "ENG", "type": "Bug", "summary": "Checkout fails on Safari when applying a promo code twice", "status": "In Progress", "priority": "Highest", "assignee": "Priya Nair", "reporter": "Marcus Webb", "sprint": "Sprint 24", "epic": "Checkout Reliability", "story_points": 5, "labels": ["safari", "payments", "regression"], "components": ["checkout"], "fix_version": "3.4.0", "due": "2026-09-02", "created": "2026-08-25", "updated": "2026-08-30", "watchers": 9, "comments": 14},
    {"key": "ENG-481", "project": "ENG", "type": "Story", "summary": "Add retry with backoff to the webhook delivery worker", "status": "To Do", "priority": "Medium", "assignee": "Devon Ashe", "reporter": "Priya Nair", "sprint": "Sprint 24", "epic": "Platform Hardening", "story_points": 8, "labels": ["infra", "webhooks"], "components": ["platform"], "fix_version": "3.5.0", "due": "2026-09-10", "created": "2026-08-24", "updated": "2026-08-27", "watchers": 3, "comments": 2},
    {"key": "ENG-479", "project": "ENG", "type": "Bug", "summary": "Memory leak in the nightly export job after 3.3.0 upgrade", "status": "Blocked", "priority": "Highest", "assignee": "Julia Ferris", "reporter": "Noah Kim", "sprint": "Sprint 24", "epic": "Platform Hardening", "story_points": 5, "labels": ["memory", "export", "prod-incident"], "components": ["batch-jobs"], "fix_version": "3.4.1", "due": "2026-09-01", "created": "2026-08-22", "updated": "2026-08-31", "watchers": 15, "comments": 22},
    {"key": "ENG-477", "project": "ENG", "type": "Task", "summary": "Rotate the staging database credentials", "status": "Done", "priority": "Low", "assignee": "Devon Ashe", "reporter": "Devon Ashe", "sprint": "Sprint 23", "epic": "Platform Hardening", "story_points": 1, "labels": ["security"], "components": ["infra"], "fix_version": "3.4.0", "due": "2026-08-20", "created": "2026-08-15", "updated": "2026-08-19", "watchers": 1, "comments": 0},
    {"key": "ENG-475", "project": "ENG", "type": "Story", "summary": "Let admins bulk-export audit logs as CSV", "status": "In Review", "priority": "Medium", "assignee": "Julia Ferris", "reporter": "Sara Kwon", "sprint": "Sprint 24", "epic": "Admin Tools", "story_points": 3, "labels": ["admin", "export"], "components": ["admin-console"], "fix_version": "3.5.0", "due": "2026-09-05", "created": "2026-08-21", "updated": "2026-08-29", "watchers": 4, "comments": 6},
    {"key": "ENG-473", "project": "ENG", "type": "Bug", "summary": "Date picker shows wrong week start for UK locale", "status": "To Do", "priority": "Low", "assignee": None, "reporter": "Amy Castillo", "sprint": None, "epic": "Admin Tools", "story_points": 2, "labels": ["i18n", "ui"], "components": ["admin-console"], "fix_version": None, "due": None, "created": "2026-08-19", "updated": "2026-08-19", "watchers": 2, "comments": 1},
    {"key": "ENG-470", "project": "ENG", "type": "Epic", "summary": "Checkout Reliability", "status": "In Progress", "priority": "High", "assignee": "Priya Nair", "reporter": "Priya Nair", "sprint": None, "epic": None, "story_points": None, "labels": ["checkout"], "components": ["checkout"], "fix_version": "3.4.0", "due": "2026-09-15", "created": "2026-08-01", "updated": "2026-08-30", "watchers": 11, "comments": 8},
    {"key": "ENG-468", "project": "ENG", "type": "Sub-task", "summary": "Write regression test for the double-promo-code bug", "status": "In Progress", "priority": "Highest", "assignee": "Priya Nair", "reporter": "Priya Nair", "sprint": "Sprint 24", "epic": "Checkout Reliability", "story_points": 1, "labels": ["testing"], "components": ["checkout"], "fix_version": "3.4.0", "due": "2026-09-02", "created": "2026-08-26", "updated": "2026-08-30", "watchers": 2, "comments": 3},
    {"key": "DES-114", "project": "DES", "type": "Story", "summary": "Redesign the empty-state illustration for the reports tab", "status": "To Do", "priority": "Low", "assignee": "Amy Castillo", "reporter": "Sara Kwon", "sprint": "Sprint 24", "epic": "Reports Revamp", "story_points": 2, "labels": ["design", "illustration"], "components": ["reports-ui"], "fix_version": "3.5.0", "due": "2026-09-08", "created": "2026-08-23", "updated": "2026-08-25", "watchers": 1, "comments": 0},
    {"key": "DES-112", "project": "DES", "type": "Bug", "summary": "Chart legend overlaps data on narrow viewports", "status": "In Progress", "priority": "Medium", "assignee": "Amy Castillo", "reporter": "Noah Kim", "sprint": "Sprint 24", "epic": "Reports Revamp", "story_points": 3, "labels": ["responsive", "charts"], "components": ["reports-ui"], "fix_version": "3.5.0", "due": "2026-09-06", "created": "2026-08-22", "updated": "2026-08-28", "watchers": 3, "comments": 5},
    {"key": "DES-110", "project": "DES", "type": "Task", "summary": "Update the design system's button spacing tokens", "status": "Done", "priority": "Lowest", "assignee": "Sara Kwon", "reporter": "Sara Kwon", "sprint": "Sprint 23", "epic": "Design System v3", "story_points": 1, "labels": ["design-system"], "components": ["design-tokens"], "fix_version": "3.4.0", "due": "2026-08-18", "created": "2026-08-10", "updated": "2026-08-17", "watchers": 0, "comments": 0},
    {"key": "DES-108", "project": "DES", "type": "Bug", "summary": "Focus ring missing on the sidebar nav in dark mode", "status": "Blocked", "priority": "High", "assignee": "Sara Kwon", "reporter": "Devon Ashe", "sprint": "Sprint 24", "epic": "Design System v3", "story_points": 2, "labels": ["accessibility", "dark-mode"], "components": ["design-tokens"], "fix_version": "3.4.1", "due": "2026-09-03", "created": "2026-08-20", "updated": "2026-08-30", "watchers": 6, "comments": 9},
    {"key": "DES-105", "project": "DES", "type": "Story", "summary": "Add keyboard shortcuts for the command palette", "status": "In Review", "priority": "Medium", "assignee": "Noah Kim", "reporter": "Julia Ferris", "sprint": "Sprint 24", "epic": "Reports Revamp", "story_points": 5, "labels": ["a11y", "power-users"], "components": ["reports-ui"], "fix_version": "3.5.0", "due": "2026-09-07", "created": "2026-08-18", "updated": "2026-08-29", "watchers": 5, "comments": 7},
    {"key": "DES-101", "project": "DES", "type": "Epic", "summary": "Design System v3", "status": "In Progress", "priority": "High", "assignee": "Sara Kwon", "reporter": "Sara Kwon", "sprint": None, "epic": None, "story_points": None, "labels": ["design-system"], "components": ["design-tokens"], "fix_version": "3.5.0", "due": "2026-09-30", "created": "2026-07-28", "updated": "2026-08-30", "watchers": 8, "comments": 4},
    {"key": "ENG-464", "project": "ENG", "type": "Bug", "summary": "Race condition duplicates invoice emails under high load", "status": "Done", "priority": "Highest", "assignee": "Julia Ferris", "reporter": "Marcus Webb", "sprint": "Sprint 23", "epic": "Platform Hardening", "story_points": 5, "labels": ["billing", "prod-incident"], "components": ["billing"], "fix_version": "3.4.0", "due": "2026-08-16", "created": "2026-08-08", "updated": "2026-08-16", "watchers": 12, "comments": 19},
    # Added 2026-09-06 at the user's request ("if you don't have in jira
    # update the jira") — backlog tracking for the parts of the big
    # LAYOUT/NAVIGATION/DISPLAY/INPUT/ACTION/OVERLAY/STATE component
    # vocabulary list that Pilant's renderer/schema don't support yet (see
    # skills/ui_composition.md and schema.py's UI_SCHEMA for what IS
    # built). One epic plus three stories, split along the two real
    # architectural blockers identified when that list was triaged — a
    # nested/composable layout system, and connectors gaining a real
    # write-capable backend — rather than one ticket per individual type.
    {"key": "DES-120", "project": "DES", "type": "Epic", "summary": "Component Vocabulary Expansion — the remaining Pilant Studio component types", "status": "To Do", "priority": "Medium", "assignee": None, "reporter": "Sara Kwon", "sprint": None, "epic": None, "story_points": None, "labels": ["component-vocabulary", "design-system"], "components": ["design-tokens"], "fix_version": None, "due": None, "created": "2026-09-06", "updated": "2026-09-06", "watchers": 1, "comments": 0},
    {"key": "DES-121", "project": "DES", "type": "Story", "summary": "Nested/composable layout system — blocks page, section, stack, grid, split_view, tabs, modal, drawer", "status": "To Do", "priority": "Medium", "assignee": None, "reporter": "Sara Kwon", "sprint": None, "epic": "Component Vocabulary Expansion", "story_points": 8, "labels": ["architecture", "layout"], "components": ["schema"], "fix_version": None, "due": None, "created": "2026-09-06", "updated": "2026-09-06", "watchers": 1, "comments": 0},
    {"key": "DES-122", "project": "DES", "type": "Story", "summary": "Write-capable connector actions — blocks form/text_input/textarea/select/multi_select/checkbox/toggle/date_picker/search/filter_bar/file_upload, button/dropdown_menu/row_actions/bulk_actions, toast/confirmation", "status": "To Do", "priority": "Medium", "assignee": None, "reporter": "Sara Kwon", "sprint": None, "epic": "Component Vocabulary Expansion", "story_points": 13, "labels": ["architecture", "backend"], "components": ["connectors"], "fix_version": None, "due": None, "created": "2026-09-06", "updated": "2026-09-06", "watchers": 1, "comments": 0},
    {"key": "DES-123", "project": "DES", "type": "Story", "summary": "Remaining standalone types not yet built — sidebar_nav, breadcrumbs, standalone tooltip, loading, progress", "status": "To Do", "priority": "Low", "assignee": None, "reporter": "Sara Kwon", "sprint": None, "epic": "Component Vocabulary Expansion", "story_points": 3, "labels": ["nice-to-have"], "components": ["renderer"], "fix_version": None, "due": None, "created": "2026-09-06", "updated": "2026-09-06", "watchers": 1, "comments": 0},
]


def get_issues(project=None, status=None, priority=None, issue_type=None, sprint=None, assignee=None, label=None, **_extra):
    """
    Fetch Jira-style issues, optionally filtered by project (ENG/DES),
    status (To Do/In Progress/In Review/Blocked/Done — pass either a single
    string or a list/tuple of strings to match ANY of several statuses in
    one call, e.g. ["To Do", "In Progress", "Blocked"] for a broad "not
    done yet" request; added 2026-09-06 mirroring priority's existing
    array support below, after a real request needing several statuses at
    once — "what's not done" — was observed making the model invent a
    malformed JSON-stringified array in this argument instead, silently
    matching zero issues since that string never equals any real status),
    priority (Lowest/Low/Medium/High/Highest — pass either a single string
    or a list/tuple of strings to match ANY of several priorities in one
    call, e.g. ["High", "Highest"] for a broad "urgent"/"high priority"
    request), issue_type (Story/Bug/Task/Epic/Sub-task), sprint (e.g.
    "Sprint 24" — pass "backlog" to match issues with no sprint), assignee
    (matches on name, case-insensitive substring; pass "unassigned" to
    match issues with no assignee), and/or label (matches if the label is
    anywhere in the issue's labels list). Returns a copy of each matching
    issue — callers can't accidentally mutate ISSUES.

    **_extra: silently absorbs and ignores any other keyword the model
    passes that isn't a real filter here (seen live: a model reaching for
    a 'limit' argument on a "compact list" request, which this tool never
    defined) — added 2026-09-01 after that exact call crashed with an
    "unexpected keyword argument" TypeError instead of just returning
    results. Better to ignore an extra param a model invents than to fail
    the whole request over it; the model still gets every real issue back
    and can trim what it shows on the rendered screen itself.
    """
    results = ISSUES
    if project:
        results = [i for i in results if i["project"].lower() == project.lower()]
    if status:
        wanted_status = {status.lower()} if isinstance(status, str) else {s.lower() for s in status}
        results = [i for i in results if i["status"].lower() in wanted_status]
    if priority:
        wanted = {priority.lower()} if isinstance(priority, str) else {p.lower() for p in priority}
        results = [i for i in results if i["priority"].lower() in wanted]
    if issue_type:
        results = [i for i in results if i["type"].lower() == issue_type.lower()]
    if sprint:
        if sprint.lower() == "backlog":
            results = [i for i in results if not i["sprint"]]
        else:
            results = [i for i in results if (i["sprint"] or "").lower() == sprint.lower()]
    if assignee:
        if assignee.lower() == "unassigned":
            results = [i for i in results if not i["assignee"]]
        else:
            results = [i for i in results if i["assignee"] and assignee.lower() in i["assignee"].lower()]
    if label:
        results = [i for i in results if label.lower() in [lbl.lower() for lbl in i["labels"]]]
    return [dict(i) for i in results]


def get_issue(key):
    """Fetch ONE issue by its exact key (e.g. "ENG-482"), case-insensitive.
    Returns a copy (same no-accidental-mutation guarantee as get_issues), or
    None if no issue with that key exists. Added 2026-09-01 for Studio's
    real Jira detail panel (studio.py's _jira_detail_panel_html) — clicking
    a row needs its ONE full issue back, not a filtered list."""
    for i in ISSUES:
        if i["key"].lower() == (key or "").strip().lower():
            return dict(i)
    return None


VALID_STATUSES = ["To Do", "In Progress", "In Review", "Blocked", "Done"]


def set_issue_status(key, new_status):
    """Mutates the matching issue's status IN PLACE in the module-level
    ISSUES list (and stamps 'updated' to today) — added 2026-09-01 for the
    detail panel's real "Mark resolved" action. This is a genuine write:
    every subsequent get_issues()/get_issue() call reflects it, in this
    process's lifetime (in-memory, like every other mock connector's state
    in this project — see connectors_helpdesk.py for the same pattern).
    Not a themed button with nothing behind it. Returns the updated issue
    (a copy), or None if no issue with that key exists. Raises ValueError
    for a status outside VALID_STATUSES, so a typo/bad value fails loudly
    instead of silently writing garbage."""
    if new_status not in VALID_STATUSES:
        raise ValueError(f"Unknown status {new_status!r} — must be one of {VALID_STATUSES}")
    key_lower = (key or "").strip().lower()
    for i in ISSUES:
        if i["key"].lower() == key_lower:
            i["status"] = new_status
            i["updated"] = datetime.date.today().isoformat()
            return dict(i)
    return None


if __name__ == "__main__":
    # Quick manual check: python3 connectors_jira.py
    import json
    print(json.dumps(get_issues(), indent=2))
