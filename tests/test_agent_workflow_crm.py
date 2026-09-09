"""Agent workflow automation for the CRM use case, which unifies both
patterns already built: an owned-but-stalled task gets a logged note plus
a nudge to its current rep (the Jira addComment pattern), while an
unowned task gets assigned via deterministic round-robin plus a pickup
request (the Splunk assignAnalyst pattern) -- which branch a matched task
takes depends only on whether it already has an owner, never the model's
choice, and reassignment never happens to a task someone is already
working. Mirrors tests/test_agent_workflow.py and
tests/test_agent_workflow_splunk.py's structure and offline-safe
conventions (ANTHROPIC_API_KEY blanked -> deterministic fallback drafts).

Both fixture match sets below were verified directly against
connectors_crm.get_tasks() rather than assumed -- see the exploration in
this session -- since the search treats a status filter and a priority
filter as alternative signals of urgency (OR) when a goal gives both, not
a compounding requirement (see tool_registry._execute_search_tasks).
"""
import tempfile
import unittest
from unittest.mock import patch
from workspace_api import create_app
from workspace_sources import SourceError
from connectors_crm import REPS

GOAL_OWNED = 'Find overdue high priority follow-up tasks and request status updates from the reps.'
GOAL_UNASSIGNED = 'Find unassigned follow-up tasks and assign a rep to each one.'
UNASSIGNED_TASK_IDS = {'CRM-4006', 'CRM-4012', 'CRM-4018', 'CRM-4024'}


class CrmAgentWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        with patch('workspace_api.read_environment', return_value={}):
            self.app = create_app(self.temp.name, {'TESTING': True})
        self.store = self.app.extensions['workspace_store']
        self.a, self.b = self.app.test_client(), self.app.test_client()
        self.tokens = {}
        for client, name in [(self.a, 'alice'), (self.b, 'bruno')]:
            token = client.get('/api/session').json['csrf']
            r = client.post('/api/auth/signup', json={'username': name, 'name': name,
                'role': 'Operations', 'password': 'test-password-123'}, headers={'X-CSRF-Token': token})
            self.assertEqual(r.status_code, 200)
            self.tokens[id(client)] = r.json['csrf']

    def tearDown(self): self.temp.cleanup()

    def mutate(self, client, path, data=None, method='POST'):
        return client.open(path, method=method, json=data or {}, headers={'X-CSRF-Token': self.tokens[id(client)]})

    def build_app(self, client=None, source='crm'):
        r = self.mutate(client or self.a, '/api/apps/build', {'source': source, 'name': 'Follow-ups', 'prompt': 'Show open tasks in a board'})
        self.assertEqual(r.status_code, 200)
        return r.json['app']['id']

    def create_plan(self, app_id, client=None, goal=GOAL_OWNED):
        r = self.mutate(client or self.a, f'/api/apps/{app_id}/agent/plans', {'goal': goal})
        self.assertEqual(r.status_code, 200, r.json)
        return r.json['plan']

    # -- Planning: the "nudge the current owner" branch ---------------------

    def test_plan_nudges_owners_of_overdue_or_high_priority_tasks(self):
        app_id = self.build_app()
        plan = self.create_plan(app_id, goal=GOAL_OWNED)
        self.assertEqual(plan['status'], 'awaiting_approval')
        self.assertEqual(plan['mode'], 'basic')
        search_step = plan['steps'][0]
        self.assertEqual(search_step['tool'], 'crm.searchTasks')
        self.assertFalse(search_step['approval_required'])
        self.assertEqual(search_step['status'], 'completed')
        self.assertEqual(search_step['result']['total'], 12)  # verified against the fixture directly
        write_steps = plan['steps'][1:]
        self.assertEqual(len(write_steps), 16)  # capped at 8 tasks x (note + message)
        self.assertTrue(all(s['tool'] != 'crm.assignOwner' for s in write_steps))  # every match already has an owner
        note_steps = [s for s in write_steps if s['tool'] == 'crm.addNote']
        self.assertEqual(len(note_steps), 8)
        for step in write_steps:
            self.assertIn(step['tool'], ('crm.addNote', 'gmail.createDraft', 'slack.sendMessage'))
            self.assertTrue(step['approval_required'])
            self.assertEqual(step['status'], 'pending')
        self.assertTrue(any(s['tool'] == 'gmail.createDraft' for s in write_steps))  # Slack isn't connected

    # -- Planning: the "assign the unowned task" branch ---------------------

    def test_plan_assigns_owners_of_unassigned_tasks(self):
        app_id = self.build_app()
        plan = self.create_plan(app_id, goal=GOAL_UNASSIGNED)
        search_step = plan['steps'][0]
        self.assertEqual(search_step['result']['total'], 4)
        write_steps = plan['steps'][1:]
        self.assertEqual(len(write_steps), 8)  # 4 tasks x (assign + message), no cap needed
        assign_steps = [s for s in write_steps if s['tool'] == 'crm.assignOwner']
        self.assertEqual(len(assign_steps), 4)
        self.assertEqual({s['input']['taskId'] for s in assign_steps}, UNASSIGNED_TASK_IDS)
        for step in assign_steps:
            self.assertIn(step['input']['owner'], REPS)  # target is server-picked from the fixed roster
        message_steps = [s for s in write_steps if s['tool'] != 'crm.assignOwner']
        self.assertTrue(all(s['tool'] == 'gmail.createDraft' for s in message_steps))

    def test_automate_mode_accepts_jira_splunk_and_crm_but_nothing_else(self):
        r = self.mutate(self.a, f'/api/apps/{self.build_app(source="helpdesk")}/agent/plans', {'goal': GOAL_OWNED})
        self.assertEqual(r.status_code, 400)
        self.assertIn('CRM', r.json['error'])
        self.assertEqual(self.mutate(self.a, f'/api/apps/{self.build_app(source="crm")}/agent/plans', {'goal': GOAL_OWNED}).status_code, 200)

    # -- Approval gate ------------------------------------------------------

    def test_write_step_only_executes_after_approval(self):
        app_id = self.build_app()
        plan = self.create_plan(app_id)
        note_step = next(s for s in plan['steps'] if s['tool'] == 'crm.addNote')
        task_id = note_step['input']['taskId']
        r = self.mutate(self.a, f'/api/apps/{app_id}/agent/plans/{plan["id"]}/steps/{note_step["id"]}/approve')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json['step']['status'], 'completed')
        self.assertEqual(r.json['step']['result']['taskId'], task_id)
        detail = self.a.get(f'/api/data/crm/{task_id}').json['record']
        self.assertTrue(detail['detail']['agent_notes'])

    def test_reject_never_executes_the_tool(self):
        app_id = self.build_app()
        plan = self.create_plan(app_id)
        note_step = next(s for s in plan['steps'] if s['tool'] == 'crm.addNote')
        task_id = note_step['input']['taskId']
        r = self.mutate(self.a, f'/api/apps/{app_id}/agent/plans/{plan["id"]}/steps/{note_step["id"]}/reject')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json['step']['status'], 'rejected')
        detail = self.a.get(f'/api/data/crm/{task_id}').json['record']
        self.assertFalse((detail['detail'] or {}).get('agent_notes'))

    # -- Tool boundary: assignment target is constrained, not free text -----

    def test_assign_owner_rejects_a_name_outside_the_known_roster(self):
        app_id = self.build_app()
        plan = self.create_plan(app_id, goal=GOAL_UNASSIGNED)
        assign_step = next(s for s in plan['steps'] if s['tool'] == 'crm.assignOwner')
        r = self.mutate(self.a, f'/api/apps/{app_id}/agent/plans/{plan["id"]}/steps/{assign_step["id"]}',
            {'input': {'owner': 'Some Rando Not On The Team'}}, method='PATCH')
        self.assertEqual(r.status_code, 400)

    # -- Permission re-check --------------------------------------------

    def test_permission_is_re_checked_at_execution_not_just_planning(self):
        app_id = self.build_app()
        plan = self.create_plan(app_id)
        note_step = next(s for s in plan['steps'] if s['tool'] == 'crm.addNote')
        with patch('policy_engine.has_permission', return_value=False):
            r = self.mutate(self.a, f'/api/apps/{app_id}/agent/plans/{plan["id"]}/steps/{note_step["id"]}/approve')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json['step']['status'], 'failed')
        self.assertIn('permission', r.json['step']['error'].lower())
        entries = self.a.get(f'/api/apps/{app_id}/agent/audit').json['entries']
        self.assertTrue(any(e['decision'] == 'blocked' and e['connected_system'] == 'crm' for e in entries))

    # -- Failure + retry ---------------------------------------------------

    def test_failed_step_can_be_retried_and_recorded_each_time(self):
        app_id = self.build_app()
        plan = self.create_plan(app_id)
        note_step = next(s for s in plan['steps'] if s['tool'] == 'crm.addNote')
        with patch('workspace_sources.Sources.crm_add_note', side_effect=SourceError('CRM is unreachable.')):
            r = self.mutate(self.a, f'/api/apps/{app_id}/agent/plans/{plan["id"]}/steps/{note_step["id"]}/approve')
        self.assertEqual(r.json['step']['status'], 'failed')
        self.assertIn('unreachable', r.json['step']['error'])
        r = self.mutate(self.a, f'/api/apps/{app_id}/agent/plans/{plan["id"]}/steps/{note_step["id"]}/retry')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json['step']['status'], 'completed')

    # -- Audit trail ---------------------------------------------------------

    def test_every_decision_and_execution_is_audited_immutably(self):
        app_id = self.build_app()
        plan = self.create_plan(app_id)
        note_step = next(s for s in plan['steps'] if s['tool'] == 'crm.addNote')
        self.mutate(self.a, f'/api/apps/{app_id}/agent/plans/{plan["id"]}/steps/{note_step["id"]}/approve')
        entries = self.a.get(f'/api/apps/{app_id}/agent/audit').json['entries']
        self.assertTrue(any(e['action'] == 'create_plan' and e['connected_system'] == 'crm' for e in entries))
        execution = next(e for e in entries if e['action'] == 'execute_step' and e['tool'] == 'crm.addNote')
        self.assertEqual(execution['actor'], 'alice')
        self.assertEqual(execution['decision'], 'approved')
        self.assertEqual(execution['connected_system'], 'crm')
        self.assertIsNotNone(execution['at'])

    # -- Cross-owner isolation ------------------------------------------

    def test_plans_and_audit_are_isolated_per_owner(self):
        app_id = self.build_app(client=self.a)
        plan = self.create_plan(app_id, client=self.a)
        self.assertEqual(self.b.get(f'/api/apps/{app_id}/agent/plans/{plan["id"]}').status_code, 404)
        note_step = next(s for s in plan['steps'] if s['tool'] == 'crm.addNote')
        r = self.mutate(self.b, f'/api/apps/{app_id}/agent/plans/{plan["id"]}/steps/{note_step["id"]}/approve')
        self.assertEqual(r.status_code, 404)


if __name__ == '__main__':
    unittest.main()
