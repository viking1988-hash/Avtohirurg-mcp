"""End-to-end outbox -> Dispatcher -> mock Bot API -> Jarvis operator API.

PostgreSQL must be the disposable CI database. Telegram HTTP is handled by an
in-process mock; the test never opens a network connection.
"""
import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch
import json

import pytest
from fastapi.testclient import TestClient

from jarvis_return_worker.app import _create_dispatch_intent
from jarvis_return_worker.dispatcher import TransportDispatcher
from jarvis_return_worker.orchestrator import Orchestrator
from jarvis_return_worker.outbox import OutboxReplay, SubscriberRegistry
from jarvis_return_worker.queue import QueueRepository
from jarvis_return_worker.transport import DeliveryError, Classification, FakeTransport, TelegramTransport
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


def prepare(repo,suffix,destination="sandbox:synthetic",payload=None):
    task_id="outbox-e2e-"+suffix
    row=repo.enqueue(task_id,destination,payload if payload is not None else {"message":"synthetic only"},"corr-"+suffix)
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


class MockBotResponse:
    status=200
    def __init__(self,body): self.body=body
    def __enter__(self): return self
    def __exit__(self,*args): return False
    def read(self,limit): return self.body[:limit]


def operator_client(repo,orchestrator):
    app.state.config=SimpleNamespace(read_token="r"*40,approval_token="a"*40,operator_id="ci-human")
    app.state.repository=repo
    app.state.orchestrator=orchestrator
    return TestClient(app)


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


def test_mock_telegram_bot_api_receipt_and_duplicate_replay(repo):
    chat_id="sandbox-chat-741"
    task_id,event_id=prepare(repo,uuid.uuid4().hex,"sandbox:telegram:"+chat_id,"synthetic Telegram test")
    assert repo.claim("outbox-chain-ci") is None
    replay,count=replay_approval(repo,event_id)
    assert count==1
    with repo.connection() as conn:
        intent=conn.execute("SELECT provider FROM jarvis_provider_dispatch_intents WHERE event_id=%s",
            (event_id,)).fetchone()
    assert intent["provider"]=="telegram"
    task=repo.claim("outbox-chain-ci")
    provider=TelegramTransport("mock-token-never-logged",chat_id,1)
    calls=[]
    def mock_bot_api(request,timeout):
        calls.append((request.method,request.full_url, json.loads(request.data)))
        return MockBotResponse(json.dumps({"ok":True,"result":{"message_id":741,
            "chat":{"id":chat_id},"text":"synthetic Telegram test"}}).encode())
    worker=Worker(repo,provider,asyncio.Event())
    with patch("jarvis_return_worker.transport.urlopen",side_effect=mock_bot_api):
        asyncio.run(worker.process(task))
    assert len(calls)==1
    assert calls[0][0]=="POST"
    assert calls[0][2]=={"chat_id":chat_id,"text":"synthetic Telegram test"}
    assert "mock-token-never-logged" in calls[0][1]  # present only in the private request URL
    row=repo.get(task_id)
    assert row["state"]=="DONE"
    assert row["provider"]=="telegram"
    assert row["provider_message_id"]=="741"
    assert row["chat_id"]==chat_id
    assert row["provider_receipt"]==f"telegram:{chat_id}:741"
    assert row["delivery_response"]=={"ok":True,"message_id":"741","chat_id":chat_id}
    replay.replay_one()
    assert repo.claim("telegram-worker-after-replay") is None
    assert len(calls)==1


def test_mock_telegram_response_lost_is_reviewed_without_resend(repo,caplog):
    chat_id="sandbox-chat-742"
    task_id,event_id=prepare(repo,uuid.uuid4().hex,"sandbox:telegram:"+chat_id,"synthetic uncertain send")
    replay,count=replay_approval(repo,event_id)
    assert count==1
    task=repo.claim("outbox-chain-ci")
    provider=TelegramTransport("mock-token-never-logged",chat_id,1)
    mock_sink={"accepted":0,"message_id":"742"}
    def mock_bot_api_then_drop_response(request,timeout):
        mock_sink["accepted"]+=1
        raise TimeoutError("mock response lost after acceptance")
    worker=Worker(repo,provider,asyncio.Event())
    with patch("jarvis_return_worker.transport.urlopen",side_effect=mock_bot_api_then_drop_response):
        asyncio.run(worker.process(task))
    assert mock_sink["accepted"]==1
    row=repo.get(task_id)
    assert row["state"]=="RECONCILIATION_REQUIRED"
    assert row["delivery_status"]=="UNKNOWN"
    assert row["reconciliation_status"]=="REQUIRED"
    assert repo.claim("telegram-worker-no-resend") is None

    client=operator_client(repo,worker.orchestrator)
    operator_headers={"Authorization":"Bearer "+"a"*40}
    read_headers={"Authorization":"Bearer "+"r"*40}
    review=client.post(f"/queue/{task_id}/reconcile",headers=operator_headers,
        json={"expected_version":row["version"],"reason":"mock sink confirms accepted",
              "comment":"synthetic mock receipt only"})
    assert review.status_code==200
    assert review.json()["reconciliation_status"]=="IN_REVIEW"
    confirmed=client.post(f"/queue/{task_id}/confirm-delivered",headers=operator_headers,
        json={"expected_version":review.json()["version"],"reason":"mock sink evidence",
              "comment":"test-only mock evidence","provider":"telegram",
              "provider_message_id":mock_sink["message_id"],
              "provider_receipt":f"telegram:{chat_id}:{mock_sink['message_id']}"})
    assert confirmed.status_code==200
    assert confirmed.json()["state"]=="DELIVERED_CONFIRMED"
    assert repo.claim("telegram-worker-after-review") is None
    events=client.get(f"/queue/{task_id}/events",headers=read_headers)
    assert events.status_code==200
    actions=[entry["action"] for entry in events.json()["events"]]
    assert "delivery_uncertain" in actions
    assert "reconcile_started" in actions
    assert "confirm-delivered" in actions
    assert mock_sink["accepted"]==1
    assert "mock-token-never-logged" not in caplog.text
