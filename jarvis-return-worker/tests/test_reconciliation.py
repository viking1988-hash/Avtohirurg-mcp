"""Provider-neutral reconciliation integration tests; PostgreSQL is disposable CI only."""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis_return_worker.api import router
from jarvis_return_worker.metrics import render_metrics
from jarvis_return_worker.queue import Conflict, QueueRepository
from jarvis_return_worker.reconciliation import mutate

READ_TOKEN = "r" * 40
APPROVAL_TOKEN = "a" * 40

@pytest.fixture
def repo():
    url = os.environ.get("JARVIS_WORKER_CI_DATABASE_URL")
    if not url:
        pytest.skip("ephemeral worker CI PostgreSQL not configured")
    result = QueueRepository(SimpleNamespace(database_url=url, worker_id="reconcile-ci",
        lease_seconds=30, heartbeat_seconds=5, max_attempts=3))
    result.migrate()
    return result

def uncertain(repo):
    task_id = "reconcile-ci-" + uuid.uuid4().hex
    row = repo.enqueue(task_id, "sandbox:telegram:test-chat", "test-only payload", task_id)
    repo.approve(task_id, row["action_fingerprint"],
                 datetime.now(timezone.utc) + timedelta(minutes=5), "ci-human")
    task = repo.claim("worker-before-crash")
    repo.mark_delivery_started(task_id, task["lease_owner"], task["attempt_count"], "telegram")
    with repo.connection() as conn:
        conn.execute("""UPDATE jarvis_return_actions SET lease_until=now()-interval '1 minute',
            heartbeat_at=now()-interval '1 minute' WHERE task_id=%s""", (task_id,))
    assert repo.recover("recovery-ci") == 1
    return task_id, repo.get(task_id)

def call(repo, task_id, actor, action, row, **kwargs):
    return mutate(repo, task_id, actor, action, row["version"],
                  reason="verified against isolated test channel", **kwargs)

def test_confirm_delivered_is_atomic_and_blocks_repeat(repo):
    task_id, row = uncertain(repo)
    assert row["state"] == "RECONCILIATION_REQUIRED"
    confirmed = call(repo, task_id, "operator-a", "confirm-delivered", row,
        provider="telegram", provider_message_id="tg-101",
        provider_receipt="telegram:test-chat:101", comment="message visible in test chat")
    assert confirmed["state"] == "DELIVERED_CONFIRMED"
    assert confirmed["delivery_status"] == "DELIVERED"
    assert confirmed["provider_message_id"] == confirmed["external_message_id"] == "tg-101"
    assert confirmed["provider_receipt"] == confirmed["delivery_receipt"] == "telegram:test-chat:101"
    with pytest.raises(Conflict):
        mutate(repo, task_id, "operator-a", "confirm-delivered", row["version"],
               reason="repeat", provider="telegram", provider_message_id="tg-101",
               provider_receipt="telegram:test-chat:101")
    assert repo.get(task_id)["version"] == confirmed["version"]
    with repo.connection() as conn:
        actions = conn.execute("""SELECT action,actor,previous_state,new_state,reason,correlation_id,metadata
            FROM jarvis_return_action_events WHERE task_id=%s ORDER BY event_id""", (task_id,)).fetchall()
    assert any(e["action"] == "confirm-delivered" and e["actor"] == "operator-a" for e in actions)
    assert any(e["action"] == "reconcile_failed" for e in actions)
    delivered_event=next(e for e in actions if e["action"] == "confirm-delivered")
    assert delivered_event["metadata"]["snapshot"]["provider_message_id"] == "tg-101"
    assert delivered_event["metadata"]["snapshot"]["delivery_status"] == "DELIVERED"

def test_confirm_undelivered_requires_new_approval_and_recovery_cannot_resend(repo):
    task_id, row = uncertain(repo)
    rejected = call(repo, task_id, "operator-a", "confirm-undelivered", row,
                    comment="test chat checked; no message")
    assert rejected["state"] == "UNDELIVERED_CONFIRMED"
    ready = call(repo, task_id, "operator-a", "request-new-approval", rejected)
    assert ready["state"] == "READY"
    assert ready["approved_by"] is None and ready["approved_fingerprint"] is None
    assert ready["delivery_started_at"] is None
    assert repo.recover("recovery-after-reconcile") == 0
    assert repo.claim("worker-after-reconcile") is None
    assert repo.get(task_id)["attempt_count"] == 1

def test_two_operators_with_same_version_only_one_reconcile_wins(repo):
    task_id, row = uncertain(repo)
    def attempt(actor):
        try:
            return mutate(repo, task_id, actor, "reconcile", row["version"],
                          reason="operator review started")
        except Conflict:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ["operator-a", "operator-b"]))
    winners = [item for item in results if item]
    assert len(winners) == 1
    assert winners[0]["reconciliation_status"] == "IN_REVIEW"
    with repo.connection() as conn:
        counts = conn.execute("""SELECT
            count(*) FILTER(WHERE action='reconcile_started') AS started,
            count(*) FILTER(WHERE action='reconcile_failed') AS failed
            FROM jarvis_return_action_events WHERE task_id=%s""", (task_id,)).fetchone()
    assert counts["started"] == 1 and counts["failed"] == 1

def test_restart_during_review_keeps_version_and_delivery_evidence(repo):
    task_id, row = uncertain(repo)
    reviewed = call(repo, task_id, "operator-a", "reconcile", row)
    restarted = QueueRepository(repo.config)
    current = restarted.get(task_id)
    assert current["version"] == reviewed["version"]
    final = call(restarted, task_id, "operator-b", "confirm-delivered", current,
        provider="telegram", provider_receipt="tg-receipt-202",
        comment="confirmed in isolated test chat; provider message id unavailable")
    assert final["state"] == "DELIVERED_CONFIRMED"
    assert final["provider_message_id"] is None
    assert final["provider_receipt"] == "tg-receipt-202"

def test_reads_are_protected_logged_and_return_provider_safe_delivery_projection(repo):
    task_id, row = uncertain(repo)
    app = FastAPI()
    app.include_router(router)
    app.state.repository = repo
    app.state.config = SimpleNamespace(read_token=READ_TOKEN, approval_token=APPROVAL_TOKEN,
                                       operator_id="sandbox-operator")
    with TestClient(app) as client:
        assert client.get(f"/queue/{task_id}").status_code == 401
        task_resp = client.get(f"/queue/{task_id}", headers={"Authorization": "Bearer "+READ_TOKEN})
        delivery_resp = client.get(f"/queue/{task_id}/delivery", headers={"Authorization": "Bearer "+READ_TOKEN})
        events_resp = client.get(f"/queue/{task_id}/events", headers={"Authorization": "Bearer "+READ_TOKEN})
    assert task_resp.status_code == delivery_resp.status_code == events_resp.status_code == 200
    assert delivery_resp.json()["provider"] == "telegram"
    assert "payload" not in delivery_resp.json()
    assert any(e["action"] == "task_read" for e in events_resp.json()["events"])

def test_stale_api_version_and_duplicate_confirm_are_rejected_and_audited(repo):
    task_id, row = uncertain(repo)
    app = FastAPI()
    app.include_router(router)
    app.state.repository = repo
    app.state.config = SimpleNamespace(read_token=READ_TOKEN, approval_token=APPROVAL_TOKEN,
                                       operator_id="sandbox-operator")
    headers = {"Authorization": "Bearer "+APPROVAL_TOKEN}
    with TestClient(app) as client:
        first = client.post(f"/queue/{task_id}/reconcile", headers=headers,
            json={"expected_version":row["version"],"reason":"review","comment":"first"})
        assert first.status_code == 200
        stale = client.post(f"/queue/{task_id}/confirm-undelivered", headers=headers,
            json={"expected_version":row["version"],"reason":"stale"})
        assert stale.status_code == 409
    assert repo.get(task_id)["reconciliation_status"] == "IN_REVIEW"

def test_reconciliation_metrics_export_persisted_counters(repo):
    task_id, row = uncertain(repo)
    call(repo, task_id, "operator-a", "reconcile", row)
    output = render_metrics(repo).decode()
    for metric in ("reconciliation_total", "reconciliation_success_total",
                   "reconciliation_failed_total", "delivery_confirmed_total",
                   "delivery_rejected_total", "reconciliation_required_total",
                   "manual_review_total", "provider_unknown_total"):
        assert f"#{' HELP'} {metric}" in output
