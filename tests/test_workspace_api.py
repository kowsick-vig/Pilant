"""Offline security and integration checks for the React workspace API.
Run from project root: python -B -m unittest discover -s tests -p test_workspace_api.py
"""
import json
import tempfile
import unittest
from unittest.mock import patch
from workspace_api import create_app
from workspace_sources import Sources, SourceError


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(self.tmp.name, {'TESTING': True})
        self.store = self.app.extensions['workspace_store']
        self.a, self.b = self.app.test_client(), self.app.test_client()
        self.tokens = {}
        for c, username in [(self.a, 'alice'), (self.b, 'bruno')]:
            self.tokens[id(c)] = c.get('/api/session').json['csrf']
            r = self.mutate(c, '/api/auth/signup', {'username': username, 'name': username.title(), 'password': 'test-password-123', 'role': 'Operations'})
            self.assertEqual(r.status_code, 200)
            self.tokens[id(c)] = r.json['csrf']

    def tearDown(self): self.tmp.cleanup()

    def mutate(self, client, path, data=None, method='POST'):
        return client.open(path, method=method, json=data or {}, headers={'X-CSRF-Token': self.tokens[id(client)]})

    def test_auth_and_csrf_required(self):
        self.assertEqual(self.app.test_client().get('/api/data/jira').status_code, 401)
        self.assertEqual(self.a.post('/api/views', json={}).status_code, 403)
        self.assertEqual(self.a.get('/api/session').headers['Cache-Control'], 'no-store')

    def test_saved_views_are_owned_and_persistent(self):
        spec = {'title': 'Private board', 'layout': 'board', 'sources': ['jira'], 'query': 'Safari'}
        r = self.mutate(self.a, '/api/views', spec)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.b.get('/api/views').json['views'], [])
        self.assertEqual(self.mutate(self.b, '/api/views/' + r.json['id'], method='DELETE').status_code, 404)
        self.assertEqual(self.a.get('/api/views').json['views'][0]['query'], 'Safari')
        from workspace_store import Store
        self.assertEqual(Store(self.tmp.name).views('alice')[0]['title'], 'Private board')

    def test_sample_edits_do_not_leak_between_users_or_modify_fixtures(self):
        row = self.a.get('/api/data/jira').json['records'][0]
        status = row['status']
        new = 'Done' if status != 'Done' else 'Blocked'
        r = self.mutate(self.a, '/api/data/jira/' + row['id'] + '/actions', {'action': 'status', 'value': new})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.a.get('/api/data/jira/' + row['id']).json['record']['status'], new)
        self.assertEqual(self.b.get('/api/data/jira/' + row['id']).json['record']['status'], status)
        from connectors_jira import get_issue
        self.assertEqual(get_issue(row['id'])['status'], status)

    def test_preferences_are_owned(self):
        self.assertEqual(self.mutate(self.a, '/api/preferences', {'layout': 'feed', 'density': 'compact'}, 'PUT').status_code, 200)
        self.assertEqual(self.a.get('/api/session').json['user']['preferences']['layout'], 'feed')
        self.assertEqual(self.b.get('/api/session').json['user']['preferences'], {})

    def test_connections_never_return_credentials(self):
        with patch('workspace_api.http', return_value={}):
            r = self.mutate(self.a, '/api/connections/github', {'repo': 'alice/project', 'token': 'private-test-token'}, 'PUT')
        self.assertEqual(r.status_code, 200)
        response = self.a.get('/api/connections')
        self.assertNotIn('private-test-token', response.get_data(as_text=True))
        self.assertTrue(next(c for c in response.json['connections'] if c['id'] == 'github')['configured'])
        self.assertFalse(next(c for c in self.b.get('/api/connections').json['connections'] if c['id'] == 'github')['configured'])
        self.assertNotIn(b'private-test-token', self.store.path.read_bytes())
        self.assertEqual(self.store.connection('alice', 'github')['token'], 'private-test-token')

    def test_github_fetch_uses_only_the_requesting_users_connection(self):
        self.store.save_connection('alice', 'github', {'repo': 'alice/project', 'token': 'alice-token'})
        with patch('workspace_sources.http', return_value=[]) as call:
            self.assertEqual(self.a.get('/api/data/github').status_code, 200)
            self.assertIn('alice/project', call.call_args.args[0])
            self.assertEqual(call.call_args.args[1], 'alice-token')
            call.reset_mock()
            self.assertEqual(self.b.get('/api/data/github').status_code, 502)
            call.assert_not_called()

    def test_source_errors_are_not_fake_records(self):
        self.assertEqual(self.a.get('/api/data/gmail').status_code, 502)
        self.assertEqual(self.a.get('/api/data/unknown').status_code, 400)
        self.assertEqual(self.a.get('/api/data/jira?q=absolutely-no-match').json['records'], [])

    def test_unsupported_actions_rejected(self):
        self.assertEqual(self.mutate(self.a, '/api/data/jira/ENG-482/actions', {'action':'status','value':'Invented status'}).status_code, 400)
        self.assertEqual(self.mutate(self.a, '/api/data/github/1/actions', {'action':'delete'}).status_code, 400)
        self.assertEqual(self.mutate(self.a, '/api/data/gmail/1/actions', {'action':'reply','value':''}).status_code, 400)

    def test_spec_validation(self):
        self.assertEqual(self.mutate(self.a, '/api/views', {'sources':['jira'],'layout':'raw_javascript'}).status_code, 400)
        self.assertEqual(self.mutate(self.a, '/api/views', {'sources':['secret_database'],'layout':'board'}).status_code, 400)

    def test_compose_fallback_has_no_fabricated_data(self):
        with patch('workspace_api.read_environment', return_value={}):
            app = create_app(self.tmp.name, {'TESTING':True})
        c = app.test_client()
        token = c.get('/api/session').json['csrf']
        r = c.post('/api/auth/login', json={'username':'alice','password':'test-password-123'}, headers={'X-CSRF-Token':token})
        r = c.post('/api/compose', json={'prompt':'show Jira as a table','sources':['jira']}, headers={'X-CSRF-Token':r.json['csrf']})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json['spec']['layout'], 'table')
        self.assertEqual(r.json['mode'], 'keyword')
        self.assertNotIn('records', r.json)

    def test_gmail_token_is_scoped_and_refreshed_once_per_fetch(self):
        self.store.save_connection('alice','gmail',{'refresh_token':'alice-refresh'})
        adapter = Sources(self.store,'alice',{'client_id':'id','client_secret':'secret'})
        with patch('workspace_sources.http', side_effect=[{'access_token':'alice-access'}, {'messages':[]}, {}]) as call:
            self.assertEqual(adapter.fetch('gmail'), [])
            adapter.gmail('/profile')
            self.assertEqual(call.call_count, 3)
            self.assertEqual(call.call_args_list[0].kwargs['form']['refresh_token'], 'alice-refresh')
        with self.assertRaises(SourceError):
            Sources(self.store,'bruno',{}).config('gmail')

    def test_gmail_callback_rejects_unmatched_state(self):
        with patch('workspace_api.http') as call:
            response = self.a.get('/api/oauth/gmail/callback?state=wrong&code=wrong')
            self.assertEqual(response.status_code, 302)
            self.assertIn('connection_error', response.location)
            call.assert_not_called()

    def test_gmail_reply_uses_original_thread_and_user_token(self):
        self.store.save_connection('alice','gmail',{'refresh_token':'alice-refresh'})
        adapter = Sources(self.store,'alice',{'client_id':'id','client_secret':'secret'})
        message = {'detail':{'from':'Sender <sender@example.com>','subject':'Test','message_id_header':'<message-1>', 'references':'', 'threadId':'thread-1'}}
        with patch.object(adapter, 'detail', return_value=message), patch.object(adapter, 'gmail', return_value={'id':'sent'}) as send:
            adapter.action('gmail','message-1','reply','Reviewed reply')
            payload = send.call_args.args[1]
            self.assertEqual(payload['threadId'], 'thread-1')
            self.assertEqual(send.call_args.args[0], '/messages/send')
            import base64
            raw = base64.urlsafe_b64decode(payload['raw']).decode()
            self.assertIn('sender@example.com', raw)
            self.assertIn('In-Reply-To: <message-1>', raw)

if __name__ == '__main__': unittest.main()
