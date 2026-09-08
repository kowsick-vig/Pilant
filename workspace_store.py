"""User-owned workspace state. Tokens are encrypted at rest; never returned to clients."""
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from cryptography.fernet import Fernet


class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        key_path = self.directory / 'encryption.key'
        if not key_path.exists():
            try:
                fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'wb') as f:
                    f.write(Fernet.generate_key())
            except FileExistsError:
                pass
        self.cipher = Fernet(key_path.read_bytes())
        self.path = self.directory / 'workspace.sqlite3'
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, name TEXT NOT NULL,
                    password TEXT NOT NULL, role TEXT NOT NULL, preferences TEXT NOT NULL DEFAULT '{}');
                CREATE TABLE IF NOT EXISTS connections (owner TEXT, source TEXT, secret BLOB NOT NULL,
                    PRIMARY KEY(owner, source));
                CREATE TABLE IF NOT EXISTS views (id TEXT PRIMARY KEY, owner TEXT, spec TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS apps (id TEXT PRIMARY KEY, owner TEXT, spec TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS copilot_turns (id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT, app TEXT, turn TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS changes (owner TEXT, source TEXT, record TEXT, value TEXT,
                    PRIMARY KEY(owner, source, record));
                CREATE TABLE IF NOT EXISTS agent_plans (id TEXT PRIMARY KEY, owner TEXT, app TEXT,
                    goal TEXT NOT NULL, status TEXT NOT NULL, mode TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS agent_steps (id TEXT PRIMARY KEY, plan_id TEXT, owner TEXT,
                    position INTEGER NOT NULL, tool TEXT NOT NULL, label TEXT NOT NULL, risk TEXT NOT NULL,
                    approval_required INTEGER NOT NULL, status TEXT NOT NULL, input TEXT NOT NULL,
                    result TEXT, error TEXT, idempotency_key TEXT);
                CREATE TABLE IF NOT EXISTS agent_audit (id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT,
                    app TEXT, plan_id TEXT, step_id TEXT, actor TEXT NOT NULL, action TEXT NOT NULL,
                    tool TEXT, target TEXT, decision TEXT, connected_system TEXT, result TEXT, at TEXT NOT NULL);
            ''')
        os.chmod(self.path, 0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def user(self, username):
        with self.connect() as db:
            row = db.execute('SELECT * FROM users WHERE id=?', (username,)).fetchone()
        return dict(row) if row else None

    def add_user(self, username, name, password, role):
        with self.connect() as db:
            db.execute('INSERT INTO users(id,name,password,role) VALUES(?,?,?,?)',
                       (username, name, password, role))

    def preferences(self, owner, value):
        with self.connect() as db:
            db.execute('UPDATE users SET preferences=? WHERE id=?', (json.dumps(value), owner))

    def connection(self, owner, source):
        with self.connect() as db:
            row = db.execute('SELECT secret FROM connections WHERE owner=? AND source=?', (owner, source)).fetchone()
        return json.loads(self.cipher.decrypt(row['secret'])) if row else None

    def save_connection(self, owner, source, value):
        encrypted = self.cipher.encrypt(json.dumps(value).encode())
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO connections VALUES(?,?,?)', (owner, source, encrypted))

    def disconnect(self, owner, source):
        with self.connect() as db:
            db.execute('DELETE FROM connections WHERE owner=? AND source=?', (owner, source))

    def views(self, owner):
        with self.connect() as db:
            rows = db.execute('SELECT id,spec FROM views WHERE owner=? ORDER BY rowid DESC', (owner,)).fetchall()
        return [dict(id=r['id'], **json.loads(r['spec'])) for r in rows]

    def save_view(self, owner, spec):
        view_id = uuid.uuid4().hex
        with self.connect() as db:
            db.execute('INSERT INTO views VALUES(?,?,?)', (view_id, owner, json.dumps(spec)))
        return view_id

    def delete_view(self, owner, view_id):
        with self.connect() as db:
            return db.execute('DELETE FROM views WHERE owner=? AND id=?', (owner, view_id)).rowcount > 0

    def changes(self, owner, source):
        with self.connect() as db:
            rows = db.execute('SELECT record,value FROM changes WHERE owner=? AND source=?', (owner, source)).fetchall()
        return {r['record']: json.loads(r['value']) for r in rows}

    def change(self, owner, source, record, value):
        # Merge (not replace) so independent overrides on the same record --
        # e.g. a status change, an agent-added comment, and a reassignment --
        # can coexist instead of the latest write silently discarding the
        # others. Existing single-field callers (status) are unaffected:
        # merging a dict with one key into itself just overwrites that key.
        with self.connect() as db:
            existing = db.execute('SELECT value FROM changes WHERE owner=? AND source=? AND record=?',
                (owner, source, record)).fetchone()
            merged = {**(json.loads(existing['value']) if existing else {}), **value}
            db.execute('INSERT OR REPLACE INTO changes VALUES(?,?,?,?)', (owner, source, record, json.dumps(merged)))

    def apps(self, owner):
        with self.connect() as db:
            rows = db.execute('SELECT id,spec FROM apps WHERE owner=? ORDER BY rowid DESC', (owner,)).fetchall()
        return [dict(id=r['id'], **json.loads(r['spec'])) for r in rows]

    def app(self, owner, app_id):
        with self.connect() as db:
            row = db.execute('SELECT id,spec FROM apps WHERE owner=? AND id=?', (owner,app_id)).fetchone()
        return dict(id=row['id'], **json.loads(row['spec'])) if row else None

    def save_app(self, owner, spec, app_id=None):
        if app_id:
            with self.connect() as db:
                if not db.execute('UPDATE apps SET spec=? WHERE owner=? AND id=?', (json.dumps(spec),owner,app_id)).rowcount:
                    raise ValueError('App not found.')
        else:
            app_id = uuid.uuid4().hex
            with self.connect() as db:
                db.execute('INSERT INTO apps VALUES(?,?,?)', (app_id,owner,json.dumps(spec)))
        return dict(id=app_id, **spec)

    def delete_app(self, owner, app_id):
        with self.connect() as db:
            return db.execute('DELETE FROM apps WHERE owner=? AND id=?', (owner,app_id)).rowcount > 0

    def copilot_history(self, owner, app_id):
        with self.connect() as db:
            rows = db.execute('SELECT turn FROM copilot_turns WHERE owner=? AND app=? ORDER BY id DESC LIMIT 12', (owner, app_id)).fetchall()
        return [json.loads(row['turn']) for row in reversed(rows)]

    def save_copilot_turn(self, owner, app_id, turn):
        with self.connect() as db:
            db.execute('INSERT INTO copilot_turns(owner,app,turn) VALUES(?,?,?)', (owner,app_id,json.dumps(turn)))
            db.execute('DELETE FROM copilot_turns WHERE owner=? AND app=? AND id NOT IN (SELECT id FROM copilot_turns WHERE owner=? AND app=? ORDER BY id DESC LIMIT 12)', (owner,app_id,owner,app_id))

    def clear_copilot(self, owner, app_id):
        with self.connect() as db:
            db.execute('DELETE FROM copilot_turns WHERE owner=? AND app=?', (owner,app_id))

    # -- Agent workflow automation: plans, their steps, and an immutable audit trail. --

    def _step_row(self, row):
        return {'id': row['id'], 'tool': row['tool'], 'label': row['label'], 'risk': row['risk'],
            'approval_required': bool(row['approval_required']), 'status': row['status'],
            'input': json.loads(row['input']), 'result': json.loads(row['result']) if row['result'] else None,
            'error': row['error']}

    def save_agent_plan(self, owner, app_id, goal, mode, status, steps):
        plan_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute('INSERT INTO agent_plans VALUES(?,?,?,?,?,?,?,?)',
                (plan_id, owner, app_id, goal, status, mode, now, now))
            for i, s in enumerate(steps):
                db.execute('INSERT INTO agent_steps VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (uuid.uuid4().hex, plan_id, owner, i, s['tool'], s['label'], s['risk'],
                     1 if s['approval_required'] else 0, s['status'], json.dumps(s['input']),
                     json.dumps(s['result']) if s.get('result') is not None else None, s.get('error'), None))
        return self.agent_plan(owner, plan_id)

    def agent_plan(self, owner, plan_id):
        with self.connect() as db:
            prow = db.execute('SELECT * FROM agent_plans WHERE owner=? AND id=?', (owner, plan_id)).fetchone()
            if not prow: return None
            srows = db.execute('SELECT * FROM agent_steps WHERE owner=? AND plan_id=? ORDER BY position',
                (owner, plan_id)).fetchall()
        return {'id': prow['id'], 'app': prow['app'], 'goal': prow['goal'], 'status': prow['status'],
            'mode': prow['mode'], 'created_at': prow['created_at'], 'updated_at': prow['updated_at'],
            'steps': [self._step_row(r) for r in srows]}

    def agent_plans(self, owner, app_id):
        with self.connect() as db:
            prows = db.execute('SELECT id FROM agent_plans WHERE owner=? AND app=? ORDER BY created_at DESC',
                (owner, app_id)).fetchall()
        return [self.agent_plan(owner, r['id']) for r in prows]

    def agent_step(self, owner, plan_id, step_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM agent_steps WHERE owner=? AND plan_id=? AND id=?',
                (owner, plan_id, step_id)).fetchone()
        return self._step_row(row) if row else None

    def update_agent_step(self, owner, plan_id, step_id, fields):
        allowed = {'status', 'input', 'result', 'error', 'idempotency_key'}
        sets, values = [], []
        for k, v in fields.items():
            if k not in allowed: continue
            sets.append(f'{k}=?')
            values.append(json.dumps(v) if k in ('input', 'result') and v is not None else v)
        if not sets: return
        values += [owner, plan_id, step_id]
        with self.connect() as db:
            db.execute(f'UPDATE agent_steps SET {",".join(sets)} WHERE owner=? AND plan_id=? AND id=?', values)

    def update_agent_plan_status(self, owner, plan_id, status):
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute('UPDATE agent_plans SET status=?, updated_at=? WHERE owner=? AND id=?',
                (status, now, owner, plan_id))

    def save_audit_entry(self, owner, entry):
        with self.connect() as db:
            db.execute('INSERT INTO agent_audit(owner,app,plan_id,step_id,actor,action,tool,target,'
                'decision,connected_system,result,at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                (owner, entry.get('app'), entry.get('plan_id'), entry.get('step_id'), entry['actor'],
                 entry['action'], entry.get('tool'), entry.get('target'), entry.get('decision'),
                 entry.get('connected_system'), entry.get('result'), entry['at']))

    def audit_trail(self, owner, app_id, limit=200):
        with self.connect() as db:
            rows = db.execute('SELECT * FROM agent_audit WHERE owner=? AND app=? ORDER BY id DESC LIMIT ?',
                (owner, app_id, limit)).fetchall()
        return [dict(r) for r in rows]
