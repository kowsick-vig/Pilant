import tempfile
import unittest
from unittest.mock import patch
from app_copilot import answer
from workspace_api import create_app
from workspace_sources import SOURCES, SourceError

class UniversalCopilotTests(unittest.TestCase):
    def test_normalized_records_work_for_any_source(self):
        for source in ['github','gmail','slack','helpdesk','future_connector']:
            with self.subTest(source=source):
                rows=[dict(id='1',title='Launch ready',status='open',person='alex',date='2026-09-08',source=source),dict(id='2',title='Bug fixed',status='closed',person='sam',date='2026-09-07',source=source)]
                r=answer('Show open records',rows,[],source=source,sample=False,scope='Test retrieval')
                self.assertEqual([x['id'] for x in r['records']],['1'])
                self.assertFalse(r['sample'])
                self.assertEqual(r['scope'],'Test retrieval')
                r=answer('Find records containing "bug"',rows,[],source=source)
                self.assertEqual([x['id'] for x in r['records']],['2'])
                r=answer('Break down by person',rows,[],source=source)
                self.assertEqual(sum(g['count'] for g in r['groups']),2)
    def test_every_registered_source_uses_app_bound_owner_adapter(self):
        with tempfile.TemporaryDirectory() as temp, patch('workspace_api.read_environment',return_value={}):
            app=create_app(temp,{'TESTING':True}); client=app.test_client()
            token=client.get('/api/session').json['csrf']
            token=client.post('/api/auth/signup',json={'username':'universal','name':'Universal','password':'test-password-123'},headers={'X-CSRF-Token':token}).json['csrf']
            store=app.extensions['workspace_store']
            for source in SOURCES:
                with self.subTest(source=source):
                    spec=store.save_app('universal',{'source':source})
                    path='/api/apps/'+spec['id']+'/copilot'
                    calls=[]
                    def fetch(adapter,requested,query='',folder='inbox'):
                        calls.append((adapter.owner,requested,folder))
                        return [dict(id='1',title='Launch',status='open',person='Alex',source=source)]
                    with patch('workspace_sources.Sources.fetch',fetch):
                        response=client.post(path,json={'message':'Show all records','source':'jira'},headers={'X-CSRF-Token':token})
                    self.assertEqual(response.status_code,200,response.json)
                    self.assertEqual(calls,[('universal',source,'all')])
                    self.assertEqual(response.json['result']['sample'],SOURCES[source]['kind']=='sample')
                    self.assertEqual(len(client.get(path).json['turns']),1)
                    with patch('workspace_sources.Sources.fetch',side_effect=SourceError('Reconnect this source')):
                        failed=client.post(path,json={'message':'Show all records'},headers={'X-CSRF-Token':token})
                    self.assertEqual(failed.status_code,502)
                    self.assertEqual(len(client.get(path).json['turns']),1)
                    self.assertEqual(app.test_client().get(path).status_code,401)
