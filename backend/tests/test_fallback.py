import json

import aiosqlite
import httpx
import pytest

from backend.app.bridge import OpenCodeBridgeClient
from backend.app.config import Settings
from backend.app.fallback import candidates, model_ref, quota_error
from backend.app.store import Store


def model(provider, name, free=False):
    return {'providerID': provider, 'id': name, 'enabled': True, 'type': 'chat',
            'capabilities': {'tools': True}, 'cost': [{'input': 0 if free else 1, 'output': 0 if free else 2}]}


def test_no_paid_models_from_exhausted_wallet():
    catalog = [model('empty', 'paid'), model('empty', 'free', True), model('other', 'usable')]
    result = list(candidates(catalog, '', [], {'providerID': 'empty', 'id': 'original'}, {'status': 402}))
    assert result == [{'providerID': 'other', 'id': 'usable'}, {'providerID': 'empty', 'id': 'free'}]


def test_allowlist_order_variants_and_no_duplicates():
    catalog = [model('a', 'x'), model('b', 'y')]
    catalog[1]['variants'] = [{'id': 'low'}]
    result = list(candidates(catalog, 'b/y#low,a/x,b/y#low,absent/z', ['a/x'], {}, {'status': 429}))
    assert result == [{'providerID': 'b', 'id': 'y', 'variant': 'low'}]
    assert model_ref('proxy/vendor/name#low')['id'] == 'vendor/name'
    assert not quota_error({'status': 400, 'message': 'context too long'})
    assert not quota_error({'status': 500})


@pytest.mark.asyncio
@pytest.mark.parametrize('succeeds', [True, False])
async def test_same_session_failover_bounded_and_persistent(tmp_path, succeeds):
    switched = False
    prompts = []
    def handler(request):
        nonlocal switched
        path = request.url.path
        if path == '/api/model':
            return httpx.Response(200, json={'data': [model('original', 'one'), model('backup', 'two')]})
        if path.endswith('/model'):
            switched = True
            assert json.loads(request.content)['model'] == {'providerID': 'backup', 'id': 'two'}
            return httpx.Response(204)
        if path.endswith('/prompt'):
            prompts.append(json.loads(request.content))
            assert request.url.path == '/api/session/ses_existing/prompt'
            return httpx.Response(204)
        ref = {'providerID': 'backup', 'id': 'two'} if switched else {'providerID': 'original', 'id': 'one'}
        if path.endswith('/message'):
            message = {'model': ref, 'content': [{'type': 'text', 'text': 'Done'}]} if switched and succeeds else {
                'model': ref, 'error': {'status': 402, 'message': 'Insufficient balance'}}
            return httpx.Response(200, json={'data': [message], 'cursor': {}})
        if request.method == 'GET':
            return httpx.Response(200, json={'data': {'model': ref, 'outcome': 'succeeded' if switched and succeeds else 'failed'}})
        assert path.endswith('/wait')
        return httpx.Response(204)

    async with aiosqlite.connect(tmp_path / 'db.sqlite') as db:
        store = Store(db)
        await store.setup()
        await store.receipt('key', 'run', 'coder', 'ses_existing', 'running')
        bridge = OpenCodeBridgeClient(Settings(opencode_auto_fallback=True, opencode_fallback_models='', opencode_max_fallbacks=1), store)
        await bridge.client.aclose()
        bridge.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url='http://test')
        state = {'run_id': 'run', 'directory': str(tmp_path)}
        try:
            if succeeds:
                assert await bridge.execute(state, 'coder', 'original task', 'key') == 'Done'
                assert await bridge.execute(state, 'coder', 'original task', 'key') == 'Done'
            else:
                with pytest.raises(RuntimeError, match='No usable fallback remains'):
                    await bridge.execute(state, 'coder', 'original task', 'key')
                # Resume after failure/restart must not reset the retry budget.
                with pytest.raises(RuntimeError, match='No usable fallback remains'):
                    await bridge.execute(state, 'coder', 'original task', 'key')
            assert len(prompts) == 1
            assert prompts[0]['resume'] is True
            assert len(await store.attempted_models('key')) == 2
            events = await store.events('run', 0)
            assert events[0]['kind'] == 'model_fallback'
        finally:
            await bridge.client.aclose()
