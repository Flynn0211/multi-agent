import asyncio
import json
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from uuid import uuid4

import aiosqlite
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from .bridge import OpenCodeBridgeClient
from .config import settings
from .graph import build_graph
from .store import Store


class Dispatch(BaseModel):
    prompt: str = Field(min_length=3, max_length=40000)
    directory: str = Field(min_length=1)
    mode: str = Field(default='human', pattern='^(auto|human)$')


class Approval(BaseModel):
    approved: bool


class Credentials(BaseModel):
    password: str = Field(max_length=4096)


class Runtime:
    def __init__(self, store, bridge, graph):
        self.store, self.bridge, self.graph = store, bridge, graph
        self.tasks = {}
        self.control_lock = asyncio.Lock()

    def config(self, run_id):
        return {'configurable': {'thread_id': run_id}, 'recursion_limit': 100}

    def launch(self, run_id, value):
        if any(not task.done() for task in self.tasks.values()):
            raise HTTPException(409, 'Another mission is running. Pause or finish it first.')
        self.tasks[run_id] = asyncio.create_task(self.work(run_id, value))

    async def work(self, run_id, value):
        await self.store.status(run_id, 'running')
        try:
            async for event in self.graph.astream_events(value, self.config(run_id), version='v2'):
                node = event.get('metadata', {}).get('langgraph_node')
                if node and event['name'] == node and event['event'] in ('on_chain_start', 'on_chain_end'):
                    await self.store.emit(run_id, 'node', {'node': node, 'phase': event['event'], 'data': event.get('data', {})})
            snapshot = await self.graph.aget_state(self.config(run_id))
            status = 'waiting_approval' if snapshot.interrupts else ('rejected' if snapshot.values.get('rejected') else 'completed')
            await self.store.emit(run_id, 'state', snapshot.values)
            await self.store.status(run_id, status)
        except asyncio.CancelledError:
            await self.store.status(run_id, 'interrupted')
            raise
        except Exception as exc:
            await self.store.status(run_id, 'failed', str(exc))


@asynccontextmanager
async def lifespan(app):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(settings.data_dir / 'missions.sqlite') as db:
        store = Store(db)
        await store.setup()
        async with AsyncSqliteSaver.from_conn_string(str(settings.data_dir / 'checkpoints.sqlite')) as saver:
            await saver.setup()
            bridge = OpenCodeBridgeClient(settings, store)
            app.state.runtime = Runtime(store, bridge, build_graph(bridge, saver))
            listener = asyncio.create_task(bridge.listen())
            try:
                yield
            finally:
                jobs = list(app.state.runtime.tasks.values()) + [listener]
                for task in jobs:
                    task.cancel()
                await asyncio.gather(*jobs, return_exceptions=True)
                await bridge.client.aclose()


app = FastAPI(title='Mission Control', lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=['http://localhost:3000', 'http://127.0.0.1:3000'],
                   allow_methods=['GET', 'POST'], allow_headers=['Content-Type', 'Last-Event-ID'])


@app.middleware('http')
async def local_origin(request: Request, call_next):
    # Local control plane: prevent another website from dispatching local commands.
    origin = request.headers.get('origin')
    if origin and origin not in ('http://localhost:3000', 'http://127.0.0.1:3000', 'http://127.0.0.1:8000', 'http://localhost:8000'):
        from fastapi.responses import JSONResponse
        return JSONResponse({'detail': 'Origin not allowed'}, status_code=403)
    return await call_next(request)


def runtime(request):
    return request.app.state.runtime


async def require_run(rt, run_id):
    run = await rt.store.get(run_id)
    if not run:
        raise HTTPException(404, 'Mission not found')
    return run


@app.get('/api/health')
async def health(request: Request):
    return {'backend': True, 'opencode': await runtime(request).bridge.health()}


@app.post('/api/credentials')
async def credentials(body: Credentials, request: Request):
    rt = runtime(request)
    if any(not t.done() for t in rt.tasks.values()):
        raise HTTPException(409, 'Finish the active mission before changing credentials')
    settings.opencode_server_password = body.password
    old = rt.bridge.client
    rt.bridge.client = rt.bridge.make_client()
    await old.aclose()
    return await rt.bridge.health()


@app.get('/api/agents')
async def agents(request: Request):
    try:
        return await runtime(request).bridge.request('GET', '/agent', timeout=10)
    except httpx.HTTPError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.get('/api/runs')
async def runs(request: Request):
    return await runtime(request).store.list()


@app.post('/api/runs', status_code=201)
async def dispatch(body: Dispatch, request: Request):
    rt = runtime(request)
    directory = Path(body.directory).expanduser().resolve()
    if not directory.is_dir():
        raise HTTPException(422, 'Project directory does not exist on the backend host')
    async with rt.control_lock:
        if any(not t.done() for t in rt.tasks.values()):
            raise HTTPException(409, 'Another mission is running')
        if not (await rt.bridge.health())['connected']:
            raise HTTPException(503, 'OpenCode is disconnected. Check URL and credentials.')
        run_id = str(uuid4())
        payload = {**body.model_dump(), 'directory': str(directory), 'run_id': run_id,
                   'outputs': {}, 'turns': 0, 'revisions': 0, 'tasks': []}
        await rt.store.create(run_id, payload)
        rt.launch(run_id, payload)
    return {'id': run_id}


@app.get('/api/runs/{run_id}')
async def run_state(run_id: str, request: Request):
    rt = runtime(request)
    run = await require_run(rt, run_id)
    snapshot = await rt.graph.aget_state(rt.config(run_id))
    sessions = await rt.store.sessions(run_id)
    pending = []
    for session in sessions:
        if session['status'] != 'done':
            with suppress(httpx.HTTPError):
                pending.extend(await rt.bridge.request('GET', f"/session/{session['session_id']}/permission", timeout=5))
    return {**run, 'state': snapshot.values, 'next': snapshot.next,
            'interrupts': [i.value for i in snapshot.interrupts], 'permissions': pending,
            'sessions': [{k: v for k, v in s.items() if k != 'result'} for s in sessions]}


@app.post('/api/runs/{run_id}/resume')
async def resume(run_id: str, body: Approval, request: Request):
    rt = runtime(request)
    run = await require_run(rt, run_id)
    if run['status'] not in ('waiting_approval', 'interrupted', 'failed'):
        raise HTTPException(409, 'Mission cannot be resumed in this state')
    snapshot = await rt.graph.aget_state(rt.config(run_id))
    if not snapshot.next:
        raise HTTPException(409, 'No checkpoint to resume')
    async with rt.control_lock:
        rt.launch(run_id, Command(resume=body.approved) if snapshot.interrupts else None)
    return {'id': run_id}


@app.post('/api/runs/{run_id}/cancel')
async def cancel(run_id: str, request: Request):
    rt = runtime(request)
    await require_run(rt, run_id)
    task = rt.tasks.get(run_id)
    if task and not task.done():
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    for s in await rt.store.sessions(run_id):
        if s['status'] != 'done':
            with suppress(httpx.HTTPError):
                await rt.bridge.request('POST', f"/session/{s['session_id']}/interrupt")
    await rt.store.status(run_id, 'cancelled')
    return {'status': 'cancelled'}


@app.post('/api/runs/{run_id}/permissions/{session_id}/{permission_id}')
async def permission(run_id: str, session_id: str, permission_id: str, body: Approval, request: Request):
    rt = runtime(request)
    owner = await rt.store.owner(session_id)
    if not owner or owner['run_id'] != run_id:
        raise HTTPException(404, 'Session not owned by this mission')
    try:
        await rt.bridge.request('POST', f'/session/{session_id}/permission/{permission_id}/reply',
                                json={'decision': 'once' if body.approved else 'reject'})
    except httpx.HTTPError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {'ok': True}


@app.get('/api/runs/{run_id}/checkpoints')
async def checkpoints(run_id: str, request: Request):
    rt = runtime(request)
    await require_run(rt, run_id)
    return [{'id': s.config['configurable']['checkpoint_id'], 'created': s.created_at,
             'next': s.next, 'state': s.values} async for s in rt.graph.aget_state_history(rt.config(run_id), limit=100)]


@app.get('/api/runs/{run_id}/events')
async def events(run_id: str, request: Request, after: int = 0):
    rt = runtime(request)
    await require_run(rt, run_id)
    try:
        cursor = max(after, int(request.headers.get('last-event-id', '0')))
    except ValueError:
        raise HTTPException(400, 'Invalid Last-Event-ID')

    async def stream():
        nonlocal cursor
        while not await request.is_disconnected():
            batch = await rt.store.events(run_id, cursor)
            for event in batch:
                cursor = event['id']
                yield {'id': str(cursor), 'event': 'mission', 'data': json.dumps(event)}
            if not batch:
                await asyncio.sleep(0.25)

    return EventSourceResponse(stream(), ping=15)
