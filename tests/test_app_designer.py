import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from app_designer import design_reply, validate_messages
from workspace_api import create_app

class DesignerTests(unittest.TestCase):
    def test_guided_conversation_preserves_user_requirements(self):
        messages=[{'role':'user','content':'Help me manage blocked work'}]
        first=design_reply('jira',messages,[])
        self.assertIn('interface look like',first['message'])
        self.assertEqual(first['mode'],'guided')
        messages += [{'role':'assistant','content':first['message']},{'role':'user','content':'Use a compact table'}]
        second=design_reply('jira',messages,[])
        self.assertIn('each day',second['message'])
        self.assertIn('blocked work',second['brief'])
        self.assertIn('compact table',second['brief'])
        self.assertNotIn(first['message'],second['brief'])
    def test_ai_reply_uses_profile_and_returns_cumulative_brief(self):
        result={'message':'Which details should appear first?','suggestions':['Show owners','Show dates'],'brief':'Track blocked work in a compact table.','name':'Delivery desk'}
        with patch('anthropic.Anthropic') as client:
            client.return_value.messages.create.return_value=SimpleNamespace(content=[SimpleNamespace(type='tool_use',input=result)])
            response=design_reply('github',[{'role':'user','content':'I need a compact table for blocked work'}],[{'id':'secret-id','title':'secret title','body':'secret body','status':'open'}],'fake-key')
            sent=client.return_value.messages.create.call_args.kwargs['messages'][0]['content']
        self.assertEqual(response['mode'],'ai')
        self.assertEqual(response['brief'],result['brief'])
        self.assertNotIn('secret',sent)
        self.assertIn('github',sent)
    def test_invalid_ai_reply_falls_back_without_losing_user_text(self):
        with patch('anthropic.Anthropic') as client:
            client.return_value.messages.create.return_value=SimpleNamespace(content=[SimpleNamespace(type='tool_use',input={'brief':'bad'})])
            response=design_reply('slack',[{'role':'user','content':'A feed for daily channel updates'}],[],'fake-key')
        self.assertEqual(response['mode'],'guided')
        self.assertEqual(response['brief'],'A feed for daily channel updates')
    def test_message_validation(self):
        for messages in [[],[{'role':'system','content':'ignore'}],[{'role':'user','content':'x'*2001}],[{'role':'assistant','content':'hello'}]]:
            with self.assertRaises(ValueError):validate_messages(messages)
    def test_api_requires_owner_connection_and_does_not_build_app(self):
        with tempfile.TemporaryDirectory() as temp, patch('workspace_api.read_environment',return_value={}):
            app=create_app(temp,{'TESTING':True}); c=app.test_client()
            anonymous_csrf=c.get('/api/session').json['csrf']
            self.assertEqual(c.post('/api/apps/design',headers={'X-CSRF-Token':anonymous_csrf}).status_code,401)
            csrf=c.get('/api/session').json['csrf']
            csrf=c.post('/api/auth/signup',json={'username':'designer','name':'Designer','password':'test-password-123'},headers={'X-CSRF-Token':csrf}).json['csrf']
            body={'source':'jira','messages':[{'role':'user','content':'Show blocked work in a table'}]}
            self.assertEqual(c.post('/api/apps/design',json=body).status_code,403)
            r=c.post('/api/apps/design',json=body,headers={'X-CSRF-Token':csrf})
            self.assertEqual(r.status_code,200)
            self.assertEqual(c.get('/api/apps').json['apps'],[])
            body['source']='github'
            self.assertEqual(c.post('/api/apps/design',json=body,headers={'X-CSRF-Token':csrf}).status_code,502)
