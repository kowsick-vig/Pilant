"""
roles.py — added 2026-08-29 to make `role` load-bearing.

users.py has always carried a `role` field, but nothing in the app ever
branched on its VALUE — it was a display label, not an enforcement
mechanism (see users.py's own module docstring: enforcement there is
`label_scope`, a different, row-level concept). This file is what the
workspace-generation rewrite needs instead: a small, fixed set of demo
business roles (independent from users.py's login accounts — see
workspace_site.py's role switcher, which lets ANY logged-in user preview
the workspace as if they were each of these roles, matching the brief's
"demonstration role switcher") where the role itself determines:

  - which Primitive TYPES are even visible (an Executive doesn't get
    row-level CustomerCard/MessageCard detail, only aggregates)
  - which ACTIONS are available (Escalate/Approve are manager+ only)
  - whether the role can decide a pending approval at all
  - how much of the underlying data is in scope (personal/team/org)

Enforcement lives in filter_primitives_for_role() and filter_actions(),
both called from workspace_engine.py BEFORE a workspace is handed to the
renderer — same "never in context to begin with" guarantee users.py's
scope_issues() already established, just keyed on role instead of label.
"""

ROLES = {
    "sales_rep": {
        "key": "sales_rep",
        "label": "Sales Representative",
        "scope": "personal",
        "detail_level": "row",
        "visible_types": [
            "Metric", "PriorityList", "DataTable", "CustomerCard", "MessageCard",
            "Timeline", "TaskQueue", "Alert", "Chart", "ActionPanel", "Form", "ActivityLog",
        ],
        "allowed_actions": ["view_account", "draft_response", "assign_follow_up"],
        "can_approve": False,
    },
    "sales_manager": {
        "key": "sales_manager",
        "label": "Sales Manager",
        "scope": "team",
        "detail_level": "row",
        "visible_types": [
            "Metric", "PriorityList", "DataTable", "CustomerCard", "MessageCard",
            "Timeline", "TaskQueue", "Alert", "Chart", "Form",
            "ApprovalPanel", "ActionPanel", "ActivityLog",
        ],
        "allowed_actions": ["view_account", "draft_response", "assign_follow_up", "escalate", "approve"],
        "can_approve": True,
    },
    "support_agent": {
        "key": "support_agent",
        "label": "Support Agent",
        "scope": "queue",
        "detail_level": "row",
        "visible_types": [
            "Metric", "PriorityList", "DataTable", "CustomerCard", "Timeline",
            "TaskQueue", "Alert", "ApprovalPanel", "ActionPanel", "Form", "ActivityLog",
        ],
        "allowed_actions": ["view_account", "resolve", "assign_follow_up", "escalate"],
        "can_approve": False,
    },
    "operations_manager": {
        "key": "operations_manager",
        "label": "Operations Manager",
        "scope": "org",
        "detail_level": "row",
        "visible_types": [
            "Metric", "PriorityList", "DataTable", "CustomerCard", "MessageCard",
            "Timeline", "TaskQueue", "Alert", "Chart", "Form",
            "ApprovalPanel", "ActionPanel", "ActivityLog",
        ],
        "allowed_actions": ["view_account", "draft_response", "assign_follow_up", "escalate", "approve", "resolve"],
        "can_approve": True,
    },
    "executive": {
        "key": "executive",
        "label": "Executive",
        "scope": "aggregate",
        "detail_level": "aggregate",
        "visible_types": [
            "Metric", "Chart", "Alert", "PriorityList", "ApprovalPanel", "ActivityLog",
        ],
        "allowed_actions": ["view_account", "escalate", "approve"],
        "can_approve": True,
    },
}

ROLE_ORDER = ["sales_rep", "sales_manager", "support_agent", "operations_manager", "executive"]
DEFAULT_ROLE = "sales_rep"

ACTION_LABELS = {
    "view_account": "View account",
    "draft_response": "Draft response",
    "assign_follow_up": "Assign follow-up",
    "escalate": "Escalate",
    "approve": "Approve",
    "resolve": "Resolve",
}


def get_role(key):
    return ROLES.get(key) or ROLES[DEFAULT_ROLE]


def is_valid_role(key):
    return key in ROLES


def can_view_type(role_key, primitive_type):
    role = get_role(role_key)
    return primitive_type in role["visible_types"]


def filter_actions(role_key, action_keys):
    """Intersect a primitive's full action list with what this role is
    permitted to do — order preserved from the primitive's own list, not
    the role's. An action absent from the role's allowed_actions simply
    never reaches the rendered page; this is a removal, not a disabled
    button, per the brief's 'never reveal actions outside the role's
    permissions.'"""
    role = get_role(role_key)
    allowed = set(role["allowed_actions"])
    return [a for a in action_keys if a in allowed]


def roles_that_can_view(primitive_type):
    """Every role label that would keep this primitive type visible —
    used purely for the inspector's 'Permitted roles' field, not for
    enforcement (enforcement is filter_primitives_for_role, already
    applied to the CURRENT role before rendering)."""
    return [ROLES[k]["label"] for k in ROLE_ORDER if primitive_type in ROLES[k]["visible_types"]]


def filter_primitives_for_role(role_key, primitives):
    """Drop whole primitive instances whose type this role can't see at
    all, then trim each survivor's allowed_actions to what the role may
    do. Applied once, centrally, in workspace_engine.generate_workspace —
    every caller downstream (renderer, inspector, save) only ever sees an
    already-scoped workspace."""
    role = get_role(role_key)
    out = []
    for p in primitives:
        if p["type"] not in role["visible_types"]:
            continue
        p = dict(p)
        p["allowed_actions"] = [
            a for a in p.get("allowed_actions", [])
            if a["key"] in role["allowed_actions"]
        ]
        out.append(p)
    return out
