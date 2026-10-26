"""One-shot, fake-only disposable Railway E2E probe; no public endpoint or credentials."""
import asyncio
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from .config import Config

log = logging.getLogger(__name__)

async def run_probe(repo, orchestrator):
    if os.environ.get('JARVIS_PR47_E2E_PROBE') != '1':
        return
    if not Config.isolated_e2e():
        raise RuntimeError('PR47 probe refused outside exact disposable fake-only environment')
    task_id = 'pr47-e2e-' + uuid.uuid4().hex
    try:
        prepared = await asyncio.to_thread(
            orchestrator.enqueue, task_id, 'sandbox:fake',
            'synthetic PR47 disposable test; no customer data', task_id)
        expires = datetime.now(timezone.utc) + timedelta(minutes=15)
        await asyncio.to_thread(orchestrator.approve, task_id,
                                prepared['action_fingerprint'], expires, 'pr47-synthetic-operator')
        for _ in range(90):
            await asyncio.sleep(1)
            task = await asyncio.to_thread(repo.get, task_id)
            if task and task['state'] in ('DONE', 'FAILED', 'RECONCILIATION_REQUIRED'):
                break
        else:
            raise RuntimeError('timed out waiting for delivery')
        with repo.connection() as conn:
            receipt = conn.execute(
                'SELECT count(*) AS n FROM jarvis_fake_receipts WHERE task_id=%s',
                (task_id,)).fetchone()['n']
            attempts = conn.execute(
                'SELECT attempt_count FROM jarvis_return_actions WHERE task_id=%s',
                (task_id,)).fetchone()['attempt_count']
        if task['state'] != 'DONE' or receipt != 1 or attempts != 1:
            raise RuntimeError(f'invalid outcome state={task["state"]} receipts={receipt} attempts={attempts}')
        log.warning('PR47_DISPOSABLE_E2E_PASS state=DONE fake_receipts=1 attempts=1')
    except Exception:
        log.exception('PR47_DISPOSABLE_E2E_FAIL')
        raise
