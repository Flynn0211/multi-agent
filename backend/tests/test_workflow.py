"""Deterministic test doubles only; production always uses the HTTP bridge."""
import json
from types import SimpleNamespace

import aiosqlite
import pytest
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from backend.app.graph import build_graph
from backend.app.store import Store


class ScriptedExecutor:
    def __init__(self, reviews=(True,)):
        self.settings = SimpleNamespace(max_revisions=3)
        self.calls = []
        self.reviews = iter(reviews)

    async def execute(self, state, role, prompt, key):
        self.calls.append(role)
        if role == 'supervisor':
            return json.dumps({'next': 'FINISH', 'summary': 'Test routing',
                               'tasks': [{'title': 'Implement feature', 'role': 'coder', 'status': 'todo'}]})
        if role == 'reviewer':
            return json.dumps({'passed': next(self.reviews), 'summary': 'QA result', 'evidence': ['pytest: exit 0']})
        return f'{role} result'


def initial(mode='human'):
    return {'run_id': 'test', 'prompt': 'Build feature', 'directory': '.', 'mode': mode,
            'outputs': {}, 'turns': 0, 'revisions': 0}


@pytest.mark.asyncio
async def test_approval_survives_database_reopen(tmp_path):
    executor = ScriptedExecutor()
    config = {'configurable': {'thread_id': 'persistent'}}
    path = str(tmp_path / 'checkpoint.sqlite')
    async with AsyncSqliteSaver.from_conn_string(path) as saver:
        graph = build_graph(executor, saver)
        await graph.ainvoke(initial(), config)
        snapshot = await graph.aget_state(config)
        assert snapshot.interrupts
        assert 'coder' not in executor.calls
    async with AsyncSqliteSaver.from_conn_string(path) as saver:
        graph = build_graph(executor, saver)
        result = await graph.ainvoke(Command(resume=True), config)
        assert result['review_passed']
        assert result['next'] == 'FINISH'
        assert executor.calls.count('planner') == 1
        assert executor.calls.count('coder') == 1


@pytest.mark.asyncio
async def test_rejection_never_reaches_coder(tmp_path):
    executor = ScriptedExecutor()
    config = {'configurable': {'thread_id': 'reject'}}
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / 'checkpoint.sqlite')) as saver:
        graph = build_graph(executor, saver)
        await graph.ainvoke(initial(), config)
        result = await graph.ainvoke(Command(resume=False), config)
        assert result['rejected']
        assert 'coder' not in executor.calls


@pytest.mark.asyncio
async def test_failed_review_routes_back_to_coder(tmp_path):
    executor = ScriptedExecutor(reviews=(False, True))
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / 'checkpoint.sqlite')) as saver:
        graph = build_graph(executor, saver)
        result = await graph.ainvoke(initial('auto'), {'configurable': {'thread_id': 'retry'}})
        assert result['review_passed']
        assert result['revisions'] == 2
        assert executor.calls.count('coder') == 2
        assert executor.calls.count('reviewer') == 2


@pytest.mark.asyncio
async def test_revision_limit_is_not_reported_as_success(tmp_path):
    executor = ScriptedExecutor(reviews=(False, False, False))
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / 'checkpoint.sqlite')) as saver:
        graph = build_graph(executor, saver)
        with pytest.raises(RuntimeError, match='maximum revisions'):
            await graph.ainvoke(initial('auto'), {'configurable': {'thread_id': 'limit'}})
        assert executor.calls.count('coder') == 3


@pytest.mark.asyncio
async def test_event_replay_and_recovery(tmp_path):
    path = tmp_path / 'missions.sqlite'
    async with aiosqlite.connect(path) as db:
        store = Store(db)
        await store.setup()
        await store.create('one', {'prompt': 'persist'})
        await store.emit('one', 'test', {'value': 1})
        first = await store.events('one', 0)
        await store.emit('one', 'test', {'value': 2})
        remaining = await store.events('one', first[0]['id'])
        assert len(remaining) == 1 and remaining[0]['payload']['value'] == 2
        assert await store.events('other', 0) == []
    async with aiosqlite.connect(path) as db:
        store = Store(db)
        await store.setup()
        assert (await store.get('one'))['status'] == 'interrupted'
        assert len(await store.events('one', 0)) == 2
