"""Runs one plan step through the tool registry.

Every execution -- whether it's a read step running automatically at
planning time, or a write/message step running because a human just
approved it -- goes through `execute_step()`, which:

  1. Re-validates the step's input against the tool's schema (never trusts
     what was stored earlier -- cheap, and closes the gap if a step's
     input was edited between planning and approval).
  2. Re-checks the user's permission immediately before the call, not just
     at planning time (workspace_api.py's own docstring pattern for this
     already exists for other endpoints; a role or connection can change
     in between).
  3. Calls the tool's `execute(sources, clean_input)`, which is always a
     Sources method -- never arbitrary code, never a string the model
     produced.
  4. Records success or failure to the step row and to the audit log,
     without ever writing a raw secret/token into either.

There's no background worker anywhere in this app (see workspace_store.py/
workspace_api.py -- everything is synchronous request/response), so a step
genuinely only spends as long "running" as its one HTTP round trip to the
underlying tool call takes; the frontend drives multi-step "run the whole
workflow" progress by calling this once per approved step in sequence.
"""
import uuid
import audit_logger
import policy_engine
from tool_registry import get_tool
from workspace_sources import SourceError

TERMINAL = {'completed', 'rejected', 'cancelled'}


def execute_step(store, sources, user, app_id, plan_id, step_id):
    owner = user['id']
    plan = store.agent_plan(owner, plan_id)
    if not plan:
        raise ValueError('Plan not found.')
    if plan['status'] == 'paused':
        raise ValueError('This workflow is paused. Resume it to continue running steps.')
    step = store.agent_step(owner, plan_id, step_id)
    if not step:
        raise ValueError('Step not found.')
    tool = get_tool(step['tool'])
    # 'failed' is always re-runnable (that's what Retry does) regardless of
    # approval_required -- reaching 'failed' means it already ran once,
    # which for an approval-required step is only possible after a human
    # approved it, so retrying doesn't ask for approval a second time.
    if step['status'] not in ('pending', 'approved', 'failed'):
        raise ValueError(f"This step is already {step['status']}.")
    if step['approval_required'] and step['status'] == 'pending':
        raise ValueError('This step needs approval before it can run.')

    # Re-check permission immediately before execution -- a decision made
    # back at planning/approval time is never trusted on its own.
    if not policy_engine.has_permission(user['role'], step['tool']):
        store.update_agent_step(owner, plan_id, step_id, {'status': 'failed', 'error': 'Permission denied.'})
        audit_logger.record(store, owner, app_id, plan_id, step_id, actor=owner, action='execute_step',
            tool=step['tool'], target=_target(step), decision='blocked',
            connected_system=tool['connected_system'], result='Permission denied.')
        return store.agent_step(owner, plan_id, step_id)

    try:
        clean = tool['validate'](step['input'])
    except ValueError as e:
        store.update_agent_step(owner, plan_id, step_id, {'status': 'failed', 'error': str(e)})
        audit_logger.record(store, owner, app_id, plan_id, step_id, actor=owner, action='execute_step',
            tool=step['tool'], target=_target(step), decision='invalid_input',
            connected_system=tool['connected_system'], result=str(e))
        return store.agent_step(owner, plan_id, step_id)

    store.update_agent_step(owner, plan_id, step_id, {'status': 'running'})
    idempotency_key = step.get('idempotency_key') or uuid.uuid4().hex
    decision_label = 'approved' if step['approval_required'] else 'auto'
    try:
        result = tool['execute'](sources, clean)
        store.update_agent_step(owner, plan_id, step_id,
            {'status': 'completed', 'result': result, 'error': None, 'idempotency_key': idempotency_key})
        audit_logger.record(store, owner, app_id, plan_id, step_id, actor=owner, action='execute_step',
            tool=step['tool'], target=_target(step), decision=decision_label,
            connected_system=tool['connected_system'], result=_summarize(result))
    except (SourceError, ValueError) as e:
        store.update_agent_step(owner, plan_id, step_id,
            {'status': 'failed', 'error': str(e), 'idempotency_key': idempotency_key})
        audit_logger.record(store, owner, app_id, plan_id, step_id, actor=owner, action='execute_step',
            tool=step['tool'], target=_target(step), decision=decision_label,
            connected_system=tool['connected_system'], result=f'Failed: {e}')
    except Exception:
        # Never leak an internal exception message (could carry a stack
        # frame referencing a token/path) into the step or the audit trail.
        store.update_agent_step(owner, plan_id, step_id,
            {'status': 'failed', 'error': 'Something went wrong running this action.', 'idempotency_key': idempotency_key})
        audit_logger.record(store, owner, app_id, plan_id, step_id, actor=owner, action='execute_step',
            tool=step['tool'], target=_target(step), decision=decision_label,
            connected_system=tool['connected_system'], result='Failed: internal error.')
    finally:
        _settle_plan(store, owner, plan_id)
    return store.agent_step(owner, plan_id, step_id)


def approve_and_run(store, sources, user, app_id, plan_id, step_id):
    import approval_service
    plan = store.agent_plan(user['id'], plan_id)
    if plan and plan['status'] == 'paused':
        raise ValueError('This workflow is paused. Resume it before approving new steps.')
    approval_service.decide(store, user, app_id, plan_id, step_id, 'approved')
    return execute_step(store, sources, user, app_id, plan_id, step_id)


def cancel_plan(store, user, app_id, plan_id):
    owner = user['id']
    plan = store.agent_plan(owner, plan_id)
    if not plan:
        raise ValueError('Plan not found.')
    for step in plan['steps']:
        if step['status'] in ('pending', 'approved'):
            store.update_agent_step(owner, plan_id, step['id'], {'status': 'cancelled'})
    store.update_agent_plan_status(owner, plan_id, 'cancelled')
    audit_logger.record(store, owner, app_id, plan_id, None, actor=owner, action='cancel_plan',
        tool=None, target=plan['goal'][:120], decision=None, connected_system=None, result='Workflow cancelled.')
    return store.agent_plan(owner, plan_id)


def set_paused(store, user, app_id, plan_id, paused):
    owner = user['id']
    plan = store.agent_plan(owner, plan_id)
    if not plan:
        raise ValueError('Plan not found.')
    if plan['status'] == 'cancelled':
        raise ValueError('This workflow was cancelled.')
    new_status = 'paused' if paused else ('awaiting_approval' if any(s['status'] == 'pending' for s in plan['steps']) else 'running')
    store.update_agent_plan_status(owner, plan_id, new_status)
    audit_logger.record(store, owner, app_id, plan_id, None, actor=owner, action='pause_plan' if paused else 'resume_plan',
        tool=None, target=plan['goal'][:120], decision=None, connected_system=None, result=None)
    return store.agent_plan(owner, plan_id)


def _target(step):
    return step['input'].get('issueKey') or step['input'].get('assigneeName') or step['input'].get('context') or ''


def _summarize(result):
    if not isinstance(result, dict):
        return str(result)[:300]
    if result.get('mock'):
        return 'Simulated (not connected): ' + str(result.get('note', ''))[:250]
    if 'issues' in result:
        return f"{result.get('total', len(result['issues']))} issue(s) found."
    if 'comment' in result:
        return f"Comment added to {result.get('issueKey', '')}."
    if 'assignee' in result:
        return f"Reassigned {result.get('issueKey', '')} to {result.get('assignee', '')}."
    if 'ts' in result or 'channel' in result:
        return f"Message sent to #{result.get('channel', '')}."
    if 'draft_id' in result or 'to' in result:
        return f"Draft created for {result.get('to', '')}."
    return 'Completed.'


def _settle_plan(store, owner, plan_id):
    plan = store.agent_plan(owner, plan_id)
    if not plan or plan['status'] in ('cancelled', 'paused'):
        return
    statuses = {s['status'] for s in plan['steps']}
    if statuses & {'pending', 'approved', 'running'}:
        store.update_agent_plan_status(owner, plan_id, 'awaiting_approval' if 'pending' in statuses else 'running')
    elif 'failed' in statuses:
        store.update_agent_plan_status(owner, plan_id, 'failed')
    else:
        store.update_agent_plan_status(owner, plan_id, 'completed')
