"""End-to-end outbox -> dispatcher -> Fake Provider -> reconciliation API tests.

PostgreSQL must be the disposable CI database. No Telegram/network call is made.
"""
import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from jarvis_return_worker.app import _create_dispatch_intent
from jarvis_return_worker.dispatcher import TransportDispatcher
from jarvis_return_worker.orchestrator import Orchestrator
from jarvis_return_worker.outbox import OutboxReplay, SubscriberRegistry
from jarvis_return_worker.queue import QueueRepository
from jarvis_return_worker.transport import DeliveryError, Classification, FakeTransport
from jarvis_return_worker.worker import Worker
from jarvis_return_worker.app import app


@pytest.fixture
def repo():
    url=os.environ.get("JARVIS_WORKER_CI_DATABASE_URL")
    if not url:
        pytest.skip("ephemeral worker CI PostgreSQL not configured")
    cfg=SimpleNamespace(database_url=url,worker_id="outbox-chain-ci",lease_seconds=30,
        heartbeat_seconds=5,max_attempts=3,require_outbox_dispatch=True)
    value=QueueRepository(cfg)
    value.migrate()
    return value


class CountingFake(FakeTransport):
    provider="fake"
    def __init__(self,error=None):
        super().__init__()
        self.calls=0
        self.error=error
    def send(self,task,idempotency_key):
        self.calls+=1
        if self.error:
            raise self.error
        return super().send(task,idempotency_key)


def prepare(repo,suffix):
    task_id="outbox-e2e-"+suffix
    row=repo.enqueue(task_id,"sandbox:synthetic",{"message":"synthetic only"},"corr-"+suffix)
    repo.approve(task_id,row["action_fingerprint"],
        datetime.now(timezone.utc)+timedelta(minutes=5),"ci-human")
    with repo.connection() as conn:
        event=conn.execute("""SELECT event_id FROM jarvis_outbox_events
            WHERE aggregate_id=%s AND event_type='TaskApproved' ORDER BY created_at DESC LIMIT 1""",
            (task_id,)).fetchone()
    assert event
    return task_id,event["event_id"]


def replay_approval(repo,event_id):
    registry=SubscriberRegistry()
    registry.register("jarvis.dispatcher-intent","TaskApproved",_create_dispatch_intent)
    replay=OutboxReplay(repo,registry,owner="outbox-e2e-replay")
    replay.register_subscribers()
    # Other old outbox rows without subscribers are ignored; replay until this
    # exact approval has a committed processed marker.
    for _ in range(100):
        with repo.connection() as conn:
            done=conn.execute("""SELECT 1 FROM jarvis_processed_events
                WHERE event_id=%s AND subscriber_name='jarvis.dispatcher-intent'""",
                (event_id,)).fetchone()
        if done:
            break
        if not replay.replay_one():
            break
    with repo.connection() as conn:
        return replay,conn.execute("""SELECT count(*) AS n FROM jarvis_provider_dispatch_intents
            WHERE event_id=%s""",(event_id,)).fetchone()["n"]


def read_delivery(repo,orchestrator,task_id):
    app.state.config=SimpleNamespace(read_token="r"*40,approval_token="a"*40,operator_id="ci-human")
    app.state.repository=repo
    app.state.orchestrator=orchestrator
    response=TestClient(app).get("/queue/"+task_id+"/delivery",
        headers={"Authorization":"Bearer "+"r"*40})
    assert response.status_code==200
    return response.json()


def test_approval_outbox_gates_fake_dispatch_and_operator_receipt_read(repo):
    task_id,event_id=prepare(repo,uuid.uuid4().hex)
    # The task cannot be claimed before TaskApproved is replayed.
    assert repo.claim("outbox-chain-ci") is None
    replay,count=replay_approval(repo,event_id)
    assert count==1
    with repo.connection() as conn:
        intent=conn.execute("SELECT provider FROM jarvis_provider_dispatch_intents WHERE event_id=%s",
            (event_id,)).fetchone()
    assert intent["provider"]=="fake"
    task=repo.claim("outbox-chain-ci")
    assert task["task_id"]==task_id
    fake=CountingFake()
    worker=Worker(repo,fake,asyncio.Event())
    asyncio.run(worker.process(task))
    assert repo.get(task_id)["state"]=="DONE"
    assert fake.calls==1
    delivery=read_delivery(repo,worker.orchestrator,task_id)
    assert delivery["provider"]=="fake"
    assert delivery["provider_receipt"]
    # Replay/restart cannot dispatch this completed task a second time.
    assert replay.replay_one() is False
    assert repo.claim("worker-after-replay") is None
    assert fake.calls==1


def test_unknown_fake_result_is_review_only_through_operator_api(repo):
    from jarvis_return_worker.transport import DeliveryError,Classification
    task_id,event_id=prepare(repo,uuid.uuid4().hex)
    assert repo.claim("outbox-chain-ci") is None
    replay,count=replay_approval(repo,event_id)
    assert count==1
    task=repo.claim("outbox-chain-ci")
    fake=CountingFake(DeliveryError(Classification.UNKNOWN,"synthetic_timeout"))
    worker=Worker(repo,fake,asyncio.Event())
    asyncio.run(worker.process(task))
    assert fake.calls==1
    row=repo.get(task_id)
    assert row["state"]=="RECONCILIATION_REQUIRED"
    assert repo.claim("worker-after-unknown") is None
    delivery=read_delivery(repo,worker.orchestrator,task_id)
    assert delivery["reconciliation_status"]=="REQUIRED"
    assert fake.calls==1
