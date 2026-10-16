import asyncio
import pytest
from starlette.requests import Request
from fastapi import HTTPException
import jarvis_return_worker.app as worker_app

class Repo:
    def stats(self):
        return {'OPEN':0,'READY':0,'APPROVED':0,'RETRY':0,'PROCESSING':0,'FAILED':0,'RECONCILIATION_REQUIRED':0,'retry_count':0,'recovery':0,'approve':0}

def _request(auth=None):
    headers=[]
    if auth:
        headers=[(b'authorization',auth.encode())]
    scope={'type':'http','method':'GET','path':'/metrics','headers':headers,'app':worker_app.app}
    worker_app.app.state.repository=Repo()
    return Request(scope)

def test_metrics_rejects_unauthenticated(monkeypatch):
    monkeypatch.setenv('JARVIS_METRICS_TOKEN','metrics-test-token')
    with pytest.raises(HTTPException) as exc:
        asyncio.run(worker_app.metrics(_request()))
    assert exc.value.status_code == 401

def test_metrics_is_read_only_prometheus_text(monkeypatch):
    monkeypatch.setenv('JARVIS_METRICS_TOKEN','metrics-test-token')
    monkeypatch.setattr(worker_app,'render_metrics',lambda repo: b'outbox_pending_total 0\nreconciliation_required_queue 0\noutbox_failed_total 0\n')
    r=asyncio.run(worker_app.metrics(_request('Bearer metrics-test-token')))
    assert r.status_code == 200
    body=r.body.decode()
    assert 'outbox_pending_total 0' in body
    assert 'reconciliation_required_queue 0' in body
    assert 'outbox_failed_total 0' in body
