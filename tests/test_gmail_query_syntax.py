"""Regression test for a real bug found live: a Gmail screen's stored query
("is:unread from:(anthropic.com OR puregym.com OR uber.com)") was silently
gutted before Sources.fetch() ever saw it, so a "pull unread messages from
Anthropic, PureGym, and Uber" app showed every unread message instead of
just those senders.

Root cause: workspace_api.py's app_data() ran _literal_query() (which
strips "field:value"-shaped tokens) on EVERY source's page query. That
stripper exists to stop a different bug -- a generic source's literal
substring search over full record JSON returning zero rows for a
"status:Blocked"-shaped query (see _literal_query's own docstring) -- but
Gmail is not a generic source: workspace_sources.py's fetch() forwards a
Gmail page's query straight to the real Gmail search API and returns
before the generic substring-match block runs at all, so Gmail genuinely
understands "is:unread"/"from:(a OR b OR c)" syntax, which is exactly what
app_composer.py's system prompt tells the model it may use for Gmail
screens. Stripping it first broke exactly the capability the prompt
promised.

Fix: app_data() now only runs _literal_query() for non-Gmail sources.
"""
import tempfile
import unittest
from unittest.mock import patch
from workspace_api import create_app


class GmailQuerySyntaxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        with patch('workspace_api.read_environment', return_value={}):
            self.app = create_app(self.temp.name, {'TESTING': True})
        self.store = self.app.extensions['workspace_store']
        self.client = self.app.test_client()
        token = self.client.get('/api/session').json['csrf']
        r = self.client.post('/api/auth/signup', json={'username': 'alice', 'name': 'alice',
            'role': 'Operations', 'password': 'test-password-123'}, headers={'X-CSRF-Token': token})
        self.assertEqual(r.status_code, 200)
        self.csrf = r.json['csrf']

    def tearDown(self):
        self.temp.cleanup()

    def build_page(self, source, query, folder='inbox', status=''):
        if source == 'gmail':
            self.store.save_connection('alice', 'gmail', {'refresh_token': 'alice-refresh'})
        # build_app() calls Sources.fetch() up front to profile real data
        # before composing the starter spec -- stub it so the build itself
        # doesn't need a live Gmail connection; the query-mangling bug this
        # test targets only happens later, in app_data().
        sample = [{'id': 'm1', 'title': 'hi', 'status': 'unread', 'person': 'someone@example.com', 'date': '2026-01-01'}]
        with patch('workspace_sources.Sources.fetch', lambda self, source, query='', folder='inbox': sample):
            r = self.client.post('/api/apps/build', json={'source': source, 'name': 'Inbox Filter',
                'prompt': 'Show unread messages'}, headers={'X-CSRF-Token': self.csrf})
        self.assertEqual(r.status_code, 200)
        app = r.json['app']
        spec = self.store.app('alice', app['id'])
        spec.pop('id', None)
        spec['pages'][0]['query'] = query
        spec['pages'][0]['folder'] = folder
        spec['pages'][0]['status'] = status
        self.store.save_app('alice', spec, app['id'])
        return app['id'], spec['pages'][0]['id']

    def test_gmail_search_syntax_reaches_fetch_unmangled(self):
        app_id, page_id = self.build_page('gmail',
            'is:unread from:(anthropic.com OR puregym.com OR uber.com)')
        captured = {}

        def fake_fetch(self, source, query='', folder='inbox'):
            captured['query'] = query
            return []

        with patch('workspace_sources.Sources.fetch', fake_fetch):
            r = self.client.get(f'/api/apps/{app_id}/data/{page_id}')
        self.assertEqual(r.status_code, 200)
        # The whole point: is:unread and from:(...) must survive intact --
        # this is what previously got stripped to stray "OR ..." fragments.
        self.assertIn('is:unread', captured['query'])
        self.assertIn('from:(anthropic.com OR puregym.com OR uber.com)', captured['query'])

    def test_non_gmail_source_still_gets_structured_tokens_stripped(self):
        """Guard against regressing the original bug this stripper fixed:
        a generic source's literal-substring search over full record JSON
        must not choke on a 'status:Blocked'-shaped query."""
        app_id, page_id = self.build_page('jira', 'status:Blocked', status='Blocked')
        captured = {}

        def fake_fetch(self, source, query='', folder='inbox'):
            captured['query'] = query
            return []

        with patch('workspace_sources.Sources.fetch', fake_fetch):
            r = self.client.get(f'/api/apps/{app_id}/data/{page_id}')
        self.assertEqual(r.status_code, 200)
        self.assertNotIn('status:Blocked', captured['query'])


if __name__ == '__main__':
    unittest.main()
