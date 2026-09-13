"""Durable append-only events, content-addressed models, and historical versions."""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import json
import sqlite3
import uuid
from . import models


class Journal:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS models (hash TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS versions (kind TEXT, entity_id TEXT, seq INTEGER, model_hash TEXT,
                    PRIMARY KEY(kind,entity_id,seq));
                CREATE INDEX IF NOT EXISTS version_seq ON versions(seq);
                CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, fingerprint TEXT, status TEXT, result TEXT);
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS skills (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS proposals (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS deliveries (skill TEXT, event_id TEXT, status TEXT, PRIMARY KEY(skill,event_id));
                CREATE TABLE IF NOT EXISTS event_types (name TEXT PRIMARY KEY, schema TEXT NOT NULL);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db: yield db
        finally: db.close()

    def append(self, name, payload=None, *, identifier=None, phase='observed', entities=None,
               changes=None, correlation=None, cause=None, depth=0, skill=None, timestamp=None, _records=None):
        identifier = identifier or str(uuid.uuid4())
        changes = changes or []
        data = {'id': identifier, 'type': name, 'phase': phase, 'source': name.rsplit('.', 1)[0],
                'entities': sorted(set((entities or []) + [f"{c['kind']}:{c['id']}" for c in changes])),
                'timestamp': timestamp or datetime.now(timezone.utc).isoformat(), 'published_at':datetime.now(timezone.utc).isoformat(), 'correlation': correlation,
                'cause': cause, 'depth': depth, 'skill': skill, 'outcome': payload, 'changes': []}
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT data,seq FROM events WHERE id=?', (identifier,)).fetchone()
            if old: return models.loads(old['data']) | {'seq': old['seq']}
            for record in _records or []:
                table=record['table']
                if table not in ('skills','proposals','settings'): raise ValueError('Unknown controller control table')
                db.execute(f'INSERT OR REPLACE INTO {table} VALUES (?,?)',(record['id'],models.dumps(record['state'])))
            payload_model = models.encode(payload)
            db.execute('INSERT OR IGNORE INTO event_types VALUES (?,?)', (name, models.dumps({'name':name,'payload_schema':{},'source':data['source']})))
            db.execute('INSERT OR IGNORE INTO models VALUES (?,?)', (payload_model['sha256'], json.dumps(payload_model)))
            data['payload_ref'] = payload_model['sha256']
            for change in changes:
                model = models.encode(change['state'])
                db.execute('INSERT OR IGNORE INTO models VALUES (?,?)', (model['sha256'], json.dumps(model)))
                data['changes'].append({'kind': change['kind'], 'id': change['id'], 'model': model['sha256']})
            cursor = db.execute('INSERT INTO events(id,data) VALUES (?,?)', (identifier, models.dumps(data)))
            seq = cursor.lastrowid
            for change in data['changes']:
                db.execute('INSERT INTO versions VALUES (?,?,?,?)', (change['kind'], change['id'], seq, change['model']))
        return data | {'seq': seq}

    def head(self):
        with self.connect() as db: return db.execute('SELECT coalesce(max(seq),0) FROM events').fetchone()[0]

    def model(self, digest):
        with self.connect() as db:
            row = db.execute('SELECT data FROM models WHERE hash=?', (digest,)).fetchone()
            if not row: raise ValueError('Unknown state model')
            return json.loads(row[0])

    def state(self, sequence):
        with self.connect() as db:
            rows = db.execute('''SELECT v.*,m.data FROM versions v JOIN
                (SELECT kind,entity_id,max(seq) AS seq FROM versions WHERE seq<=? GROUP BY kind,entity_id) last
                USING(kind,entity_id,seq) JOIN models m ON m.hash=v.model_hash ORDER BY kind,entity_id''', (sequence,)).fetchall()
        return [{'kind': r['kind'], 'id': r['entity_id'], 'sequence': r['seq'], 'model': r['model_hash'],
                 'state': models.decode(json.loads(r['data']))} for r in rows]

    def events(self, after=0, until=None):
        with self.connect() as db:
            cursor = db.execute('SELECT seq,data FROM events WHERE seq>? AND seq<=? ORDER BY seq', (after, until if until is not None else self.head()))
            for row in cursor: yield models.loads(row['data']) | {'seq': row['seq']}

    def setting(self, key, default=None):
        with self.connect() as db:
            row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
            return models.loads(row[0]) if row else default

    def set_setting(self, key, value):
        with self.connect() as db: db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, models.dumps(value)))
