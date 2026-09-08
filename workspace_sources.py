"""Request-scoped adapters: credentials always come from the authenticated owner's store.
Uses existing Jira/helpdesk fixtures and Gmail MIME parsing, never legacy global tokens.
"""
import base64
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.utils import parseaddr

SOURCES = {
    'gmail': {'label': 'Gmail', 'kind': 'live', 'layout': 'inbox', 'description': 'Read, search, and reply to your email.'},
    'github': {'label': 'GitHub', 'kind': 'live', 'layout': 'board', 'description': 'Your repository issues, with room to focus.'},
    'slack': {'label': 'Slack', 'kind': 'live', 'layout': 'feed', 'description': 'Catch up on a channel without the noise.'},
    'jira': {'label': 'Jira', 'kind': 'sample', 'layout': 'board', 'description': 'Try a project board with your own sample issue changes.'},
    'helpdesk': {'label': 'Helpdesk', 'kind': 'sample', 'layout': 'inbox', 'description': 'Explore a support queue with sample tickets.'},
}
JIRA_STATUSES = ['To Do', 'In Progress', 'In Review', 'Blocked', 'Done']
HELPDESK_STATUSES = ['open', 'in_progress', 'escalated', 'resolved']


class SourceError(Exception):
    pass


def http(url, token=None, payload=None, form=None, method=None):
    headers = {'Accept': 'application/json', 'User-Agent': 'Pilant-Workspace'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers['Content-Type'] = 'application/json'
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return json.load(response)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise SourceError('Access was denied. Reconnect this source and check its permissions.') from None
        if e.code == 429:
            raise SourceError('This source is rate limited. Please try again shortly.') from None
        raise SourceError(f'The source returned an error (HTTP {e.code}). Check the connection settings.') from None
    except (urllib.error.URLError, TimeoutError, ValueError):
        raise SourceError('Could not reach this source. Please try again.') from None


def record(source, id, title, **kwargs):
    return dict(source=source, id=str(id), title=title or '(untitled)', **kwargs)


class Sources:
    def __init__(self, store, owner, oauth):
        self.store, self.owner, self.oauth = store, owner, oauth
        self._gmail_token = None

    def config(self, source):
        value = self.store.connection(self.owner, source)
        if not value:
            raise SourceError('Connect this source in Connections to see your data.')
        return value

    def gmail(self, path, payload=None, method=None):
        c = self.config('gmail')
        if not self._gmail_token:
            token = http('https://oauth2.googleapis.com/token', form={
                'client_id': self.oauth['client_id'], 'client_secret': self.oauth['client_secret'],
                'refresh_token': c['refresh_token'], 'grant_type': 'refresh_token'})
            self._gmail_token = token['access_token']
        return http('https://gmail.googleapis.com/gmail/v1/users/me' + path, self._gmail_token, payload, method=method)

    def fetch(self, source, query='', folder='inbox'):
        if source == 'jira':
            from connectors_jira import get_issues
            from jira_demo import demo_issues
            changed = self.store.changes(self.owner, source)
            rows = []
            for item in get_issues() + demo_issues():
                item.update(changed.get(item['key'], {}))
                rows.append(record(source, item['key'], item['summary'], status=item['status'],
                    priority=item['priority'], person=item.get('assignee') or 'Unassigned',
                    date=item.get('updated'), detail=item))
        elif source == 'helpdesk':
            from connectors_helpdesk import get_tickets
            changed = self.store.changes(self.owner, source)
            rows = []
            for item in get_tickets():
                item = dict(item, **changed.get(item['id'], {}))
                rows.append(record(source, item['id'], item['subject'], status=item['status'],
                    priority=item['priority'], person=item['requester'], date=item['created'], detail=item))
        elif source == 'github':
            c = self.config(source)
            repo = c['repo']
            data = http(f'https://api.github.com/repos/{repo}/issues?state=all&per_page=100', c.get('token'))
            rows = [record(source, i['number'], i['title'], status=i['state'],
                person=(i.get('assignee') or {}).get('login') or 'Unassigned', date=i['updated_at'],
                body=i.get('body') or '', url=i['html_url'], labels=[l['name'] for l in i.get('labels', [])])
                for i in data if 'pull_request' not in i]
        elif source == 'slack':
            c = self.config(source)
            data = http('https://slack.com/api/conversations.history?' + urllib.parse.urlencode({'channel': c['channel'], 'limit': 100}), c['token'])
            if not data.get('ok'):
                raise SourceError('Slack could not read this channel. Check the channel ID, bot membership, and history permissions.')
            rows = [record(source, m['ts'], m.get('text', '')[:120], body=m.get('text', ''),
                person=m.get('user') or 'Slack', status='message', date=datetime.fromtimestamp(float(m['ts']), timezone.utc).isoformat(),
                url=f"https://slack.com/archives/{c['channel']}/p{m['ts'].replace('.', '')}")
                for m in data.get('messages', []) if not m.get('subtype')]
        elif source == 'gmail':
            folders = {'inbox': 'in:inbox', 'sent': 'in:sent', 'starred': 'is:starred', 'all': ''}
            search = ' '.join([folders.get(folder, 'in:inbox'), query]).strip()
            raw = self.gmail('/messages?' + urllib.parse.urlencode({'q': search, 'maxResults': 30}))
            from connectors_gmail import _message_dict_from_raw
            rows = []
            for m in raw.get('messages', []):
                item = self.gmail('/messages/' + urllib.parse.quote(m['id'], safe='') + '?format=metadata')
                parsed = _message_dict_from_raw(item)
                rows.append(record(source, parsed['id'], parsed['subject'], body=item.get('snippet', ''),
                    person=parsed['from'], date=parsed['date'], status='unread' if parsed['unread'] else 'read', starred=parsed['starred']))
            return rows
        else:
            raise SourceError('Unknown source.')
        if query:
            words = query.lower().split()
            rows = [r for r in rows if all(w in json.dumps(r).lower() for w in words)]
        return rows

    def detail(self, source, id):
        if source == 'gmail':
            from connectors_gmail import _message_dict_from_raw
            parsed = _message_dict_from_raw(self.gmail('/messages/' + urllib.parse.quote(id, safe='') + '?format=full'))
            return record(source, id, parsed['subject'], body=parsed['body'], person=parsed['from'],
                date=parsed['date'], status='unread' if parsed['unread'] else 'read', starred=parsed['starred'], detail=parsed)
        return next((r for r in self.fetch(source) if r['id'] == id), None)

    def action(self, source, id, action, value):
        if source in ('jira', 'helpdesk') and action == 'status':
            if value not in (JIRA_STATUSES if source == 'jira' else HELPDESK_STATUSES):
                raise ValueError('Choose a valid status.')
            if not self.detail(source, id):
                raise ValueError('Record not found.')
            self.store.change(self.owner, source, id, {'status': value})
            return {'ok': True}
        if source == 'gmail':
            path = '/messages/' + urllib.parse.quote(id, safe='')
            if action == 'reply':
                original = self.detail(source, id)['detail']
                msg = MIMEText(value, 'plain', 'utf-8')
                msg['To'] = parseaddr(original['from'])[1]
                msg['Subject'] = original['subject'] if original['subject'].lower().startswith('re:') else 'Re: ' + original['subject']
                if original.get('message_id_header'):
                    msg['In-Reply-To'] = original['message_id_header']
                    msg['References'] = ((original.get('references') or '') + ' ' + original['message_id_header']).strip()
                return self.gmail('/messages/send', {'raw': base64.urlsafe_b64encode(msg.as_bytes()).decode(), 'threadId': original['threadId']})
            if action in ('read', 'star', 'unstar', 'archive'):
                add, remove = [], []
                if action == 'read': remove = ['UNREAD']
                if action == 'star': add = ['STARRED']
                if action == 'unstar': remove = ['STARRED']
                if action == 'archive': remove = ['INBOX']
                return self.gmail(path + '/modify', {'addLabelIds': add, 'removeLabelIds': remove})
        raise ValueError('This action is not supported for this source.')
