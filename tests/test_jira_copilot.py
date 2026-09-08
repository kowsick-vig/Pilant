"""Offline checks for Jira copilot query correctness, memory and ownership."""
import tempfile
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch
from workspace_api import create_app
from jira_copilot import answer, execute_plan, validate_plan
from jira_demo import demo_issues


def rows(today=None):
    return [dict(id=i['key'],title=i['summary'],source='jira',status=i['status'],priority=i['priority'],person=i['assignee'] or 'Unassigned',date=i['updated'],detail=i) for i in demo_issues(today)]

class QueryTests(unittest.TestCase):
    def setUp(self):self.today=date(2026,9,7);self.rows=rows(self.today)
    def test_filters_and_followup(self):
        result=answer('Show blocked issues assigned to Priya',self.rows,[],today=self.today)
        self.assertEqual({r['id'] for r in result['records']},{'PIL-101','PIL-102'})
        follow=answer('Only the highest priority ones',self.rows,[{'question':'Show blocked issues assigned to Priya','plan':result['plan']}],today=self.today)
        self.assertEqual([r['id'] for r in follow['records']],['PIL-101'])
        self.assertEqual(follow['total'],1)
    def test_overdue_excludes_done_and_due_today(self):
        result=answer('Which unfinished issues are overdue?',self.rows,[],today=self.today)
        self.assertEqual({r['id'] for r in result['records']},{'PIL-101','PIL-103','PIL-104','PIL-109'})
    def test_breakdown_computed_from_matching_records(self):
        result=answer('Break down open work by assignee',self.rows,[],today=self.today)
        self.assertEqual(result['total'],10)
        self.assertEqual(sum(g['count'] for g in result['groups']),10)
        self.assertEqual(next(g['count'] for g in result['groups'] if g['label']=='Priya Nair'),2)
    def test_blocker_is_the_fixture_fact(self):
        result=answer('Show PIL-104 and why it is blocked',self.rows,[],today=self.today)
        self.assertEqual(result['total'],1)
        self.assertIn('blocked_reason',result['plan']['columns'])
        self.assertIn('vendor confirmation',result['records'][0]['detail']['blocked_reason'])
    def test_empty_results_and_limit_counts(self):
        result=execute_plan({'filters':[{'field':'assignee','operator':'eq','value':'Nonexistent person'}]},self.rows)
        self.assertEqual(result['total'],0)
        self.assertEqual(result['records'],[])
        limited=execute_plan({'limit':2,'metric':'story_points'},self.rows)
        self.assertEqual(len(limited['records']),2)
        self.assertEqual(limited['total'],12)
        self.assertEqual(limited['story_points'],42)
    def test_rejects_unknown_fields_actions_and_invalid_dates(self):
        with self.assertRaises(ValueError):validate_plan({'filters':[{'field':'password','operator':'eq','value':'x'}]})
        with self.assertRaises(ValueError):validate_plan({'filters':[{'field':'due','operator':'before','value':'yesterday'}]})
        with self.assertRaises(ValueError):answer('Delete PIL-104',self.rows,[])
        with self.assertRaises(ValueError):answer('Tell me our revenue',self.rows,[])
    def test_ai_output_runs_only_as_validated_query(self):
        plan=validate_plan({'filters':[{'field':'status','operator':'eq','value':'Blocked'}]})
        with patch('anthropic.Anthropic') as client:
            client.return_value.messages.create.return_value=SimpleNamespace(content=[SimpleNamespace(type='tool_use',input=plan)])
            result=answer('Show blockers',self.rows,[],api_key='fake-key',today=self.today)
        self.assertEqual(result['mode'],'ai')
        self.assertEqual(result['total'],4)
        self.assertTrue(all(r['status']=='Blocked' for r in result['records']))
    def test_any_filters_and_numeric_comparison(self):
        result=execute_plan({'filters':[{'field':'story_points','operator':'gte','value':8},{'field':'key','operator':'eq','value':'PIL-101'}],'match':'any'},self.rows)
        self.assertEqual({r['id'] for r in result['records']},{'PIL-101','PIL-104'})

class CopilotApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        with patch('workspace_api.read_environment',return_value={}):self.app=create_app(self.temp.name,{'TESTING':True})
        self.a,self.b=self.app.test_client(),self.app.test_client();self.tokens={}
        for c,name in [(self.a,'alice'),(self.b,'bruno')]:
            token=c.get('/api/session').json['csrf']
            r=c.post('/api/auth/signup',json={'username':name,'name':name,'password':'test-password-123','role':'Operations'},headers={'X-CSRF-Token':token})
            self.tokens[id(c)]=r.json['csrf']
        self.spec=self.post(self.a,'/api/apps/build',{'source':'jira','name':'Demo','prompt':'Show a board of all Jira issues'}).json['app']
        self.path='/api/apps/'+self.spec['id']+'/copilot'
    def tearDown(self):self.temp.cleanup()
    def post(self,c,path,body):return c.post(path,json=body,headers={'X-CSRF-Token':self.tokens[id(c)]})
    def test_history_and_followup_are_owned_by_app_and_user(self):
        r=self.post(self.a,self.path,{'message':'Show blocked issues assigned to Priya'})
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.json['result']['total'],2)
        follow=self.post(self.a,self.path,{'message':'Only the highest priority ones'})
        self.assertEqual(follow.json['result']['total'],1)
        self.assertEqual(len(self.a.get(self.path).json['turns']),2)
        self.assertEqual(self.b.get(self.path).status_code,404)
        self.assertEqual(self.post(self.b,self.path,{'message':'Show all issues'}).status_code,404)
        other=self.post(self.a,'/api/apps/build',{'source':'jira','name':'Other','prompt':'Show all Jira work'}).json['app']
        self.assertEqual(self.a.get('/api/apps/'+other['id']+'/copilot').json['turns'],[])
    def test_changes_reflected_and_other_user_does_not_see_override(self):
        self.post(self.a,'/api/data/jira/PIL-101/actions',{'action':'status','value':'Done'})
        result=self.post(self.a,self.path,{'message':'Show blocked issues assigned to Priya'}).json['result']
        self.assertEqual([r['id'] for r in result['records']],['PIL-102'])
        self.assertEqual(self.b.get('/api/data/jira/PIL-101').json['record']['status'],'Blocked')
    def test_query_reads_beyond_the_active_screen_and_rejects_csrf(self):
        self.assertEqual(self.a.post(self.path,json={'message':'Show all issues'}).status_code,403)
        r=self.post(self.a,self.path,{'message':'Show PIL-104 and why it is blocked'})
        self.assertEqual(r.json['result']['records'][0]['id'],'PIL-104')
        self.assertIn('Warehouse API',r.json['result']['records'][0]['detail']['blocked_reason'])
    def test_clear_history_and_non_jira_scope(self):
        self.post(self.a,self.path,{'message':'Show all issues'})
        r=self.a.delete(self.path,headers={'X-CSRF-Token':self.tokens[id(self.a)]})
        self.assertEqual(r.status_code,200)
        self.assertEqual(self.a.get(self.path).json['turns'],[])
        other=self.post(self.a,'/api/apps/build',{'source':'helpdesk','name':'Other','prompt':'Show all support tickets'}).json['app']
        self.assertEqual(self.post(self.a,'/api/apps/'+other['id']+'/copilot',{'message':'Show all issues'}).status_code,200)

if __name__=='__main__':unittest.main()
