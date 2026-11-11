r"""Explicit opt-in real-provider smoke test. Creates only a temporary project.

Run: .venv\Scripts\python.exe -m backend.tests.live_smoke
Uses the running backend and its configured OpenCode model (may incur cost).
"""
import asyncio
import json
import os
from pathlib import Path
import tempfile

import httpx


async def main():
    root = Path(os.environ.get('LOCALAPPDATA', tempfile.gettempdir())) / 'Temp' / 'opencode'
    root.mkdir(parents=True, exist_ok=True)
    project = Path(tempfile.mkdtemp(prefix='orbit-smoke-', dir=root))
    async with httpx.AsyncClient(base_url='http://127.0.0.1:8000', timeout=30) as client:
        response = await client.post('/api/runs', json={
            'directory': str(project), 'mode': 'human',
            'prompt': 'Create exactly one file hello.py containing def add(a, b): return a + b. '
                      'No dependencies. Reviewer must run python -c "from hello import add; assert add(2, 3) == 5" '
                      'in this directory. Keep analysis brief. Research only this local empty directory; no web needed.'})
        response.raise_for_status()
        run_id = response.json()['id']
        print(json.dumps({'run_id': run_id, 'project': str(project)}), flush=True)
        approved_plan = False
        seen_permissions = set()
        async with asyncio.timeout(600):
            while True:
                response = await client.get(f'/api/runs/{run_id}')
                response.raise_for_status()
                run = response.json()
                if run['interrupts']:
                    approved_plan = True
                    response = await client.post(f'/api/runs/{run_id}/resume', json={'approved': True})
                    response.raise_for_status()
                    print('Approved checkpointed plan', flush=True)
                for permission in run['permissions']:
                    if permission['id'] in seen_permissions:
                        continue
                    action = permission['action']
                    # Only this isolated smoke task's edit and assertion command are authorized.
                    resources = permission['resources']
                    allowed = action == 'edit' and all(r.replace('\\', '/').endswith('hello.py') for r in resources)
                    allowed |= action == 'shell' and all('from hello import add' in r and 'assert add(2, 3)' in r for r in resources)
                    if not allowed:
                        await client.post(f'/api/runs/{run_id}/cancel', json={})
                        raise RuntimeError(f'Unexpected smoke permission: {action} {resources}')
                    result = await client.post(f"/api/runs/{run_id}/permissions/{permission['sessionID']}/{permission['id']}", json={'approved': True})
                    result.raise_for_status()
                    seen_permissions.add(permission['id'])
                    print(f'Approved real tool: {action}', flush=True)
                if run['status'] in ('completed', 'failed', 'cancelled', 'rejected'):
                    print(json.dumps({'status': run['status'], 'error': run['error'],
                                      'roles': [s['role'] for s in run['sessions']],
                                      'review': run['state'].get('outputs', {}).get('reviewer')}), flush=True)
                    assert run['status'] == 'completed', run['error']
                    assert approved_plan
                    assert (project / 'hello.py').exists()
                    history = await client.get(f'/api/runs/{run_id}/checkpoints')
                    assert len(history.json()) > 5
                    print('LIVE SMOKE PASSED', flush=True)
                    return
                await asyncio.sleep(1)


if __name__ == '__main__':
    asyncio.run(main())
