"""The strict, fixed catalog of tools an agent plan may reference.

This is the hard boundary between "the model drafts an action" and "an
action actually runs": the model (in agent_planner.py) never emits code and
never invents a tool name -- every step in a stored plan is one of the keys
below, and every input is re-validated here, server-side, before Sources
ever sees it. There is no code path anywhere that executes a tool name or
an input shape this registry didn't already approve.

Each entry has:
  connected_system  -- which integration this touches (jira/slack/gmail)
  permission        -- the permission string policy_engine checks
  risk              -- 'read' | 'write' | 'message'
  approval_required -- whether a human must decide before this ever runs
                       (v1 policy: every write/message does, unconditionally
                       -- see policy_engine.requires_approval)
  schema            -- a plain description of the accepted input, surfaced
                       to the UI/tests; the real enforcement is `validate`
  validate(input)   -- raises ValueError, or returns a cleaned input dict
  execute(sources, clean_input) -- calls into workspace_sources.Sources
                       and returns a small JSON-safe result dict

Nothing here makes a network call directly; execute() always goes through
a Sources method, which is the same request-scoped, owner-bound adapter
every other read/write in this app already uses.
"""

JIRA_STATUSES = ['To Do', 'In Progress', 'In Review', 'Blocked', 'Done']
JIRA_PRIORITIES = ['Lowest', 'Low', 'Medium', 'High', 'Highest']

RISK_READ, RISK_WRITE, RISK_MESSAGE = 'read', 'write', 'message'


def _as_list(value, allowed, name):
    if value is None:
        return None
    items = value if isinstance(value, list) else [value]
    if not items or len(items) > 10 or not all(isinstance(v, str) for v in items):
        raise ValueError(f'Invalid {name}.')
    matched = []
    for v in items:
        hit = next((a for a in allowed if a.lower() == v.lower()), None)
        if not hit:
            raise ValueError(f'Unsupported {name}: {v!r}.')
        matched.append(hit)
    return matched


def _text(value, name, max_len, required=True):
    if value is None:
        value = ''
    if not isinstance(value, str):
        raise ValueError(f'{name} must be text.')
    value = value.strip()
    if required and not value:
        raise ValueError(f'{name} is required.')
    if len(value) > max_len:
        raise ValueError(f'{name} is too long (max {max_len} characters).')
    return value


def _validate_search_issues(raw):
    if not isinstance(raw, dict):
        raise ValueError('Invalid input.')
    return {
        'status': _as_list(raw.get('status'), JIRA_STATUSES, 'status'),
        'priority': _as_list(raw.get('priority'), JIRA_PRIORITIES, 'priority'),
        'assignee': _text(raw.get('assignee') or '', 'assignee', 120, required=False) or None,
    }


def _execute_search_issues(sources, clean):
    rows = sources.fetch('jira')

    def keep(row):
        d = row.get('detail') or {}
        if clean['status'] and d.get('status') not in clean['status']:
            return False
        if clean['priority'] and d.get('priority') not in clean['priority']:
            return False
        if clean['assignee'] and clean['assignee'].lower() not in (d.get('assignee') or '').lower():
            return False
        return True

    matches = [r for r in rows if keep(r)]
    issues = [{
        'key': r['id'], 'summary': r['title'], 'status': (r.get('detail') or {}).get('status'),
        'priority': (r.get('detail') or {}).get('priority'), 'assignee': (r.get('detail') or {}).get('assignee'),
        'updated': (r.get('detail') or {}).get('updated'),
        'blockedReason': (r.get('detail') or {}).get('blocked_reason'),
    } for r in matches]
    return {'issues': issues, 'total': len(issues)}


def _validate_get_issue(raw):
    if not isinstance(raw, dict):
        raise ValueError('Invalid input.')
    return {'issueKey': _text(raw.get('issueKey'), 'issueKey', 40)}


def _execute_get_issue(sources, clean):
    issue = sources.detail('jira', clean['issueKey'])
    if not issue:
        raise ValueError(f"No Jira issue found with key {clean['issueKey']}.")
    d = issue.get('detail') or {}
    return {'key': issue['id'], 'summary': issue['title'], 'status': d.get('status'),
        'priority': d.get('priority'), 'assignee': d.get('assignee'), 'updated': d.get('updated'),
        'blockedReason': d.get('blocked_reason')}


def _validate_add_comment(raw):
    if not isinstance(raw, dict):
        raise ValueError('Invalid input.')
    return {'issueKey': _text(raw.get('issueKey'), 'issueKey', 40),
        'comment': _text(raw.get('comment'), 'comment', 2000)}


def _execute_add_comment(sources, clean):
    return sources.jira_add_comment(clean['issueKey'], sources.owner, clean['comment'])


def _validate_update_assignee(raw):
    if not isinstance(raw, dict):
        raise ValueError('Invalid input.')
    return {'issueKey': _text(raw.get('issueKey'), 'issueKey', 40),
        'assignee': _text(raw.get('assignee'), 'assignee', 120)}


def _execute_update_assignee(sources, clean):
    return sources.jira_update_assignee(clean['issueKey'], clean['assignee'])


def _validate_slack_message(raw):
    if not isinstance(raw, dict):
        raise ValueError('Invalid input.')
    return {'text': _text(raw.get('text'), 'text', 2000),
        'context': _text(raw.get('context') or '', 'context', 200, required=False)}


def _execute_slack_message(sources, clean):
    return sources.slack_send_message(clean['text'], clean['context'])


def _validate_gmail_draft(raw):
    if not isinstance(raw, dict):
        raise ValueError('Invalid input.')
    return {'assigneeName': _text(raw.get('assigneeName'), 'assigneeName', 120),
        'subject': _text(raw.get('subject'), 'subject', 200),
        'body': _text(raw.get('body'), 'body', 4000)}


def _execute_gmail_draft(sources, clean):
    return sources.gmail_create_draft(clean['assigneeName'], clean['subject'], clean['body'])


TOOLS = {
    'jira.searchIssues': {
        'connected_system': 'jira', 'permission': 'jira.read', 'risk': RISK_READ, 'approval_required': False,
        'schema': {'status': f'one or more of {JIRA_STATUSES}', 'priority': f'one or more of {JIRA_PRIORITIES}', 'assignee': 'substring match, optional'},
        'validate': _validate_search_issues, 'execute': _execute_search_issues,
    },
    'jira.getIssue': {
        'connected_system': 'jira', 'permission': 'jira.read', 'risk': RISK_READ, 'approval_required': False,
        'schema': {'issueKey': 'e.g. "ENG-479"'},
        'validate': _validate_get_issue, 'execute': _execute_get_issue,
    },
    'jira.addComment': {
        'connected_system': 'jira', 'permission': 'jira.write', 'risk': RISK_WRITE, 'approval_required': True,
        'schema': {'issueKey': 'e.g. "ENG-479"', 'comment': 'up to 2000 characters'},
        'validate': _validate_add_comment, 'execute': _execute_add_comment,
    },
    'jira.updateAssignee': {
        'connected_system': 'jira', 'permission': 'jira.write', 'risk': RISK_WRITE, 'approval_required': True,
        'schema': {'issueKey': 'e.g. "ENG-479"', 'assignee': 'display name'},
        'validate': _validate_update_assignee, 'execute': _execute_update_assignee,
    },
    'slack.sendMessage': {
        'connected_system': 'slack', 'permission': 'slack.write', 'risk': RISK_MESSAGE, 'approval_required': True,
        'schema': {'text': 'up to 2000 characters', 'context': 'optional label, e.g. an issue key'},
        'validate': _validate_slack_message, 'execute': _execute_slack_message,
    },
    'gmail.createDraft': {
        'connected_system': 'gmail', 'permission': 'gmail.write', 'risk': RISK_MESSAGE, 'approval_required': True,
        'schema': {'assigneeName': 'display name the recipient address is derived from', 'subject': 'up to 200 characters', 'body': 'up to 4000 characters'},
        'validate': _validate_gmail_draft, 'execute': _execute_gmail_draft,
    },
}


def get_tool(name):
    tool = TOOLS.get(name)
    if not tool:
        raise ValueError(f'Unknown tool: {name!r}.')
    return tool
