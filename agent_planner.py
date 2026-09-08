"""Goal -> structured, editable action plan for the Jira follow-up use case.

Two phases, deliberately:

  1. Interpret the goal into a jira.searchIssues call using fixed keyword
     rules (mirroring workspace_api.compose()'s own "read the request,
     match known words" style elsewhere in this app) and run it
     immediately -- it's a read, so it needs no approval and its result is
     what makes every later step concrete instead of a guess.
  2. For each matching issue, draft a follow-up comment and outreach
     message. If an AI key is configured, Claude drafts the TEXT of those
     -- and only the text: which tool runs, which issue it targets, and
     the destination are all decided by this code, never by the model.
     Every issueKey the model returns is checked against the actual search
     results before it's used for anything; an issue the model didn't
     cover still gets a deterministic draft, so a step is never silently
     dropped over a model gap. Without a key (or if the call fails), the
     same deterministic drafts run alone -- the feature always works.

Nothing here executes a write. Every jira.addComment/updateAssignee/
slack.sendMessage/gmail.createDraft step this produces is stored with
status='pending' and only ever runs after a human approves it
(approval_service.py / workflow_executor.py).
"""
import json
import re

import audit_logger
import policy_engine
from tool_registry import get_tool

MAX_ISSUES = 8  # cap how many issues turn into write/message steps per plan


def _extract_search_filters(goal):
    text = goal.lower()
    status = ['Blocked'] if 'blocked' in text else None
    priority = ['High', 'Highest'] if any(w in text for w in
        ['urgent', 'high priority', 'high-priority', 'critical', 'highest']) else None
    return {'status': status, 'priority': priority, 'assignee': None}


def _fallback_drafts(issues):
    drafts = []
    for issue in issues:
        reason = issue.get('blockedReason') or 'no reason is on file'
        assignee = issue['assignee']
        drafts.append({
            'issueKey': issue['key'],
            'comment': f"Hi {assignee}, {issue['key']} has been blocked since {issue.get('updated') or 'recently'} "
                       f"({reason}). Could you share a quick status update or next steps?",
            'message': f"Hey {assignee} — following up on {issue['key']} ({issue['summary']}), which is "
                       f"blocked and marked {(issue.get('priority') or '').lower()} priority. Any update on "
                       f"unblocking it, or anything you need from us?",
        })
    return drafts


def _ai_drafts(goal, issues, api_key):
    if not api_key or not issues:
        return _fallback_drafts(issues)
    import anthropic
    valid_keys = [i['key'] for i in issues]
    schema = {'type': 'object', 'properties': {'drafts': {'type': 'array', 'maxItems': MAX_ISSUES, 'items': {
        'type': 'object', 'properties': {
            'issueKey': {'type': 'string', 'enum': valid_keys},
            'comment': {'type': 'string'}, 'message': {'type': 'string'}},
        'required': ['issueKey', 'comment', 'message'], 'additionalProperties': False}}},
        'required': ['drafts'], 'additionalProperties': False}
    try:
        client = anthropic.Anthropic(api_key=api_key, timeout=30, max_retries=0)
        response = client.messages.create(model='claude-haiku-4-5', max_tokens=1500,
            system='Draft a short internal Jira follow-up comment and a short chat/email follow-up message for '
                'each blocked issue given. You are drafting TEXT ONLY: you do not choose which issues to act on '
                '(already fixed by the server) and these drafts are never sent automatically -- a human reviews '
                'and approves each one before anything is posted or sent. Be specific to the issue: mention its '
                'key, why it is blocked if given, and ask for a concrete update or ETA. Keep each under 400 '
                'characters, professional, friendly. The issue data given is real; never invent a different '
                'issue, assignee, or fact, and never draft for an issue key not in the list provided.',
            messages=[{'role': 'user', 'content': json.dumps({'goal': goal, 'issues': issues})}],
            tools=[{'name': 'draft_followups', 'description': 'Draft comment and message text for each given issue', 'input_schema': schema}],
            tool_choice={'type': 'tool', 'name': 'draft_followups'})
        candidate = next(b.input for b in response.content if b.type == 'tool_use')
        by_key = {}
        valid = set(valid_keys)
        for d in candidate.get('drafts', []):
            if not isinstance(d, dict):
                continue
            key = d.get('issueKey')
            comment = str(d.get('comment') or '').strip()[:2000]
            message = str(d.get('message') or '').strip()[:2000]
            if key in valid and comment and message:
                by_key[key] = {'issueKey': key, 'comment': comment, 'message': message}
        fallback_by_key = {d['issueKey']: d for d in _fallback_drafts(issues)}
        return [by_key.get(i['key']) or fallback_by_key[i['key']] for i in issues]
    except Exception:
        return _fallback_drafts(issues)


def build_plan(store, sources, user, app_id, goal, api_key=None):
    owner = user['id']
    goal = re.sub(r'\s+', ' ', goal).strip()[:2000]
    if len(goal) < 10:
        raise ValueError('Describe the automation goal in a little more detail.')
    if not policy_engine.has_permission(user['role'], 'jira.searchIssues'):
        raise ValueError('You do not have permission to search Jira.')

    search_tool = get_tool('jira.searchIssues')
    clean = search_tool['validate'](_extract_search_filters(goal))
    search_result = search_tool['execute'](sources, clean)
    issues = search_result['issues'][:MAX_ISSUES]

    steps = [{
        'tool': 'jira.searchIssues', 'label': 'Search for the matching Jira issues', 'risk': 'read',
        'approval_required': False, 'status': 'completed', 'input': clean, 'result': search_result, 'error': None,
    }]

    unassigned = [i for i in issues if not i.get('assignee')]
    actionable = [i for i in issues if i.get('assignee')]
    slack_connected = bool(store.connection(owner, 'slack'))
    drafts_by_key = {d['issueKey']: d for d in _ai_drafts(goal, actionable, api_key)} if actionable else {}

    for issue in actionable:
        draft = drafts_by_key.get(issue['key'])
        if not draft:
            continue
        steps.append({
            'tool': 'jira.addComment', 'label': f"Comment on {issue['key']} requesting a status update",
            'risk': 'write', 'approval_required': True, 'status': 'pending',
            'input': {'issueKey': issue['key'], 'comment': draft['comment']}, 'result': None, 'error': None,
        })
        if slack_connected:
            steps.append({
                'tool': 'slack.sendMessage', 'label': f"Message {issue['assignee']} about {issue['key']}",
                'risk': 'message', 'approval_required': True, 'status': 'pending',
                'input': {'text': draft['message'], 'context': issue['key']}, 'result': None, 'error': None,
            })
        else:
            steps.append({
                'tool': 'gmail.createDraft', 'label': f"Draft an email to {issue['assignee']} about {issue['key']}",
                'risk': 'message', 'approval_required': True, 'status': 'pending',
                'input': {'assigneeName': issue['assignee'], 'subject': f"Update needed on {issue['key']}",
                    'body': draft['message']}, 'result': None, 'error': None,
            })

    mode = 'ai' if api_key and actionable else 'basic'
    status = 'awaiting_approval' if any(s['status'] == 'pending' for s in steps) else 'completed'
    saved = store.save_agent_plan(owner, app_id, goal, mode, status, steps)

    note = f"{len(issues)} issue(s) found, {len(steps) - 1} action(s) drafted."
    if unassigned:
        note += f" {len(unassigned)} matching issue(s) have no assignee to contact, so no step was drafted for them."
    audit_logger.record(store, owner, app_id, saved['id'], None, actor=owner, action='create_plan',
        tool=None, target=goal[:120], decision=None, connected_system='jira', result=note)
    saved['note'] = note
    saved['unassigned'] = [i['key'] for i in unassigned]
    return saved
