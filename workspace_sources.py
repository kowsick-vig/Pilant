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
from email.utils import parseaddr, parsedate_to_datetime

SOURCES = {
    'gmail': {'label': 'Gmail', 'kind': 'live', 'layout': 'inbox', 'description': 'Read, search, and reply to your email.'},
    'github': {'label': 'GitHub', 'kind': 'live', 'layout': 'board', 'description': 'Your repository issues, with room to focus.'},
    'slack': {'label': 'Slack', 'kind': 'live', 'layout': 'feed', 'description': 'Catch up on a channel without the noise.'},
    'jira': {'label': 'Jira', 'kind': 'sample', 'layout': 'board', 'description': 'Try a project board with your own sample issue changes.'},
    'helpdesk': {'label': 'Helpdesk', 'kind': 'sample', 'layout': 'inbox', 'description': 'Explore a support queue with sample tickets.'},
    'splunk': {'label': 'Splunk', 'kind': 'sample', 'layout': 'table', 'description': 'Try a notable-events queue with sample security and ops alerts.'},
    'crm': {'label': 'CRM', 'kind': 'sample', 'layout': 'board', 'description': 'Try a sales follow-up queue with sample leads, deals, and tasks.'},
}
JIRA_STATUSES = ['To Do', 'In Progress', 'In Review', 'Blocked', 'Done']
HELPDESK_STATUSES = ['open', 'in_progress', 'escalated', 'resolved']
SPLUNK_STATUSES = ['new', 'investigating', 'escalated', 'resolved']
CRM_STATUSES = ['open', 'in_progress', 'overdue', 'done']


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


def _date_ts(value):
    """Turn a row's `date` into a comparable timestamp, or None if it can't be
    parsed. Sources don't agree on date shape: Jira/GitHub/Slack use ISO-8601
    (sorts fine as a string, but we want real chronological order, not text
    order), while Gmail's `date` is the raw RFC 2822 `Date:` header (e.g.
    "Tue, 8 Sep 2026 16:36:00 +0000") — NOT lexically sortable at all (day
    numbers aren't zero-padded, month names aren't alphabetical-by-date), so
    treating it as a plain string anywhere would silently scramble the order.
    Tries the email-header format first (the case that actually breaks on a
    naive sort), then falls back to ISO-8601."""
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        if dt is not None:
            return dt.timestamp()
    except (TypeError, ValueError):
        pass
    try:
        from datetime import datetime as _datetime
        iso = value[:-1] + '+00:00' if value.endswith('Z') else value
        return _datetime.fromisoformat(iso).timestamp()
    except (TypeError, ValueError):
        return None


def _by_date_desc(rows):
    """Sort newest-first by real date, undated rows pushed to the end
    (never dropped) instead of sorting arbitrarily wherever a naive
    string/None comparison would land them."""
    def key(r):
        ts = _date_ts(r.get('date'))
        return (ts is None, -(ts or 0))
    return sorted(rows, key=key)


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
        elif source == 'splunk':
            from connectors_splunk import get_events
            changed = self.store.changes(self.owner, source)
            rows = []
            for item in get_events():
                item = dict(item, **changed.get(item['id'], {}))
                rows.append(record(source, item['id'], item['title'], status=item['status'],
                    priority=item['severity'], person=item['owner'], date=item['trigger_time'], detail=item))
        elif source == 'crm':
            from connectors_crm import get_tasks
            changed = self.store.changes(self.owner, source)
            rows = []
            for item in get_tasks():
                item = dict(item, **changed.get(item['id'], {}))
                rows.append(record(source, item['id'], item['title'], status=item['status'],
                    priority=item['priority'], person=item['owner'], date=item['due'], detail=item))
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
            return _by_date_desc(rows)
        else:
            raise SourceError('Unknown source.')
        if query:
            words = query.lower().split()
            rows = [r for r in rows if all(w in json.dumps(r).lower() for w in words)]
        return _by_date_desc(rows)

    def detail(self, source, id):
        if source == 'gmail':
            from connectors_gmail import _message_dict_from_raw
            parsed = _message_dict_from_raw(self.gmail('/messages/' + urllib.parse.quote(id, safe='') + '?format=full'))
            return record(source, id, parsed['subject'], body=parsed['body'], person=parsed['from'],
                date=parsed['date'], status='unread' if parsed['unread'] else 'read', starred=parsed['starred'],
                attachments=[{'filename': a['filename'], 'mime_type': a['mime_type'], 'size': a['size'],
                    'attachment_id': a['attachment_id']} for a in parsed['attachments']], detail=parsed)
        return next((r for r in self.fetch(source) if r['id'] == id), None)

    def gmail_attachment(self, message_id, attachment_id):
        """Fetch one attachment's real bytes for the message it belongs to,
        plus its filename/mime type (looked up fresh from the message's own
        parts, since messages.attachments.get returns only {size, data} —
        never trust a filename/type the client claims for what we serve)."""
        from connectors_gmail import _walk_attachments
        full = self.gmail('/messages/' + urllib.parse.quote(message_id, safe='') + '?format=full')
        meta = next((a for a in _walk_attachments(full.get('payload') or {}) if a['attachment_id'] == attachment_id), None)
        if not meta:
            raise SourceError('Attachment not found.')
        data = self.gmail('/messages/' + urllib.parse.quote(message_id, safe='') +
            '/attachments/' + urllib.parse.quote(attachment_id, safe=''))
        padded = data['data'] + '=' * (-len(data['data']) % 4)
        return base64.urlsafe_b64decode(padded), meta['filename'], meta['mime_type']

    def action(self, source, id, action, value):
        if source in ('jira', 'helpdesk', 'splunk', 'crm') and action == 'status':
            statuses = {'jira': JIRA_STATUSES, 'helpdesk': HELPDESK_STATUSES,
                'splunk': SPLUNK_STATUSES, 'crm': CRM_STATUSES}[source]
            if value not in statuses:
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
