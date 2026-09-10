"""React workspace API. Run: python workspace_api.py (http://127.0.0.1:5010).
Independent of legacy Studio's global conversations, OAuth tokens and demo login.
"""
import json
import os
import re
import secrets
import sqlite3
import time
import urllib.parse
from functools import wraps
from pathlib import Path
from flask import Flask, jsonify, request, session, send_from_directory, redirect, Response
from werkzeug.security import check_password_hash, generate_password_hash
from workspace_store import Store
from workspace_sources import SOURCES, Sources, SourceError, http

ROOT = Path(__file__).resolve().parent
LAYOUTS = ['inbox', 'board', 'feed', 'table', 'focus']
ROLES = ['Product & engineering', 'Customer support', 'Operations', 'Leadership']

_STRUCTURED_QUERY_TOKEN = re.compile(r'\b\w+:\S+')


def _literal_query(text):
    """Strip any 'field:value'-shaped token out of a page's stored `query`.

    app_composer.py's own system prompt tells the model query is "a literal
    search term, not an instruction" — a screen's real filter is the
    dedicated `status` field. The model doesn't always follow that: it can
    emit something like query="status:Blocked" alongside status="Blocked".
    Sources.fetch() (workspace_sources.py) then treats the WHOLE query as
    literal text to find inside each record's JSON, and "status:blocked"
    (no space around the colon) never appears in `{"status": "Blocked", ...}`
    — so every row gets filtered out and the screen silently shows zero
    records even though matching data exists (reproduced: a "Blocked"-status
    screen over the Jira sample data, which has 6 genuinely blocked issues,
    renders "Nothing here right now"). Stripping any such token before it
    reaches fetch() leaves the real status filter (applied two lines below,
    in app_data()) to do its job, without touching the AI prompt/schema or
    the generic substring search genuinely free-text queries still rely on.

    Gmail is the deliberate exception (see app_data()): Sources.fetch()
    forwards a Gmail page's query straight to the real Gmail search API
    (workspace_sources.py's gmail branch returns before the generic
    substring-match block below runs at all), and Gmail's API genuinely
    understands "is:unread"/"from:(a OR b OR c)" syntax — which is exactly
    what app_composer.py's system prompt tells the model it may use for
    Gmail screens ("query may use Gmail search syntax"). Running THIS
    stripper on a Gmail query first was silently gutting that syntax
    before it ever reached Gmail (reproduced: a screen meant to filter
    "is:unread from:(anthropic.com OR puregym.com OR uber.com)" had both
    field:value tokens stripped, leaving stray "OR ..." fragments that
    matched almost every unread message instead of the intended senders).
    """
    return _STRUCTURED_QUERY_TOKEN.sub('', text or '').strip()


def read_environment():
    values = dict(os.environ)
    path = ROOT / '.env'
    if path.exists():
        for line in path.read_text().splitlines():
            if '=' in line and not line.strip().startswith('#'):
                key, value = line.split('=', 1)
                values.setdefault(key.strip(), value.strip().strip('\"\''))
    return values


def create_app(data_dir=None, config=None):
    env = read_environment()
    app = Flask(__name__, static_folder=None)
    store = Store(data_dir or env.get('PILANT_WORKSPACE_DATA') or ROOT / '.workspace')
    key_path = store.directory / 'session.key'
    if not key_path.exists():
        try:
            fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as f: f.write(secrets.token_hex(32))
        except FileExistsError:
            pass
    app.config.update(SECRET_KEY=key_path.read_text(), SESSION_COOKIE_NAME='pilant_workspace',
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax', MAX_CONTENT_LENGTH=65536)
    if config: app.config.update(config)
    app.extensions['workspace_store'] = store
    oauth = {'client_id': env.get('GMAIL_CLIENT_ID', ''), 'client_secret': env.get('GMAIL_CLIENT_SECRET', '')}
    oauth_redirect = env.get('WORKSPACE_GMAIL_REDIRECT_URI', 'http://127.0.0.1:5010/api/oauth/gmail/callback')
    attempts = {}
    agent_attempts = {}
    dummy_hash = generate_password_hash(secrets.token_hex(20))

    def agent_rate_limited(owner):
        # Same shape as the login attempt limiter above, applied to
        # plan/step actions per owner so a runaway client (or a scripted
        # approval loop) can't hammer connected write/message tools.
        now = time.time()
        recent = [t for t in agent_attempts.get(owner, []) if now - t < 60]
        agent_attempts[owner] = recent
        if len(recent) >= 40:
            return True
        recent.append(now)
        return False

    def csrf():
        if 'csrf' not in session: session['csrf'] = secrets.token_urlsafe(32)
        return session['csrf']

    def public_user(user):
        return {'id': user['id'], 'name': user['name'], 'role': user['role'], 'preferences': json.loads(user['preferences'])}

    def authenticated(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not session.get('user') or not store.user(session['user']):
                return jsonify(error='Please sign in.'), 401
            return fn(*args, **kwargs)
        return wrapper

    def adapters():
        return Sources(store, session['user'], oauth)

    def ensure_source(source):
        if source not in SOURCES: raise ValueError('Unknown source.')

    @app.before_request
    def protect_mutations():
        if request.path.startswith('/api/') and request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            expected = session.get('csrf', '')
            if not expected or not secrets.compare_digest(expected, request.headers.get('X-CSRF-Token', '')):
                return jsonify(error='Your session changed. Refresh the page and try again.'), 403

    @app.after_request
    def headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['X-Frame-Options'] = 'DENY'
        if request.path.startswith('/api/'): response.headers['Cache-Control'] = 'no-store'
        return response

    @app.errorhandler(ValueError)
    def bad_request(error): return jsonify(error=str(error)), 400

    @app.errorhandler(SourceError)
    def source_error(error): return jsonify(error=str(error)), 502

    @app.get('/api/session')
    def current_session():
        user = store.user(session['user']) if session.get('user') else None
        return jsonify(user=public_user(user) if user else None, csrf=csrf(), roles=ROLES)

    @app.post('/api/auth/<action>')
    def auth(action):
        if action == 'logout':
            session.clear()
            return jsonify(csrf=csrf())
        if action not in ('login', 'signup'): raise ValueError('Unknown action.')
        data = request.get_json(silent=True) or {}
        username = str(data.get('username', '')).strip().lower()
        password = str(data.get('password', ''))
        key = request.remote_addr or 'local'
        now = time.time()
        attempts[key] = [t for t in attempts.get(key, []) if now - t < 60]
        if len(attempts[key]) >= 15: return jsonify(error='Too many attempts. Try again in one minute.'), 429
        attempts[key].append(now)
        if not re.fullmatch(r'[a-z0-9_.@+-]{3,100}', username) or len(password) > 200:
            raise ValueError('Use a username of 3–100 letters, numbers, or email characters.')
        if action == 'signup':
            if len(password) < 10: raise ValueError('Use at least 10 characters for your password.')
            name = str(data.get('name', '')).strip()[:80]
            role = data.get('role', ROLES[0])
            if not name or role not in ROLES: raise ValueError('Enter your name and choose a role.')
            try: store.add_user(username, name, generate_password_hash(password), role)
            except sqlite3.IntegrityError: return jsonify(error='That username is already registered.'), 409
        user = store.user(username)
        if not check_password_hash(user['password'] if user else dummy_hash, password):
            return jsonify(error='Username or password is incorrect.'), 401
        session.clear()
        session['user'] = username
        return jsonify(user=public_user(user), csrf=csrf())

    @app.get('/api/connections')
    @authenticated
    def connections():
        rows = []
        for source, metadata in SOURCES.items():
            c = store.connection(session['user'], source)
            rows.append(dict(metadata, id=source, configured=bool(c) or metadata['kind'] == 'sample',
                account=(c or {}).get('account') or (c or {}).get('repo') or (c or {}).get('channel') or '',
                oauth_ready=bool(oauth['client_id'] and oauth['client_secret'])))
        return jsonify(connections=rows)

    @app.put('/api/connections/<source>')
    @authenticated
    def connect(source):
        data = request.get_json(silent=True) or {}
        if source == 'github':
            repo = str(data.get('repo', '')).strip()
            if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo): raise ValueError('Enter a repository as owner/name.')
            value = {'repo': repo, 'token': str(data.get('token', '')).strip()}
            http(f'https://api.github.com/repos/{repo}', value['token'])
        elif source == 'slack':
            channel, token = str(data.get('channel', '')).strip(), str(data.get('token', '')).strip()
            if not re.fullmatch(r'[CG][A-Z0-9]+', channel) or not token: raise ValueError('Enter a channel ID and bot token.')
            result = http('https://slack.com/api/conversations.history?' + urllib.parse.urlencode({'channel': channel, 'limit': 1}), token)
            if not result.get('ok'): raise ValueError('Slack could not read this channel. Check the bot token and channel membership.')
            value = {'channel': channel, 'token': token}
        else: raise ValueError('Use Google sign-in for Gmail. Sample sources need no connection.')
        if len(value.get('token', '')) > 8192: raise ValueError('Token is too long.')
        store.save_connection(session['user'], source, value)
        return jsonify(ok=True)

    @app.delete('/api/connections/<source>')
    @authenticated
    def disconnect(source):
        ensure_source(source)
        store.disconnect(session['user'], source)
        return jsonify(ok=True)

    @app.post('/api/oauth/gmail')
    @authenticated
    def gmail_start():
        if not oauth['client_id'] or not oauth['client_secret']:
            raise ValueError('Gmail OAuth is not configured on this server. Set GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET.')
        state = secrets.token_urlsafe(32)
        session['oauth_state'] = state
        session['oauth_started'] = time.time()
        scopes = ' '.join('https://www.googleapis.com/auth/' + s for s in ['gmail.readonly', 'gmail.send', 'gmail.modify'])
        return jsonify(url='https://accounts.google.com/o/oauth2/v2/auth?' + urllib.parse.urlencode({
            'client_id': oauth['client_id'], 'redirect_uri': oauth_redirect, 'response_type': 'code',
            'scope': scopes, 'access_type': 'offline', 'prompt': 'consent', 'state': state}))

    @app.get('/api/oauth/gmail/callback')
    @authenticated
    def gmail_callback():
        expected = session.pop('oauth_state', '')
        started = session.pop('oauth_started', 0)
        if not expected or not secrets.compare_digest(expected, request.args.get('state', '')) or time.time() - started > 600:
            return redirect('/?connection_error=Google+sign-in+expired.+Please+try+again.')
        if not request.args.get('code'):
            return redirect('/?connection_error=Google+sign-in+was+cancelled.')
        try:
            token = http('https://oauth2.googleapis.com/token', form={**oauth, 'code': request.args['code'], 'grant_type': 'authorization_code', 'redirect_uri': oauth_redirect})
            if not token.get('refresh_token'): raise SourceError('Google did not return an offline token. Try connecting again.')
            account = http('https://gmail.googleapis.com/gmail/v1/users/me/profile', token['access_token'])
            store.save_connection(session['user'], 'gmail', {'refresh_token': token['refresh_token'], 'account': account['emailAddress']})
            return redirect('/?connected=gmail')
        except SourceError:
            return redirect('/?connection_error=Google+connection+failed.+Please+try+again.')

    @app.get('/api/data/<source>')
    @authenticated
    def data(source):
        ensure_source(source)
        rows = adapters().fetch(source, request.args.get('q', '')[:300], request.args.get('folder', 'inbox'))
        return jsonify(records=rows, scope='Up to 30 messages' if source == 'gmail' else 'Up to 100 recent records' if source in ('github','slack') else 'Sample workspace data')

    @app.get('/api/data/<source>/<id>')
    @authenticated
    def detail(source, id):
        ensure_source(source)
        row = adapters().detail(source, id)
        if row is None: return jsonify(error='Record not found.'), 404
        return jsonify(record=row)

    @app.post('/api/data/<source>/<id>/actions')
    @authenticated
    def action(source, id):
        ensure_source(source)
        body = request.get_json(silent=True) or {}
        value = body.get('value', '')
        if not isinstance(value, str) or len(value) > 20000: raise ValueError('Invalid action value.')
        if body.get('action') == 'reply' and not value.strip(): raise ValueError('Write a reply before sending.')
        return jsonify(result=adapters().action(source, id, body.get('action'), value))

    @app.get('/api/views')
    @authenticated
    def views(): return jsonify(views=store.views(session['user']))

    def view_spec(data):
        sources = data.get('sources')
        if not isinstance(sources, list) or not sources or len(sources) > 5 or any(s not in SOURCES for s in sources):
            raise ValueError('Choose one or more supported sources.')
        if data.get('layout') not in LAYOUTS: raise ValueError('Choose a supported layout.')
        return {'title': str(data.get('title') or 'My workspace')[:80], 'sources': list(dict.fromkeys(sources)),
                'layout': data['layout'], 'query': str(data.get('query', ''))[:300]}

    @app.post('/api/views')
    @authenticated
    def save_view():
        spec = view_spec(request.get_json(silent=True) or {})
        return jsonify(id=store.save_view(session['user'], spec), **spec)

    @app.delete('/api/views/<id>')
    @authenticated
    def delete_view(id):
        if not store.delete_view(session['user'], id): return jsonify(error='View not found.'), 404
        return jsonify(ok=True)

    @app.put('/api/preferences')
    @authenticated
    def preferences():
        data = request.get_json(silent=True) or {}
        if data.get('layout') not in LAYOUTS or data.get('density') not in ('comfortable', 'compact'):
            raise ValueError('Invalid preference.')
        store.preferences(session['user'], {'layout': data['layout'], 'density': data['density']})
        return jsonify(ok=True)

    @app.post('/api/compose')
    @authenticated
    def compose():
        data = request.get_json(silent=True) or {}
        prompt = str(data.get('prompt', '')).strip()[:1000]
        if not prompt: raise ValueError('Describe the workspace you want.')
        current = data.get('sources') or ['jira']
        if not isinstance(current, list) or any(s not in SOURCES for s in current): raise ValueError('Invalid sources.')
        text = prompt.lower()
        chosen = [s for s in SOURCES if s in text]
        if not chosen and any(w in text for w in ['email','inbox','mail']): chosen = ['gmail']
        if not chosen and 'ticket' in text: chosen = ['helpdesk']
        chosen = chosen or current
        layout = next((l for l in LAYOUTS if l in text), SOURCES[chosen[0]]['layout'])
        spec = {'title': prompt[:80], 'sources': chosen, 'layout': layout, 'query': ''}
        # AI composes structure only; records and actions remain bound to trusted APIs.
        # No external source text or connection secrets are included in this request.
        mode = 'keyword'
        if env.get('ANTHROPIC_API_KEY'):
            try:
                import anthropic
                client = anthropic.Anthropic(api_key=env['ANTHROPIC_API_KEY'], timeout=20, max_retries=0)
                schema = {'type':'object','properties':{
                    'title':{'type':'string'},'sources':{'type':'array','items':{'type':'string','enum':list(SOURCES)},'minItems':1},
                    'layout':{'type':'string','enum':LAYOUTS}, 'query':{'type':'string'}},
                    'required':['title','sources','layout','query'],'additionalProperties':False}
                response = client.messages.create(model='claude-haiku-4-5', max_tokens=500,
                    system='Choose a functional workspace. inbox=split list/detail, board=status columns, feed=message stream, table=comparison, focus=one record at a time. query is a literal search term ONLY when explicitly requested, otherwise empty. Do not invent data. Role is a layout preference, never a permission.',
                    messages=[{'role':'user','content':json.dumps({'request':prompt,'current_sources':current,'role':store.user(session['user'])['role']})}],
                    tools=[{'name':'workspace','description':'Configure the interactive workspace','input_schema':schema}], tool_choice={'type':'tool','name':'workspace'})
                candidate = next(b.input for b in response.content if b.type == 'tool_use')
                spec = view_spec(candidate)
                mode = 'ai'
            except Exception:
                mode = 'keyword'
        return jsonify(spec=view_spec(spec), mode=mode)

    @app.get('/api/apps')
    @authenticated
    def list_apps():
        return jsonify(apps=store.apps(session['user']))

    @app.get('/api/apps/<app_id>')
    @authenticated
    def get_app(app_id):
        spec = store.app(session['user'], app_id)
        if not spec: return jsonify(error='App not found.'), 404
        return jsonify(app=spec)

    @app.post('/api/apps/design')
    @authenticated
    def design_app():
        from app_designer import design_reply, validate_messages
        body = request.get_json(silent=True) or {}
        source = body.get('source')
        ensure_source(source)
        messages = validate_messages(body.get('messages'))
        records = adapters().fetch(source)
        return jsonify(design_reply(source, messages, records, env.get('ANTHROPIC_API_KEY')))

    @app.post('/api/apps/build')
    @authenticated
    def build_app():
        from app_composer import compose_app
        body = request.get_json(silent=True) or {}
        source, prompt = body.get('source'), str(body.get('prompt') or '').strip()[:2000]
        ensure_source(source)
        if len(prompt) < 10: raise ValueError('Describe what you want to do with this software in a little more detail.')
        records = adapters().fetch(source)
        spec, mode = compose_app(source,prompt,str(body.get('name') or '').strip()[:80],
            store.user(session['user'])['role'],records,env.get('ANTHROPIC_API_KEY'))
        spec['mode'] = mode
        return jsonify(app=store.save_app(session['user'],spec))

    @app.post('/api/apps/<app_id>/refine')
    @authenticated
    def refine_app(app_id):
        from app_composer import compose_app
        previous = store.app(session['user'],app_id)
        if not previous: return jsonify(error='App not found.'),404
        body = request.get_json(silent=True) or {}
        prompt = str(body.get('prompt') or '').strip()[:2000]
        if len(prompt) < 10: raise ValueError('Describe how this app should change.')
        if not env.get('ANTHROPIC_API_KEY'): raise ValueError('App refinement needs an AI connection. Your current app is unchanged.')
        records = adapters().fetch(previous['source'])
        spec, mode = compose_app(previous['source'],prompt,previous['title'],store.user(session['user'])['role'],
            records,env.get('ANTHROPIC_API_KEY'),previous)
        spec['mode'] = mode
        return jsonify(app=store.save_app(session['user'],spec,app_id))

    @app.delete('/api/apps/<app_id>')
    @authenticated
    def delete_app(app_id):
        if not store.delete_app(session['user'],app_id): return jsonify(error='App not found.'),404
        return jsonify(ok=True)

    @app.get('/api/apps/<app_id>/data/<page_id>')
    @authenticated
    def app_data(app_id,page_id):
        spec = store.app(session['user'],app_id)
        if not spec: return jsonify(error='App not found.'),404
        page = next((p for p in spec['pages'] if p['id']==page_id),None)
        if not page: return jsonify(error='Screen not found.'),404
        # Gmail's own fetch() forwards this straight to the real Gmail search
        # API, which genuinely understands "is:unread"/"from:(...)" syntax —
        # see _literal_query()'s docstring for why it must NOT run on Gmail.
        raw_query = page['query'] if spec['source'] == 'gmail' else _literal_query(page['query'])
        query = ' '.join(filter(None,[raw_query,request.args.get('q','')[:300]]))
        rows = adapters().fetch(spec['source'],query,page['folder'])
        if page['status']: rows = [r for r in rows if r.get('status')==page['status']]
        # adapters().fetch() already returns rows newest-first by real date; a page
        # built (or, for an app made before `sort` existed, defaulted via .get) with
        # sort='oldest' just runs that same order backwards, rather than re-sorting.
        if page.get('sort') == 'oldest': rows = list(reversed(rows))
        return jsonify(records=rows, source=spec['source'], sample=SOURCES[spec['source']]['kind']=='sample',
            scope='Up to 30 matching messages' if spec['source']=='gmail' else 'Up to 100 recent records' if spec['source'] in ['slack','github'] else 'Sample records')

    @app.get('/api/data/gmail/<message_id>/attachments/<attachment_id>')
    @authenticated
    def gmail_attachment(message_id, attachment_id):
        raw, filename, mime_type = adapters().gmail_attachment(message_id, attachment_id)
        mime_type = mime_type or 'application/octet-stream'
        disposition = 'inline' if mime_type.startswith('image/') or mime_type == 'application/pdf' else 'attachment'
        resp = Response(raw, mimetype=mime_type)
        safe_name = re.sub(r'[\r\n"]', '_', filename or 'attachment')
        resp.headers['Content-Disposition'] = f'{disposition}; filename="{safe_name}"'
        return resp

    @app.route('/api/apps/<app_id>/copilot', methods=['GET','POST','DELETE'])
    @authenticated
    def app_copilot(app_id):
        from datetime import datetime, timezone
        from app_copilot import answer
        spec = store.app(session['user'], app_id)
        if not spec: return jsonify(error='App not found.'),404
        if request.method == 'DELETE':
            store.clear_copilot(session['user'], app_id)
            return jsonify(ok=True)
        history = store.copilot_history(session['user'], app_id)
        if request.method == 'GET': return jsonify(turns=history)
        body = request.get_json(silent=True) or {}
        message = str(body.get('message') or '').strip()
        if not 3 <= len(message) <= 1500: raise ValueError('Ask a question between 3 and 1,500 characters.')
        # Bind every request to this app and the authenticated owner's connection.
        source = spec['source']
        records = adapters().fetch(source, folder='all')
        sample = SOURCES[source]['kind']=='sample'
        scope = 'Sample records' if sample else 'Up to 30 recent messages across mail folders' if source=='gmail' else 'Up to 100 recent records'
        result = answer(message, records, history, env.get('ANTHROPIC_API_KEY'), source=source, sample=sample, scope=scope)
        turn = {'question':message,'summary':result['summary'],'plan':result['plan'],
            'mode':result['mode'],'at':datetime.now(timezone.utc).isoformat()}
        store.save_copilot_turn(session['user'], app_id, turn)
        return jsonify(turn=turn,result=result)

    # -- Agent workflow automation --------------------------------------
    # User goal -> plan -> permission/policy checks -> human approval when
    # required -> connected-app tool call -> interface update -> audit log.
    # Every route below re-derives the app/plan from the store scoped to
    # session['user'] -- a client can never address another owner's plan
    # by guessing an id, the same guarantee every other /api/apps/<id>/*
    # route in this file already gives.

    def owned_app(app_id):
        return store.app(session['user'], app_id)

    def owned_plan(app_id, plan_id):
        plan = store.agent_plan(session['user'], plan_id)
        return plan if plan and plan['app'] == app_id else None

    @app.get('/api/apps/<app_id>/agent/plans')
    @authenticated
    def list_agent_plans(app_id):
        if not owned_app(app_id): return jsonify(error='App not found.'), 404
        return jsonify(plans=store.agent_plans(session['user'], app_id))

    @app.post('/api/apps/<app_id>/agent/plans')
    @authenticated
    def create_agent_plan(app_id):
        import agent_planner
        spec = owned_app(app_id)
        if not spec: return jsonify(error='App not found.'), 404
        builder = {'jira': agent_planner.build_plan, 'splunk': agent_planner.build_splunk_plan,
            'crm': agent_planner.build_crm_plan}.get(spec['source'])
        if not builder:
            raise ValueError('Automate mode currently supports Jira, Splunk, and CRM apps. Ask mode still works for every source.')
        if agent_rate_limited(session['user']):
            return jsonify(error='Too many automation requests. Please wait a moment and try again.'), 429
        body = request.get_json(silent=True) or {}
        goal = str(body.get('goal') or '').strip()[:2000]
        plan = builder(store, adapters(), store.user(session['user']), app_id, goal, env.get('ANTHROPIC_API_KEY'))
        return jsonify(plan=plan)

    @app.get('/api/apps/<app_id>/agent/plans/<plan_id>')
    @authenticated
    def get_agent_plan(app_id, plan_id):
        plan = owned_plan(app_id, plan_id)
        if not plan: return jsonify(error='Plan not found.'), 404
        return jsonify(plan=plan)

    @app.patch('/api/apps/<app_id>/agent/plans/<plan_id>/steps/<step_id>')
    @authenticated
    def edit_agent_step(app_id, plan_id, step_id):
        if not owned_plan(app_id, plan_id): return jsonify(error='Plan not found.'), 404
        step = store.agent_step(session['user'], plan_id, step_id)
        if not step: return jsonify(error='Step not found.'), 404
        if step['status'] != 'pending': raise ValueError('Only a pending step can be edited.')
        from tool_registry import get_tool
        body = request.get_json(silent=True) or {}
        candidate_input = body.get('input')
        if not isinstance(candidate_input, dict): raise ValueError('Invalid step input.')
        merged = {**step['input'], **candidate_input}
        clean = get_tool(step['tool'])['validate'](merged)  # re-validates before it's ever stored
        store.update_agent_step(session['user'], plan_id, step_id, {'input': clean})
        return jsonify(step=store.agent_step(session['user'], plan_id, step_id))

    @app.post('/api/apps/<app_id>/agent/plans/<plan_id>/steps/<step_id>/approve')
    @authenticated
    def approve_agent_step(app_id, plan_id, step_id):
        import workflow_executor
        if not owned_plan(app_id, plan_id): return jsonify(error='Plan not found.'), 404
        if agent_rate_limited(session['user']):
            return jsonify(error='Too many automation requests. Please wait a moment and try again.'), 429
        step = workflow_executor.approve_and_run(store, adapters(), store.user(session['user']), app_id, plan_id, step_id)
        return jsonify(step=step)

    @app.post('/api/apps/<app_id>/agent/plans/<plan_id>/steps/<step_id>/reject')
    @authenticated
    def reject_agent_step(app_id, plan_id, step_id):
        import approval_service
        if not owned_plan(app_id, plan_id): return jsonify(error='Plan not found.'), 404
        step = approval_service.decide(store, store.user(session['user']), app_id, plan_id, step_id, 'rejected')
        return jsonify(step=step)

    @app.post('/api/apps/<app_id>/agent/plans/<plan_id>/steps/<step_id>/retry')
    @authenticated
    def retry_agent_step(app_id, plan_id, step_id):
        import workflow_executor
        if not owned_plan(app_id, plan_id): return jsonify(error='Plan not found.'), 404
        step = store.agent_step(session['user'], plan_id, step_id)
        if not step: return jsonify(error='Step not found.'), 404
        if step['status'] != 'failed': raise ValueError('Only a failed step can be retried.')
        if agent_rate_limited(session['user']):
            return jsonify(error='Too many automation requests. Please wait a moment and try again.'), 429
        step = workflow_executor.execute_step(store, adapters(), store.user(session['user']), app_id, plan_id, step_id)
        return jsonify(step=step)

    @app.post('/api/apps/<app_id>/agent/plans/<plan_id>/pause')
    @authenticated
    def pause_agent_plan(app_id, plan_id):
        import workflow_executor
        if not owned_plan(app_id, plan_id): return jsonify(error='Plan not found.'), 404
        return jsonify(plan=workflow_executor.set_paused(store, store.user(session['user']), app_id, plan_id, True))

    @app.post('/api/apps/<app_id>/agent/plans/<plan_id>/resume')
    @authenticated
    def resume_agent_plan(app_id, plan_id):
        import workflow_executor
        if not owned_plan(app_id, plan_id): return jsonify(error='Plan not found.'), 404
        return jsonify(plan=workflow_executor.set_paused(store, store.user(session['user']), app_id, plan_id, False))

    @app.post('/api/apps/<app_id>/agent/plans/<plan_id>/cancel')
    @authenticated
    def cancel_agent_plan(app_id, plan_id):
        import workflow_executor
        if not owned_plan(app_id, plan_id): return jsonify(error='Plan not found.'), 404
        return jsonify(plan=workflow_executor.cancel_plan(store, store.user(session['user']), app_id, plan_id))

    @app.get('/api/apps/<app_id>/agent/audit')
    @authenticated
    def agent_audit(app_id):
        if not owned_app(app_id): return jsonify(error='App not found.'), 404
        return jsonify(entries=store.audit_trail(session['user'], app_id))

    @app.get('/', defaults={'path': ''})
    @app.get('/<path:path>')
    def frontend(path):
        if path.startswith('api/'): return jsonify(error='Endpoint not found.'), 404
        # Deliberately frontend_appstudio/ rather than frontend/ — pilant-agent's
        # frontend/ is already the existing Pilant Studio SPA (served by
        # studio.py on port 5008); this app is fully independent of it and
        # needs its own build output so the two never collide.
        dist = ROOT / 'frontend_appstudio' / 'dist'
        if path and (dist / path).is_file(): return send_from_directory(dist, path)
        if not (dist / 'index.html').exists():
            return 'Frontend is not built. Run npm install and npm run build inside frontend_appstudio/.', 503
        return send_from_directory(dist, 'index.html')

    return app


if __name__ == '__main__':
    create_app().run(host='127.0.0.1', port=int(os.environ.get('WORKSPACE_PORT', '5010')), debug=False)
