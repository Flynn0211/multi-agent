import asyncio
import json
import time

import aiosqlite


class Store:
    """Persistent run registry, execution receipts and replayable SSE journal."""

    def __init__(self, db: aiosqlite.Connection):
        self.db = db
        self.lock = asyncio.Lock()

    async def setup(self):
        self.db.row_factory = aiosqlite.Row
        await self.db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, payload TEXT NOT NULL,
                status TEXT NOT NULL, error TEXT, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                kind TEXT NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS events_run ON events(run_id, id);
            CREATE TABLE IF NOT EXISTS executions (
                key TEXT PRIMARY KEY, run_id TEXT NOT NULL, role TEXT NOT NULL,
                session_id TEXT NOT NULL, status TEXT NOT NULL, result TEXT);
            CREATE TABLE IF NOT EXISTS model_attempts (
                execution_key TEXT NOT NULL, model TEXT NOT NULL,
                PRIMARY KEY(execution_key, model));
        ''')
        await self.db.execute("UPDATE runs SET status='interrupted' WHERE status='running'")
        await self.db.commit()

    async def create(self, run_id, payload):
        async with self.lock:
            await self.db.execute('INSERT INTO runs VALUES (?, ?, ?, NULL, ?)',
                                  (run_id, json.dumps(payload), 'running', time.time()))
            await self.db.commit()

    async def status(self, run_id, status, error=None):
        async with self.lock:
            await self.db.execute('UPDATE runs SET status=?, error=? WHERE id=?', (status, error, run_id))
            await self.db.commit()
        await self.emit(run_id, 'status', {'status': status, 'error': error})

    async def get(self, run_id):
        async with self.db.execute('SELECT * FROM runs WHERE id=?', (run_id,)) as cur:
            row = await cur.fetchone()
        return {**dict(row), 'payload': json.loads(row['payload'])} if row else None

    async def list(self):
        async with self.db.execute('SELECT id, status, created, error FROM runs ORDER BY created DESC LIMIT 100') as cur:
            return [dict(row) for row in await cur.fetchall()]

    async def emit(self, run_id, kind, payload):
        async with self.lock:
            await self.db.execute('INSERT INTO events(run_id,kind,payload,created) VALUES(?,?,?,?)',
                                  (run_id, kind, json.dumps(payload, default=str), time.time()))
            await self.db.commit()

    async def events(self, run_id, after):
        async with self.db.execute('SELECT * FROM events WHERE run_id=? AND id>? ORDER BY id LIMIT 250',
                                   (run_id, after)) as cur:
            return [{**dict(r), 'payload': json.loads(r['payload'])} for r in await cur.fetchall()]

    async def execution(self, key):
        async with self.db.execute('SELECT * FROM executions WHERE key=?', (key,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def attempted_models(self, key):
        async with self.db.execute('SELECT model FROM model_attempts WHERE execution_key=?', (key,)) as cur:
            return [r['model'] for r in await cur.fetchall()]

    async def attempt_model(self, key, model):
        async with self.lock:
            await self.db.execute('INSERT OR IGNORE INTO model_attempts VALUES(?,?)', (key, model))
            await self.db.commit()

    async def receipt(self, key, run_id, role, session, status, result=None):
        async with self.lock:
            await self.db.execute('INSERT OR REPLACE INTO executions VALUES(?,?,?,?,?,?)',
                                  (key, run_id, role, session, status, result))
            await self.db.commit()

    async def sessions(self, run_id):
        async with self.db.execute('SELECT * FROM executions WHERE run_id=?', (run_id,)) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def owner(self, session):
        async with self.db.execute('SELECT run_id,role FROM executions WHERE session_id=?', (session,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None
