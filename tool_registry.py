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
SPLUNK_STATUSES = ['new', 'investigating', 'escalated', 'resolved']
SPLUNK_SEVERITIES = ['critical', 'high', 'medium', 'low', 'informational']
CRM_STATUSES = ['open', 'in_progress', 'overdue', 'done']
CRM_PRIORITIES = ['high', 'medium', 'low']

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


def _one_of(value, allowed, name):
    if not isinstance(value, str):
        raise ValueError(f'{name} is required.')
    hit = next((a for a in allowed if a.lower() == value.strip().lower()), None)
    if not hit:
        raise ValueError(f'Unsupported {name}: {value!r}.')
    return hit


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


def _validate_search_events(raw):
    if not isinstance(raw, dict):
        raise ValueError('Invalid input.')
    unassigned = raw.get('unassigned')
    if unassigned is not None and not isinstance(unassigned, bool):
        raise ValueError('unassigned must be true or false.')
    return {
        'severity': _as_list(raw.get('severity'), SPLUNK_SEVERITIES, 'severity'),
        'status': _as_list(raw.get('status'), SPLUNK_STATUSES, 'status'),
        'unassigned': unassigned,
    }


def _execute_search_events(sources, clean):
    rows = sources.fetch('splunk')

    def keep(row):
        d = row.get('detail') or {}
        severity_ok = not clean['severity'] or d.get('severity') in clean['severity']
        status_ok = not clean['status'] or d.get('status') in clean['status']
        # When both a severity and a status filter are given, treat them as
        # alternative signals of urgency ("critical OR escalated") rather
        # than compounding requirements -- an already-escalated event and a
        # newly-triggered critical one are both worth surfacing, and
        # requiring both at once would silently return nothing for the
        # natural-language goal this filter is built from. With only one of
        # the two given, that one must match (the other is trivially true).
        urgent = (severity_ok or status_ok) if (clean['severity'] and clean['status']) else (severity_ok and status_ok)
        if not urgent:
            return False
        if clean['unassigned'] and d.get('owner') != 'Unassigned':
            return False
        return True

    matches = [r for r in rows if keep(r)]
    events = [{
        'id': r['id'], 'title': r['title'], 'severity': (r.get('detail') or {}).get('severity'),
        'status': (r.get('detail') or {}).get('status'), 'owner': (r.get('detail') or {}).get('owner'),
        'triggerTime': (r.get('detail') or {}).get('trigger_time'),
        'mitreTechnique': (r.get('detail') or {}).get('mitre_technique'),
    } for r in matches]
    return {'events': events, 'total': len(events)}


def _validate_get_event(raw):
    if not isinstance(raw, dict):
        raise ValueError('Invalid input.')
    return {'eventId': _text(raw.get('eventId'), 'eventId', 40)}


def _execute_get_event(sources, clean):
    event = sources.detail('splunk', clean['eventId'])
    if not event:
        raise ValueError(f"No Splunk event found with id {clean['eventId']}.")
    d = event.get('detail') or {}
    return {'id': event['id'], 'title': event['title'], 'severity': d.get('severity'),
        'status': d.get('status'), 'owner': d.get('owner'), 'triggerTime': d.get('trigger_time')}


def _validate_assign_analyst(raw):
    if not isinstance(raw, dict):
        raise ValueError('Invalid input.')
    from connectors_splunk import ANALYSTS  # the only names an assignment may target
    return {'eventId': _text(raw.get('eventId'), 'eventId', 40),
        'analyst': _one_of(raw.get('analyst'), ANALYSTS, 'analyst')}


def _execute_assign_analyst(sources, clean):
    return sources.splunk_assign_analyst(clean['eventId'], clean['analyst'])


def _validate_search_tasks(raw):
    if not isinstance(raw, dict):
        raise ValueError('Invalid input.')
    unassigned = raw.get('unassigned')
    if unassigned is not None and not isinstance(unassigned, bool):
        raise ValueError('unassigned must be true or false.')
    return {
        'status': _as_list(raw.get('status'), CRM_STATUSES, 'status'),
        'priority': _as_list(raw.get('priority'), CRM_PRIORITIES, 'priority'),
        'unassigned': unassigned,
    }


def _execute_search_tasks(sources, clean):
    rows = sources.fetch('crm')

    def keep(row):
        d = row.get('detail') or {}
        status_ok = not clean['status'] or d.get('status') in clean['status']
        priority_ok = not clean['priority'] or d.get('priority') in clean['priority']
        # Same reasoning as Splunk's search: when both a status and a
        # priority filter are given, treat them as alternative signals of
        # urgency ("overdue OR high priority") rather than a compounding
        # requirement -- these two dimensions are otherwise unrelated, and
        # requiring both at once would silently return nothing for a
        # perfectly reasonable natural-language goal.
        urgent = (status_ok or priority_ok) if (clean['status'] and clean['priority']) else (status_ok and priority_ok)
        if not urgent:
            return False
        if clean['unassigned'] and d.get('owner') != 'Unassigned':
            return False
        return True

    matches = [r for r in rows if keep(r)]
    tasks = [{
        'id': r['id'], 'title': r['title'], 'status': (r.get('detail') or {}).get('status'),
        'priority': (r.get('detail') or {}).get('priority'), 'owner': (r.get('detail') or {}).get('owner'),
        'company': (r.get('detail') or {}).get('company'), 'contact': (r.get('detail') or {}).get('contact'),
        'stage': (r.get('detail') or {}).get('stage'), 'due': (r.get('detail') or {}).get('due'),
    } for r in matches]
    return {'tasks': tasks, 'total': len(tasks)}


def _validate_get_task(raw):
    if not isinstance(raw, dict):
        raise ValueError('Invalid input.')
    return {'taskId': _text(raw.get('taskId'), 'taskId', 40)}


def _execute_get_task(sources, clean):
    task = sources.detail('crm', clean['taskId'])
    if not task:
        raise ValueError(f"No CRM task found with id {clean['taskId']}.")
    d = task.get('detail') or {}
    return {'id': task['id'], 'title': task['title'], 'status': d.get('status'), 'priority': d.get('priority'),
        'owner': d.get('owner'), 'company': d.get('company'), 'contact': d.get('contact'), 'stage': d.get('stage')}


def _validate_add_note(raw):
    if not isinstance(raw, dict):
        raise ValueError('Invalid input.')
    return {'taskId': _text(raw.get('taskId'), 'taskId', 40),
        'note': _text(raw.get('note'), 'note', 2000)}


def _execute_add_note(sources, clean):
    return sources.crm_add_note(clean['taskId'], sources.owner, clean['note'])


def _validate_assign_owner(raw):
    if not isinstance(raw, dict):
        raise ValueError('Invalid input.')
    from connectors_crm import REPS  # the only names an assignment may target
    return {'taskId': _text(raw.get('taskId'), 'taskId', 40),
        'owner': _one_of(raw.get('owner'), REPS, 'owner')}


def _execute_assign_owner(sources, clean):
    return sources.crm_assign_owner(clean['taskId'], clean['owner'])


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
    'splunk.searchEvents': {
        'connected_system': 'splunk', 'permission': 'splunk.read', 'risk': RISK_READ, 'approval_required': False,
        'schema': {'severity': f'one or more of {SPLUNK_SEVERITIES}', 'status': f'one or more of {SPLUNK_STATUSES}',
            'unassigned': 'true to only match events with no analyst assigned'},
        'validate': _validate_search_events, 'execute': _execute_search_events,
    },
    'splunk.getEvent': {
        'connected_system': 'splunk', 'permission': 'splunk.read', 'risk': RISK_READ, 'approval_required': False,
        'schema': {'eventId': 'e.g. "NE-30231"'},
        'validate': _validate_get_event, 'execute': _execute_get_event,
    },
    'splunk.assignAnalyst': {
        'connected_system': 'splunk', 'permission': 'splunk.write', 'risk': RISK_WRITE, 'approval_required': True,
        'schema': {'eventId': 'e.g. "NE-30231"', 'analyst': 'one of the known analyst roster'},
        'validate': _validate_assign_analyst, 'execute': _execute_assign_analyst,
    },
    'crm.searchTasks': {
        'connected_system': 'crm', 'permission': 'crm.read', 'risk': RISK_READ, 'approval_required': False,
        'schema': {'status': f'one or more of {CRM_STATUSES}', 'priority': f'one or more of {CRM_PRIORITIES}',
            'unassigned': 'true to only match tasks with no rep assigned'},
        'validate': _validate_search_tasks, 'execute': _execute_search_tasks,
    },
    'crm.getTask': {
        'connected_system': 'crm', 'permission': 'crm.read', 'risk': RISK_READ, 'approval_required': False,
        'schema': {'taskId': 'e.g. "CRM-4001"'},
        'validate': _validate_get_task, 'execute': _execute_get_task,
    },
    'crm.addNote': {
        'connected_system': 'crm', 'permission': 'crm.write', 'risk': RISK_WRITE, 'approval_required': True,
        'schema': {'taskId': 'e.g. "CRM-4001"', 'note': 'up to 2000 characters'},
        'validate': _validate_add_note, 'execute': _execute_add_note,
    },
    'crm.assignOwner': {
        'connected_system': 'crm', 'permission': 'crm.write', 'risk': RISK_WRITE, 'approval_required': True,
        'schema': {'taskId': 'e.g. "CRM-4001"', 'owner': 'one of the known rep roster'},
        'validate': _validate_assign_owner, 'execute': _execute_assign_owner,
    },
}


def get_tool(name):
    tool = TOOLS.get(name)
    if not tool:
        raise ValueError(f'Unknown tool: {name!r}.')
    return tool
