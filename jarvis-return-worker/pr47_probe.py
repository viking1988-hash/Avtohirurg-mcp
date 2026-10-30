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
        expected_receipt = 'sandbox:receipt:' + task_id + ':' + prepared['action_fingerprint']
        if task['state'] != 'DONE' or task['delivery_receipt'] != expected_receipt:
            raise RuntimeError('fake provider receipt or DONE state mismatch')
        # Repeating the exact enqueue must not create a second delivery.
        await asyncio.to_thread(orchestrator.enqueue, task_id, 'sandbox:fake',
                                'synthetic PR47 disposable test; no customer data', task_id)
        await asyncio.sleep(2)
        with repo.connection() as conn:
            row = conn.execute(
                'SELECT state,attempt_count,delivery_receipt FROM jarvis_return_actions WHERE task_id=%s',
                (task_id,)).fetchone()
            count = conn.execute(
                'SELECT count(*) AS n FROM jarvis_return_actions WHERE task_id=%s',
                (task_id,)).fetchone()['n']
        if (row['state'] != 'DONE' or row['attempt_count'] != 1
                or row['delivery_receipt'] != expected_receipt or count != 1):
            raise RuntimeError('duplicate enqueue altered delivered task')
        log.warning('PR47_DISPOSABLE_E2E_PASS state=DONE durable_receipt=1 attempts=1 tasks=1')
    except Exception:
        log.exception('PR47_DISPOSABLE_E2E_FAIL')
        raise
