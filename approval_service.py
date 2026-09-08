"""Human approve/reject decisions on one plan step.

Idempotent by construction: a step can only be decided while it's still
'pending', so replaying the same request twice (a double click, a retried
network request) can't silently approve something twice or flip an
already-rejected step to approved. Every decision writes exactly one audit
entry in the same call, so a decision and its audit record can never
drift apart.
"""
import audit_logger


def decide(store, user, app_id, plan_id, step_id, decision):
    if decision not in ('approved', 'rejected'):
        raise ValueError('Invalid decision.')
    owner = user['id']
    step = store.agent_step(owner, plan_id, step_id)
    if not step:
        raise ValueError('Step not found.')
    if step['status'] != 'pending':
        raise ValueError(f"This step is already {step['status']} and can't be decided again.")
    store.update_agent_step(owner, plan_id, step_id, {'status': decision})
    target = step['input'].get('issueKey') or step['input'].get('assigneeName') or step['input'].get('context') or ''
    audit_logger.record(store, owner, app_id, plan_id, step_id, actor=owner, action='decide_step',
        tool=step['tool'], target=target, decision=decision,
        connected_system=step['tool'].split('.')[0], result=None)
    return store.agent_step(owner, plan_id, step_id)
