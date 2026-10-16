import pytest
from fastapi.testclient import TestClient
from jarvis_return_worker.app import app

class Repo:
    def stats(self):
        return {'OPEN':0,'READY':0,'APPROVED':0,'RETRY':0,'PROCESSING':0,'FAILED':0,'RECONCILIATION_REQUIRED':0,'retry_count':0,'recovery':0,'approve':0}

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('JARVIS_METRICS_TOKEN','metrics-test-token')
    with TestClient(app) as c:
        app.state.repository=Repo()
        yield c

def test_metrics_rejects_unauthenticated(client):
    assert client.get('/metrics').status_code == 401

def test_metrics_is_read_only_prometheus_text(client, monkeypatch):
    monkeypatch.setattr('jarvis_return_worker.app.render_metrics', lambda repo: b'outbox_pending_total 0\nreconciliation_required_queue 0\noutbox_failed_total 0\n')
    r=client.get('/metrics',headers={'Authorization':'Bearer metrics-test-token'})
    assert r.status_code == 200
    assert 'outbox_pending_total 0' in r.text
    assert 'reconciliation_required_queue 0' in r.text
    assert 'outbox_failed_total 0' in r.text
