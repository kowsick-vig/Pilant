"""Two independent, always-re-checked gates for every agent tool call:

  1. Permission -- does this user's role grant the tool's required permission?
  2. Approval   -- does this tool's risk tier require a human decision first?

Both are re-evaluated immediately before execution (workflow_executor.py),
never trusted from an earlier planning or approval step -- a role or a
connection can change in between.

v1 policy, chosen deliberately over a role-based auto-approve matrix (no
such matrix has been defined yet): every account role can read and act on
its own sample/connected data, and EVERY write or message action always
requires a human decision, with no exceptions for role. Tightening this
later (e.g. letting a manager role skip approval for low-risk comments) is
a one-line change to PERMISSIONS_BY_ROLE / requires_approval -- the
enforcement points that call into this module don't need to change.
"""
from tool_registry import get_tool

# Every existing account role (workspace_api.ROLES) gets the same
# permissions today -- there is no team/manager hierarchy in this app to
# restrict against yet. The check is still real and executed on every
# call; it simply hasn't been asked to draw a distinction yet.
PERMISSIONS_BY_ROLE = {
    'Product & engineering': {'jira.read', 'jira.write', 'slack.write', 'gmail.write'},
    'Customer support': {'jira.read', 'jira.write', 'slack.write', 'gmail.write'},
    'Operations': {'jira.read', 'jira.write', 'slack.write', 'gmail.write'},
    'Leadership': {'jira.read', 'jira.write', 'slack.write', 'gmail.write'},
}


def has_permission(role, tool_name):
    tool = get_tool(tool_name)
    return tool['permission'] in PERMISSIONS_BY_ROLE.get(role, set())


def requires_approval(tool_name):
    return get_tool(tool_name)['risk'] != 'read'
