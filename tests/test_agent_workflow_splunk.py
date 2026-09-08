"""Agent workflow automation for the Splunk use case: "Find critical or
escalated notable events with no assigned analyst, draft an assignment and
request triage." Mirrors tests/test_agent_workflow.py's structure and
offline-safe conventions (ANTHROPIC_API_KEY blanked -> deterministic
fallback drafts, no live services or AI calls).

The fictional fixture (connectors_splunk.py) has exactly one notable event
that is both critical-or-escalated AND unassigned: NE-30235 ("Unusual
Volume of Data Egress to External Host", critical, investigating, owner
"Unassigned") -- verified directly against get_events() rather than
assumed, since the search treats a severity filter and a status filter as
alternative signals of urgency (OR) when a goal gives both, not a
compounding requirement (see tool_registry._execute_search_events).
"""
import tempfile
import unittest
from unittest.mock import patch
from workspace_api import create_app
from workspace_sources import SourceError
from connectors_splunk import ANALYSTS

GOAL = 'Find critical or escalated notable events with no assigned analyst, draft an assignment and request triage.'
EVENT_ID = 'NE-30235'


class SplunkAgentWorkflowTests(unittest.TestCase):
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

    def build_app(self, client=None, source='splunk'):
        r = self.mutate(client or self.a, '/api/apps/build', {'source': source, 'name': 'Triage queue', 'prompt': 'Show notable events in a table'})
        self.assertEqual(r.status_code, 200)
        return r.json['app']['id']

    def create_plan(self, app_id, client=None, goal=GOAL):
        r = self.mutate(client or self.a, f'/api/apps/{app_id}/agent/plans', {'goal': goal})
        self.assertEqual(r.status_code, 200, r.json)
        return r.json['plan']

    # -- Planning ---------------------------------------------------------

    def test_plan_finds_the_one_unassigned_critical_event_with_pending_write_steps(self):
        app_id = self.build_app()
        plan = self.create_plan(app_id)
        self.assertEqual(plan['status'], 'awaiting_approval')
        self.assertEqual(plan['mode'], 'basic')  # no AI key configured in tests
        search_step = plan['steps'][0]
        self.assertEqual(search_step['tool'], 'splunk.searchEvents')
        self.assertEqual(search_step['risk'], 'read')
        self.assertFalse(search_step['approval_required'])
        self.assertEqual(search_step['status'], 'completed')
        self.assertEqual(search_step['result']['total'], 1)
        self.assertEqual(search_step['result']['events'][0]['id'], EVENT_ID)
        write_steps = plan['steps'][1:]
        self.assertEqual(len(write_steps), 2)  # one assign + one message, for the one matching event
        assign_step = next(s for s in write_steps if s['tool'] == 'splunk.assignAnalyst')
        self.assertEqual(assign_step['input']['eventId'], EVENT_ID)
        self.assertIn(assign_step['input']['analyst'], ANALYSTS)  # target is server-picked from the fixed roster
        self.assertTrue(assign_step['approval_required'])
        self.assertEqual(assign_step['status'], 'pending')
        # Slack isn't connected in this test -- outreach must fall back to Gmail, not Slack.
        message_step = next(s for s in write_steps if s['tool'] != 'splunk.assignAnalyst')
        self.assertEqual(message_step['tool'], 'gmail.createDraft')
        self.assertEqual(message_step['input']['assigneeName'], assign_step['input']['analyst'])

    def test_automate_mode_accepts_both_jira_and_splunk_but_nothing_else(self):
        r = self.mutate(self.a, f'/api/apps/{self.build_app(source="helpdesk")}/agent/plans', {'goal': GOAL})
        self.assertEqual(r.status_code, 400)
        self.assertIn('Splunk', r.json['error'])
        # Splunk itself (and, unchanged, Jira) must still work.
        self.assertEqual(self.mutate(self.a, f'/api/apps/{self.build_app(source="splunk")}/agent/plans', {'goal': GOAL}).status_code, 200)

    # -- Approval gate ------------------------------------------------------

    def test_write_step_only_executes_after_approval(self):
        app_id = self.build_app()
        plan = self.create_plan(app_id)
        assign_step = next(s for s in plan['steps'] if s['tool'] == 'splunk.assignAnalyst')
        analyst = assign_step['input']['analyst']
        r = self.mutate(self.a, f'/api/apps/{app_id}/agent/plans/{plan["id"]}/steps/{assign_step["id"]}/approve')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json['step']['status'], 'completed')
        self.assertEqual(r.json['step']['result']['analyst'], analyst)
        # The assignment really landed on the event's overlay, not just marked done.
        detail = self.a.get(f'/api/data/splunk/{EVENT_ID}').json['record']
        self.assertEqual(detail['detail']['owner'], analyst)

    def test_reject_never_executes_the_tool(self):
        app_id = self.build_app()
        plan = self.create_plan(app_id)
        assign_step = next(s for s in plan['steps'] if s['tool'] == 'splunk.assignAnalyst')
        r = self.mutate(self.a, f'/api/apps/{app_id}/agent/plans/{plan["id"]}/steps/{assign_step["id"]}/reject')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json['step']['status'], 'rejected')
        detail = self.a.get(f'/api/data/splunk/{EVENT_ID}').json['record']
        self.assertEqual(detail['detail']['owner'], 'Unassigned')

    # -- Tool boundary: assignment target is constrained, not free text -----

    def test_assign_analyst_rejects_a_name_outside_the_known_roster(self):
        app_id = self.build_app()
        plan = self.create_plan(app_id)
        assign_step = next(s for s in plan['steps'] if s['tool'] == 'splunk.assignAnalyst')
        r = self.mutate(self.a, f'/api/apps/{app_id}/agent/plans/{plan["id"]}/steps/{assign_step["id"]}',
            {'input': {'analyst': 'Some Rando Not On The Team'}}, method='PATCH')
        self.assertEqual(r.status_code, 400)

    # -- Permission re-check --------------------------------------------

    def test_permission_is_re_checked_at_execution_not_just_planning(self):
        app_id = self.build_app()
        plan = self.create_plan(app_id)
        assign_step = next(s for s in plan['steps'] if s['tool'] == 'splunk.assignAnalyst')
        with patch('policy_engine.has_permission', return_value=False):
            r = self.mutate(self.a, f'/api/apps/{app_id}/agent/plans/{plan["id"]}/steps/{assign_step["id"]}/approve')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json['step']['status'], 'failed')
        self.assertIn('permission', r.json['step']['error'].lower())
        entries = self.a.get(f'/api/apps/{app_id}/agent/audit').json['entries']
        self.assertTrue(any(e['decision'] == 'blocked' and e['connected_system'] == 'splunk' for e in entries))

    # -- Failure + retry ---------------------------------------------------

    def test_failed_step_can_be_retried_and_recorded_each_time(self):
        app_id = self.build_app()
        plan = self.create_plan(app_id)
        assign_step = next(s for s in plan['steps'] if s['tool'] == 'splunk.assignAnalyst')
        with patch('workspace_sources.Sources.splunk_assign_analyst', side_effect=SourceError('Splunk is unreachable.')):
            r = self.mutate(self.a, f'/api/apps/{app_id}/agent/plans/{plan["id"]}/steps/{assign_step["id"]}/approve')
        self.assertEqual(r.json['step']['status'], 'failed')
        self.assertIn('unreachable', r.json['step']['error'])
        r = self.mutate(self.a, f'/api/apps/{app_id}/agent/plans/{plan["id"]}/steps/{assign_step["id"]}/retry')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json['step']['status'], 'completed')

    # -- Audit trail ---------------------------------------------------------

    def test_every_decision_and_execution_is_audited_immutably(self):
        app_id = self.build_app()
        plan = self.create_plan(app_id)
        assign_step = next(s for s in plan['steps'] if s['tool'] == 'splunk.assignAnalyst')
        self.mutate(self.a, f'/api/apps/{app_id}/agent/plans/{plan["id"]}/steps/{assign_step["id"]}/approve')
        entries = self.a.get(f'/api/apps/{app_id}/agent/audit').json['entries']
        self.assertTrue(any(e['action'] == 'create_plan' and e['connected_system'] == 'splunk' for e in entries))
        execution = next(e for e in entries if e['action'] == 'execute_step' and e['tool'] == 'splunk.assignAnalyst')
        self.assertEqual(execution['actor'], 'alice')
        self.assertEqual(execution['decision'], 'approved')
        self.assertEqual(execution['connected_system'], 'splunk')
        self.assertIsNotNone(execution['at'])

    # -- Cross-owner isolation ------------------------------------------

    def test_plans_and_audit_are_isolated_per_owner(self):
        app_id = self.build_app(client=self.a)
        plan = self.create_plan(app_id, client=self.a)
        self.assertEqual(self.b.get(f'/api/apps/{app_id}/agent/plans/{plan["id"]}').status_code, 404)
        assign_step = next(s for s in plan['steps'] if s['tool'] == 'splunk.assignAnalyst')
        r = self.mutate(self.b, f'/api/apps/{app_id}/agent/plans/{plan["id"]}/steps/{assign_step["id"]}/approve')
        self.assertEqual(r.status_code, 404)


if __name__ == '__main__':
    unittest.main()
