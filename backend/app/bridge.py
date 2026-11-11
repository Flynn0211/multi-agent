import asyncio
import json
from contextlib import suppress

import httpx

from .config import Settings
from .store import Store
from .fallback import candidates, model_id, model_ref, quota_error


def permissions(role: str, human: bool):
    rules = [{'action': '*', 'resource': '*', 'effect': 'ask' if human else 'allow'}]
    if role in ('supervisor', 'planner', 'researcher'):
        rules = [{'action': '*', 'resource': '*', 'effect': 'deny'}]
    for action in ('read', 'glob', 'grep', 'webfetch', 'websearch'):
        rules.append({'action': action, 'resource': '*', 'effect': 'allow'})
    if not human and role in ('coder', 'reviewer'):
        # Unknown shell commands and all mutations are approved at the actual tool boundary.
        for action in ('shell', 'edit', 'external_directory'):
            rules.append({'action': action, 'resource': '*', 'effect': 'ask'})
        for resource in ('npm run *', 'npm test *', 'npx tsc *', 'python -m pytest *', 'git status *', 'git diff *'):
            rules.append({'action': 'shell', 'resource': resource, 'effect': 'allow'})
    rules.extend([
        {'action': 'question', 'resource': '*', 'effect': 'deny'},
        {'action': 'subagent', 'resource': '*', 'effect': 'deny'},
        {'action': 'read', 'resource': '*.env*', 'effect': 'ask'},
        {'action': 'read', 'resource': '*.env.example', 'effect': 'allow'},
    ])
    return rules


class OpenCodeBridgeClient:
    def __init__(self, settings: Settings, store: Store):
        self.settings, self.store = settings, store
        self.connected = False
        self.stream_error = None
        self.client = self.make_client()
        self.preferred = {}

    def make_client(self):
        s = self.settings
        headers = {'Authorization': f'Bearer {s.opencode_server_token}'} if s.opencode_server_token else {}
        auth = httpx.BasicAuth(s.opencode_server_username, s.opencode_server_password) if s.opencode_server_password and not headers else None
        return httpx.AsyncClient(base_url=s.opencode_server_url.rstrip('/'), headers=headers, auth=auth,
                                 timeout=httpx.Timeout(30, read=s.execution_timeout))

    async def request(self, method, path, **kwargs):
        response = await self.client.request(method, '/api' + path, **kwargs)
        response.raise_for_status()
        if not response.content:
            return None
        payload = response.json()
        # V2 resource responses wrap data; paginated message lists retain their cursor.
        if isinstance(payload, dict) and 'data' in payload and 'cursor' not in payload:
            return payload['data']
        return payload

    async def health(self):
        try:
            info = await self.request('GET', '/info', timeout=5)
            return {'connected': True, 'url': self.settings.opencode_server_url,
                    'version': info.get('version'), 'stream': self.connected, 'stream_error': self.stream_error}
        except (httpx.HTTPError, ValueError) as exc:
            return {'connected': False, 'url': self.settings.opencode_server_url, 'error': str(exc), 'stream': self.connected}

    async def listen(self):
        while True:
            try:
                async with self.client.stream('GET', '/api/event', timeout=httpx.Timeout(10, read=None)) as response:
                    response.raise_for_status()
                    self.connected, self.stream_error = True, None
                    data = []
                    async for line in response.aiter_lines():
                        if line.startswith('data:'):
                            data.append(line[5:].lstrip())
                        elif not line and data:
                            event = json.loads('\n'.join(data))
                            data = []
                            body = event.get('data', {})
                            owner = await self.store.owner(body.get('sessionID', ''))
                            if owner:
                                # Public assistant output and tool telemetry; never expose private reasoning.
                                if event.get('type', '').startswith(('session.text.', 'session.tool.', 'session.shell.',
                                                                      'session.execution.', 'session.status', 'session.idle',
                                                                      'permission.')):
                                    await self.store.emit(owner['run_id'], 'opencode', {**owner, 'event': event})
                self.connected = False
            except (httpx.HTTPError, ValueError) as exc:
                self.connected, self.stream_error = False, str(exc)
            await asyncio.sleep(2)

    async def execute(self, state, role, prompt, key):
        run_id = state['run_id']
        old = await self.store.execution(key)
        if old and old['status'] == 'done':
            return old['result']
        if old and old['status'] == 'submitting':
            raise RuntimeError('Submission outcome is uncertain. Inspect session ' + old['session_id'] + ' before starting a new mission; prompt was not resent.')
        session = old['session_id'] if old else None
        if not session:
            body = {'title': f'Mission {run_id[:8]} · {role}', 'agent': self.settings.opencode_agent,
                    'location': {'directory': state['directory']},
                    'permissions': permissions(role, state['mode'] == 'human')}
            if self.settings.opencode_model:
                body['model'] = model_ref(self.settings.opencode_model)
            if state['run_id'] in self.preferred:
                body['model'] = self.preferred[state['run_id']]
            created = await self.request('POST', '/session', json=body)
            session = created['id']
            await self.store.receipt(key, run_id, role, session, 'submitting')
            await self.store.emit(run_id, 'session', {'role': role, 'session_id': session})
            await self.request('POST', f'/session/{session}/prompt', json={'text': prompt})
            await self.store.receipt(key, run_id, role, session, 'running')
        try:
            while True:
                await self.request('POST', f'/experimental/session/{session}/wait')
                info = await self.request('GET', f'/session/{session}')
                messages = await self.request('GET', f'/session/{session}/message', params={'order': 'desc', 'type': 'assistant', 'limit': '100'})
                if info.get('outcome') == 'succeeded':
                    if info.get('model'):
                        self.preferred[run_id] = info['model']
                    break
                # Only the newest assistant turn determines whether failover is appropriate.
                failure = messages['data'][0].get('error', {}) if messages['data'] else {}
                failed_model = info.get('model') or (messages['data'][0].get('model', {}) if messages['data'] else {})
                replacement = None
                if self.settings.opencode_auto_fallback and quota_error(failure):
                    if model_id(failed_model):
                        await self.store.attempt_model(key, model_id(failed_model))
                    attempted = await self.store.attempted_models(key)
                    if len(attempted) <= max(0, self.settings.opencode_max_fallbacks) and self.settings.opencode_max_fallbacks > 0:
                        catalog = await self.request('GET', '/model', params={'location[directory]': info.get('location', {}).get('directory', state.get('directory', ''))})
                        replacement = next(candidates(catalog, self.settings.opencode_fallback_models,
                                                      attempted, failed_model, failure), None)
                if replacement:
                    await self.store.attempt_model(key, model_id(replacement))
                    await self.request('POST', f'/session/{session}/model', json={'model': replacement})
                    await self.store.emit(run_id, 'model_fallback', {'role': role, 'session_id': session,
                        'from': model_id(failed_model), 'to': model_id(replacement),
                        'reason': failure.get('message', 'Provider quota exceeded')})
                    # Keep the session/tool history; never replay the original task in a new session.
                    await self.store.receipt(key, run_id, role, session, 'submitting')
                    await self.request('POST', f'/session/{session}/prompt', json={
                        'text': 'The previous provider ran out of quota. Continue the existing task from its current state. '
                                'Preserve the required final response format. Inspect existing tool results; do not repeat '
                                'completed edits or commands. Finish only the remaining work.', 'resume': True})
                    await self.store.receipt(key, run_id, role, session, 'running')
                    continue
                detail = failure.get('message', info.get('outcome', 'no completion outcome'))
                code = f"HTTP {failure['status']}: " if failure.get('status') else ''
                if quota_error(failure) and self.settings.opencode_auto_fallback:
                    detail += ' No usable fallback remains (or fallback limit reached). Connect another provider/model or replenish quota.'
                raise RuntimeError(f'OpenCode session {session}: {code}{detail}')
            text = next(('\n'.join(p['text'] for p in m.get('content', []) if p['type'] == 'text')
                         for m in messages['data'] if any(p['type'] == 'text' for p in m.get('content', []))), '')
            if not text:
                raise RuntimeError(f'No assistant result in {session}')
            await self.store.receipt(key, run_id, role, session, 'done', text)
            return text
        except asyncio.CancelledError:
            with suppress(httpx.HTTPError):
                await self.request('POST', f'/session/{session}/interrupt')
            raise
