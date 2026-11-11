import json

import aiosqlite
import httpx
import pytest

from backend.app.bridge import OpenCodeBridgeClient
from backend.app.config import Settings
from backend.app.store import Store


@pytest.mark.asyncio
async def test_receipt_prevents_reexecuting_completed_mutations(tmp_path):
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path == '/api/session':
            payload = json.loads(request.content)
            assert payload['location']['directory'] == str(tmp_path)
            assert payload['model'] == {'providerID': 'test-provider', 'id': 'test-model'}
            return httpx.Response(200, json={'data': {'id': 'ses_test'}})
        if request.url.path.endswith('/message'):
            return httpx.Response(200, json={'data': [{'type': 'assistant', 'content': [{'type': 'text', 'text': 'Done'}]}], 'cursor': {}})
        if request.url.path == '/api/session/ses_test':
            return httpx.Response(200, json={'data': {'outcome': 'succeeded'}})
        return httpx.Response(204)

    async with aiosqlite.connect(tmp_path / 'db.sqlite') as db:
        store = Store(db)
        await store.setup()
        bridge = OpenCodeBridgeClient(Settings(opencode_model='test-provider/test-model'), store)
        await bridge.client.aclose()
        bridge.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url='http://test')
        state = {'run_id': 'r1', 'directory': str(tmp_path), 'mode': 'human'}
        assert await bridge.execute(state, 'coder', 'implement', 'once') == 'Done'
        assert await bridge.execute(state, 'coder', 'implement', 'once') == 'Done'
        assert calls.count(('POST', '/api/session/ses_test/prompt')) == 1
        await bridge.client.aclose()


@pytest.mark.asyncio
async def test_ambiguous_submission_is_not_silently_retried(tmp_path):
    async with aiosqlite.connect(tmp_path / 'db.sqlite') as db:
        store = Store(db)
        await store.setup()
        await store.receipt('uncertain', 'r1', 'coder', 'ses_existing', 'submitting')
        bridge = OpenCodeBridgeClient(Settings(), store)
        try:
            with pytest.raises(RuntimeError, match='not resent'):
                await bridge.execute({'run_id': 'r1'}, 'coder', 'implement', 'uncertain')
        finally:
            await bridge.client.aclose()


@pytest.mark.asyncio
async def test_provider_failure_is_actionable_without_resubmission(tmp_path):
    def handler(request):
        if request.url.path.endswith('/message'):
            return httpx.Response(200, json={'data': [{'error': {'status': 402, 'message': 'Insufficient balance'}}], 'cursor': {}})
        if request.method == 'GET':
            return httpx.Response(200, json={'data': {'outcome': 'failed'}})
        assert request.url.path.endswith('/wait')
        return httpx.Response(204)

    async with aiosqlite.connect(tmp_path / 'db.sqlite') as db:
        store = Store(db)
        await store.setup()
        await store.receipt('failed', 'r1', 'supervisor', 'ses_failed', 'running')
        bridge = OpenCodeBridgeClient(Settings(opencode_auto_fallback=False), store)
        await bridge.client.aclose()
        bridge.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url='http://test')
        try:
            with pytest.raises(RuntimeError, match='HTTP 402: Insufficient balance'):
                await bridge.execute({'run_id': 'r1'}, 'supervisor', 'prompt', 'failed')
        finally:
            await bridge.client.aclose()
