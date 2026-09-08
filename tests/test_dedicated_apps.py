"""Dedicated app composition and ownership checks; no live services are called."""
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from workspace_api import create_app
from app_composer import compose_app, starter_app, validate_app

class DedicatedAppTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        with patch('workspace_api.read_environment',return_value={}):
            self.app=create_app(self.temp.name,{'TESTING':True})
        self.store=self.app.extensions['workspace_store']
        self.a,self.b=self.app.test_client(),self.app.test_client()
        self.tokens={}
        for client,name in [(self.a,'alice'),(self.b,'bruno')]:
            token=client.get('/api/session').json['csrf']
            r=client.post('/api/auth/signup',json={'username':name,'name':name,'role':'Operations','password':'test-password-123'},headers={'X-CSRF-Token':token})
            self.assertEqual(r.status_code,200)
            self.tokens[id(client)]=r.json['csrf']
    def tearDown(self):self.temp.cleanup()
    def post(self,client,path,body):return client.post(path,json=body,headers={'X-CSRF-Token':self.tokens[id(client)]})
    def build(self,source='jira',prompt='Show blocked issues first and give me a board'):
        response=self.post(self.a,'/api/apps/build',{'source':source,'name':'Delivery desk','prompt':prompt})
        self.assertEqual(response.status_code,200)
        return response.json['app']
    def test_new_account_starts_with_apps_not_a_data_workspace(self):
        self.assertEqual(self.a.get('/api/apps').json['apps'],[])
    def test_app_has_separate_pages_one_source_and_persists(self):
        app=self.build()
        self.assertEqual(app['source'],'jira')
        self.assertEqual(app['pages'][0]['status'],'Blocked')
        self.assertGreater(len(app['pages']),1)
        self.assertEqual(app['mode'],'starter')
        self.assertTrue(all('source' not in p for p in app['pages']))
        self.assertEqual(self.a.get('/api/apps/'+app['id']).json['app']['title'],'Delivery desk')
        from workspace_store import Store
        self.assertEqual(Store(self.temp.name).app('alice',app['id'])['source'],'jira')
    def test_owner_checked_for_open_data_refine_and_delete(self):
        app=self.build();base='/api/apps/'+app['id']
        self.assertEqual(self.b.get('/api/apps').json['apps'],[])
        for path in [base,base+'/data/'+app['pages'][0]['id']]:self.assertEqual(self.b.get(path).status_code,404)
        self.assertEqual(self.post(self.b,base+'/refine',{'prompt':'Make this app a compact table'}).status_code,404)
        self.assertEqual(self.b.delete(base,headers={'X-CSRF-Token':self.tokens[id(self.b)]}).status_code,404)
    def test_page_data_applies_its_bound_source_and_filter(self):
        app=self.build()
        data=self.a.get('/api/apps/'+app['id']+'/data/'+app['pages'][0]['id']+'?source=gmail').json
        self.assertTrue(data['records'])
        self.assertEqual(data['source'],'jira')
        self.assertTrue(all(r['source']=='jira' and r['status']=='Blocked' for r in data['records']))
    def test_two_apps_for_same_software_have_independent_purpose_and_navigation(self):
        a=self.build(prompt='Show blocked issues first in a focus screen')
        b=self.build(prompt='Give me a table to browse all issues')
        self.assertNotEqual(a['id'],b['id'])
        self.assertNotEqual(a['pages'][0]['layout'],b['pages'][0]['layout'])
        self.assertEqual(len(self.a.get('/api/apps').json['apps']),2)
    def test_build_requires_existing_connected_data_for_live_source(self):
        with patch('app_composer.compose_app') as compose:
            r=self.post(self.a,'/api/apps/build',{'source':'gmail','prompt':'Give me a reply inbox'})
            self.assertEqual(r.status_code,502)
            compose.assert_not_called()
    def test_unavailable_refinement_preserves_existing_app(self):
        app=self.build()
        r=self.post(self.a,'/api/apps/'+app['id']+'/refine',{'prompt':'Make the app compact with a table'})
        self.assertEqual(r.status_code,400)
        self.assertEqual(self.a.get('/api/apps/'+app['id']).json['app'],app)
    def test_invalid_screen_fields_and_cross_source_bindings_rejected(self):
        spec=starter_app('jira','A board for existing work')
        spec['pages'][0]['columns']=['password']
        with self.assertRaises(ValueError):validate_app(spec,'jira','test')
        spec=starter_app('jira','A board for existing work')
        spec['pages'][0]['source']='gmail'
        with self.assertRaises(ValueError):validate_app(spec,'jira','test')
    def test_ai_composes_screens_from_actual_data_profile_without_copying_records(self):
        candidate=starter_app('jira','Show blocked issues first','Delivery desk')
        candidate['pages'][0]['title']='Unblock delivery'
        response=SimpleNamespace(content=[SimpleNamespace(type='tool_use',input=candidate)])
        with patch('anthropic.Anthropic') as client:
            client.return_value.messages.create.return_value=response
            result,mode=compose_app('jira','Show blocked issues first','Delivery desk','Operations',[{'id':'secret-issue','title':'private issue title','status':'Blocked'}],'fake-key')
            sent=client.return_value.messages.create.call_args.kwargs['messages'][0]['content']
            self.assertNotIn('private issue title',sent)
            self.assertNotIn('secret-issue',sent)
            self.assertIn('Blocked',sent)
        self.assertEqual(mode,'ai')
        self.assertEqual(result['pages'][0]['title'],'Unblock delivery')
    def test_bad_ai_output_does_not_erase_previous_app(self):
        previous=starter_app('jira','Show issues')
        with patch('anthropic.Anthropic') as client:
            client.return_value.messages.create.side_effect=RuntimeError('offline')
            with self.assertRaises(ValueError):compose_app('jira','Change all screens','My app','Operations',[],'fake-key',previous)

if __name__=='__main__':unittest.main()
