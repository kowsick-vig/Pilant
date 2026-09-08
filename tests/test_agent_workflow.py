"""Agent workflow automation: goal -> plan -> permission/approval gates ->
controlled tool execution -> audit trail. No live services or AI API calls
are made (ANTHROPIC_API_KEY is blanked via read_environment, so every plan
uses the deterministic fallback drafter -- same offline-safe pattern as
tests/test_dedicated_apps.py).
"""
import tempfile
import unittest
from unittest.mock import patch
from workspace_api import create_app
from workspace_sources import SourceError
import policy_engine

GOAL = 'Find urgent blocked issues, identify the assignees, draft follow-up messages and request updates.'


class AgentWorkflowTests(unittest.TestCase):
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

    def build_app(self, client=None, source='jira'):
        r = self.mutate(client or self.a, '/api/apps/build', {'source': source, 'name': 'Follow-ups', 'prompt': 'Show blocked issues in a board'})
        self.assertEqual(r.status_code, 200)
        return r.json['app']['id']

    def create_plan(self, app_id, client=None, goal=GOAL):
        r = self.mutate(client or self.a, f'/api/apps/{app_id}/agent/plans', {'goal': goal})
        self.assertEqual(r.status_code, 200, r.json)
        return r.json['plan']

    # -- Planning ---------------------------------------------------------

    def test_plan_finds_urgent_blocked_issues_with_pending_write_steps(self):
        app_id = self.build_app()
        plan = self.create_plan(app_id)
        self.assertEqual(plan['status'], 'awaiting_approval')
        self.assertEqual(plan['mode'], 'basic')  # no AI key configured in tests
        search_step = plan['steps'][0]
        self.assertEqual(search_step['tool'], 'jira.searchIssues')
        self.assertEqual(search_step['risk'], 'read')
        self.assertFalse(search_step['approval_required'])
        self.assertEqual(search_step['status'], 'completed')
        self.assertGreaterEqual(search_step['result']['total'], 1)
        write_steps = plan['steps'][1:]
        self.assertTrue(write_steps)
        for step in write_steps:
            self.assertIn(step['tool'], ('jira.addComment', 'gmail.createDraft', 'slack.sendMessage'))
            self.assertIn(step['risk'], ('write', 'message'))
            self.assertTrue(step['approval_required'])
            self.assertEqual(step['status'], 'pending')
        # Slack isn't connected in this test -- outreach must fall back to Gmail, not Slack.
        self.assertTrue(any(s['tool'] == 'gmail.createDraft' for s in write_steps))
        self.assertFalse(any(s['tool'] == 'slack.sendMessage' for s in write_steps))

    def test_automate_mode_rejects_sources_without_a_planner(self):
        # Jira and Splunk have real agent_planner.py builders (see
        # tests/test_agent_workflow_splunk.py for the Splunk use case);
        # every other sample/live source still has none wired up.
        app_id = self.build_app(source='helpdesk')
        r = self.mutate(self.a, f'/api/apps/{app_id}/agent/plans', {'goal': GOAL})
        self.assertEqual(r.status_code, 400)
        self.assertIn('Jira', r.json['error'])

    def test_goal_too_short_is_rejected(self):
        app_id = self.build_app()
        r = self.mutate(self.a, f'/api/apps/{app_id}/agent/plans', {'goal': 'do it'})
        self.assertEqual(r.status_code, 400)

    # -- Approval gate ------------------------------------------------------

    def test_write_step_only_executes_after_approval(self):
        app_id = self.build_app()
        plan = self.create_plan(app_id)
        comment_step = next(s for s in plan['steps'] if s['tool'] == 'jira.addComment')
        issue_key = comment_step['input']['issueKey']
        r = self.mutate(self.a, f'/api/apps/{app_id}/agent/plans/{plan["id"]}/steps/{comment_step["id"]}/approve')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json['step']['status'], 'completed')
        self.assertEqual(r.json['step']['result']['issueKey'], issue_key)
        # The comment really landed on the issue's overlay, not just marked done.
        detail = self.a.get(f'/api/data/jira/{issue_key}').json['record']
        self.assertTrue(detail['detail']['agent_comments'])

    def test_reject_never_executes_the_tool(self):
        app_id = self.build_app()
        plan = self.create_plan(app_id)
        comment_step = next(s for s in plan['steps'] if s['tool'] == 'jira.addComment')
        issue_key = comment_step['input']['issueKey']
        r = self.mutate(self.a, f'/api/apps/{app_id}/agent/plans/{plan["id"]}/steps/{comment_step["id"]}/reject')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json['step']['status'], 'rejected')
        detail = self.a.get(f'/api/data/jira/{issue_key}').json['record']
        self.assertFalse((detail['detail'] or {}).get('agent_comments'))

    def test_a_decided_step_cannot_be_decided_again(self):
        app_id = self.build_app()
        plan = self.create_plan(app_id)
        step = next(s for s in plan['steps'] if s['tool'] == 'jira.addComment')
        self.mutate(self.a, f'/api/apps/{app_id}/agent/plans/{plan["id"]}/steps/{step["id"]}/reject')
        r = self.mutate(self.a, f'/api/apps/{app_id}/agent/plans/{plan["id"]}/steps/{step["id"]}/approve')
        self.assertEqual(r.status_code, 400)

    def test_editing_a_pending_step_changes_what_gets_executed(self):
        app_id = self.build_app()
        plan = self.create_plan(app_id)
        step = next(s for s in plan['steps'] if s['tool'] == 'jira.addComment')
        r = self.mutate(self.a, f'/api/apps/{app_id}/agent/plans/{plan["id"]}/steps/{step["id"]}',
            {'input': {'comment': 'Edited: please prioritize this by Friday.'}}, method='PATCH')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json['step']['input']['comment'], 'Edited: please prioritize this by Friday.')
        r = self.mutate(self.a, f'/api/apps/{app_id}/agent/plans/{plan["id"]}/steps/{step["id"]}/approve')
        self.assertEqual(r.json['step']['result']['comment'], 'Edited: please prioritize this by Friday.')

    def test_cannot_edit_a_step_that_already_ran(self):
        app_id = self.build_app()
        plan = self.create_plan(app_id)
        step = next(s for s in plan['steps'] if s['tool'] == 'jira.addComment')
        self.mutate(self.a, f'/api/apps/{app_id}/agent/plans/{plan["id"]}/steps/{step["id"]}/approve')
        r = self.mutate(self.a, f'/api/apps/{app_id}/agent/plans/{plan["id"]}/steps/{step["id"]}',
            {'input': {'comment': 'too late'}}, method='PATCH')
        self.assertEqual(r.status_code, 400)

    # -- Permission re-check --------------------------------------------

    def test_permission_is_re_checked_at_execution_not_just_planning(self):
        app_id = self.build_app()
        plan = self.create_plan(app_id)
        step = next(s for s in plan['steps'] if s['tool'] == 'jira.addComment')
        with patch('policy_engine.has_permission', return_value=False):
            r = self.mutate(self.a, f'/api/apps/{app_id}/agent/plans/{plan["id"]}/steps/{step["id"]}/approve')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json['step']['status'], 'failed')
        self.assertIn('permission', r.json['step']['error'].lower())
        entries = self.a.get(f'/api/apps/{app_id}/agent/audit').json['entries']
        self.assertTrue(any(e['decision'] == 'blocked' for e in entries))

    # -- Failure + retry ---------------------------------------------------

    def test_failed_step_can_be_retried_and_recorded_each_time(self):
        app_id = self.build_app()
        plan = self.create_plan(app_id)
        step = next(s for s in plan['steps'] if s['tool'] == 'jira.addComment')
        with patch('workspace_sources.Sources.jira_add_comment', side_effect=SourceError('Jira is unreachable.')):
            r = self.mutate(self.a, f'/api/apps/{app_id}/agent/plans/{plan["id"]}/steps/{step["id"]}/approve')
        self.assertEqual(r.json['step']['status'], 'failed')
        self.assertIn('unreachable', r.json['step']['error'])
        r = self.mutate(self.a, f'/api/apps/{app_id}/agent/plans/{plan["id"]}/steps/{step["id"]}/retry')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json['step']['status'], 'completed')
        entries = self.a.get(f'/api/apps/{app_id}/agent/audit').json['entries']
        failed = [e for e in entries if e['tool'] == 'jira.addComment' and e['result'] and 'Failed' in e['result']]
        self.assertTrue(failed)

    def test_only_a_failed_step_can_be_retried(self):
        app_id = self.build_app()
        plan = self.create_plan(app_id)
        step = next(s for s in plan['steps'] if s['tool'] == 'jira.addComment')
        r = self.mutate(self.a, f'/api/apps/{app_id}/agent/plans/{plan["id"]}/steps/{step["id"]}/retry')
        self.assertEqual(r.status_code, 400)

    # -- Pause / resume / cancel --------------------------------------------

    def test_pause_blocks_execution_until_resumed(self):
        app_id = self.build_app()
        plan = self.create_plan(app_id)
        step = next(s for s in plan['steps'] if s['tool'] == 'jira.addComment')
        r = self.mutate(self.a, f'/api/apps/{app_id}/agent/plans/{plan["id"]}/pause')
        self.assertEqual(r.json['plan']['status'], 'paused')
        r = self.mutate(self.a, f'/api/apps/{app_id}/agent/plans/{plan["id"]}/steps/{step["id"]}/approve')
        self.assertEqual(r.status_code, 400)
        self.assertIn('paused', r.json['error'].lower())
        r = self.mutate(self.a, f'/api/apps/{app_id}/agent/plans/{plan["id"]}/resume')
        self.assertNotEqual(r.json['plan']['status'], 'paused')
        r = self.mutate(self.a, f'/api/apps/{app_id}/agent/plans/{plan["id"]}/steps/{step["id"]}/approve')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json['step']['status'], 'completed')

    def test_cancel_marks_remaining_pending_steps_cancelled(self):
        app_id = self.build_app()
        plan = self.create_plan(app_id)
        r = self.mutate(self.a, f'/api/apps/{app_id}/agent/plans/{plan["id"]}/cancel')
        self.assertEqual(r.json['plan']['status'], 'cancelled')
        self.assertTrue(all(s['status'] == 'cancelled' for s in r.json['plan']['steps'] if s['tool'] != 'jira.searchIssues'))
        step = next(s for s in plan['steps'] if s['tool'] == 'jira.addComment')
        r = self.mutate(self.a, f'/api/apps/{app_id}/agent/plans/{plan["id"]}/steps/{step["id"]}/approve')
        self.assertEqual(r.status_code, 400)

    # -- Audit trail ---------------------------------------------------------

    def test_every_decision_and_execution_is_audited_immutably(self):
        app_id = self.build_app()
        plan = self.create_plan(app_id)
        step = next(s for s in plan['steps'] if s['tool'] == 'jira.addComment')
        self.mutate(self.a, f'/api/apps/{app_id}/agent/plans/{plan["id"]}/steps/{step["id"]}/approve')
        entries = self.a.get(f'/api/apps/{app_id}/agent/audit').json['entries']
        self.assertTrue(any(e['action'] == 'create_plan' for e in entries))
        execution = next(e for e in entries if e['action'] == 'execute_step' and e['tool'] == 'jira.addComment')
        self.assertEqual(execution['actor'], 'alice')
        self.assertEqual(execution['decision'], 'approved')
        self.assertEqual(execution['connected_system'], 'jira')
        self.assertIsNotNone(execution['at'])
        # No route anywhere lets a client mutate or delete an audit entry --
        # append-only by construction (Store/audit_logger expose no such API).
        agent_paths = [str(r) for r in self.app.url_map.iter_rules() if '/agent/audit' in str(r)]
        self.assertEqual(agent_paths, ['/api/apps/<app_id>/agent/audit'])

    # -- Cross-owner isolation ------------------------------------------

    def test_plans_and_audit_are_isolated_per_owner(self):
        app_id = self.build_app(client=self.a)
        plan = self.create_plan(app_id, client=self.a)
        self.assertEqual(self.b.get(f'/api/apps/{app_id}/agent/plans/{plan["id"]}').status_code, 404)
        self.assertEqual(self.b.get(f'/api/apps/{app_id}/agent/audit').status_code, 404)
        step = next(s for s in plan['steps'] if s['tool'] == 'jira.addComment')
        r = self.mutate(self.b, f'/api/apps/{app_id}/agent/plans/{plan["id"]}/steps/{step["id"]}/approve')
        self.assertEqual(r.status_code, 404)


if __name__ == '__main__':
    unittest.main()
