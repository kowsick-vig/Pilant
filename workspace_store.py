"""User-owned workspace state. Tokens are encrypted at rest; never returned to clients."""
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
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
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO changes VALUES(?,?,?,?)', (owner, source, record, json.dumps(value)))

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
