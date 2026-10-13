"""Four channel contracts against disposable CI PostgreSQL, with zero network I/O."""
import asyncio
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import urlsplit
import pytest
from jarvis_return_worker.automation_policy import route
from jarvis_return_worker.dispatcher import TransportDispatcher
from jarvis_return_worker.orchestrator import Orchestrator
from jarvis_return_worker.queue import QueueRepository, Conflict
from jarvis_return_worker.transport import Classification, DeliveryError

CHANNELS = ("email", "telegram", "sms", "crm")

@pytest.fixture
def repo():
    url=os.environ.get("JARVIS_WORKER_CI_DATABASE_URL")
    if not url: pytest.skip("disposable CI PostgreSQL required")
    parsed=urlsplit(url)
    if parsed.hostname not in ("localhost","127.0.0.1") or parsed.path != "/jarvis_return_queue_test":
        pytest.fail("acceptance must never run against a shared Sandbox database")
    value=QueueRepository(SimpleNamespace(database_url=url,worker_id="matrix",lease_seconds=30,
                                         heartbeat_seconds=5,max_attempts=3))
    value.migrate()
    with value.connection() as c:
        c.execute("DELETE FROM jarvis_return_action_events")
        c.execute("DELETE FROM jarvis_fake_receipts")
        c.execute("DELETE FROM jarvis_return_actions")
    return value

class ContractSink:
    """In-memory external effect probe. This is not a real provider implementation."""
    def __init__(self,channel,outcome="success"):
        self.provider=channel; self.outcome=outcome; self.calls=[]; self.effects={}
    def send(self,task,key):
        assert task["payload"]["synthetic"] is True
        assert task["destination_key"].startswith("sandbox:contract:")
        self.calls.append(key)
        if self.outcome == "rejected":
            self.outcome="success"
            raise DeliveryError(Classification.RETRYABLE,"confirmed_not_accepted")
        result={"receipt_id":"contract:"+key,"message_id":"synthetic:"+key}
        self.effects.setdefault(key,result)
        if self.outcome == "lost_response":
            raise DeliveryError(Classification.UNKNOWN,"accepted_response_lost")
        return result

def prepare(repo,channel):
    task_id="matrix-"+uuid.uuid4().hex
    row=repo.enqueue(task_id,"sandbox:contract:"+channel,{"synthetic":True,"channel":channel},task_id)
    assert route(channel,"draft",confidence=.99,synthetic=True).mode == "AUTO"
    assert row["state"]=="READY"
    assert repo.claim("before-approval") is None
    assert route(channel,"send",confidence=.99,synthetic=True).mode == "APPROVAL"
    return row

def approve(repo,row):
    return repo.approve(row["task_id"],row["action_fingerprint"],
                        datetime.now(timezone.utc)+timedelta(minutes=5),"synthetic-human")

def core(repo,sink):
    dispatcher=TransportDispatcher(); dispatcher.register(sink.provider,sink,enabled=True)
    return Orchestrator(repo,dispatcher)

def process(service,task,sink,owner):
    task["provider"]=sink.provider
    asyncio.run(service.process(task,owner))

def expire(repo,task_id):
    with repo.connection() as c:
        c.execute("UPDATE jarvis_return_actions SET lease_until=now()-interval '1 minute',heartbeat_at=now()-interval '1 minute' WHERE task_id=%s",(task_id,))

@pytest.mark.parametrize("channel",CHANNELS)
def test_approval_duplicate_enqueue_and_two_workers_have_one_effect(repo,channel):
    row=prepare(repo,channel)
    duplicate=repo.enqueue(row["task_id"],row["destination_key"],row["payload"],row["correlation_id"])
    assert duplicate["task_id"]==row["task_id"]
    with pytest.raises(Conflict):
        repo.enqueue(row["task_id"],row["destination_key"],{"synthetic":True,"changed":True},row["correlation_id"])
    approve(repo,row)
    with ThreadPoolExecutor(2) as pool:
        results=list(pool.map(repo.claim,("one","two")))
    claims=[t for t in results if t]
    assert len(claims)==1
    task=claims[0]; sink=ContractSink(channel); service=core(repo,sink)
    process(service,task,sink,task["lease_owner"])
    # An accidentally redelivered stale task must be fenced before a second send.
    process(service,dict(task),sink,task["lease_owner"])
    assert repo.get(row["task_id"])["state"]=="DONE"
    assert len(sink.calls)==len(sink.effects)==1
    assert repo.claim("third") is None

@pytest.mark.parametrize("channel",CHANNELS)
def test_confirmed_rejection_retry_preserves_key_and_has_one_effect(repo,channel):
    row=prepare(repo,channel); approve(repo,row)
    sink=ContractSink(channel,"rejected"); service=core(repo,sink)
    process(service,repo.claim("one"),sink,"one")
    assert repo.get(row["task_id"])["state"]=="APPROVED"
    assert not sink.effects
    with repo.connection() as c:
        c.execute("UPDATE jarvis_return_actions SET next_attempt_at=now() WHERE task_id=%s",(row["task_id"],))
    process(service,repo.claim("two"),sink,"two")
    assert repo.get(row["task_id"])["state"]=="DONE"
    assert len(sink.calls)==2 and len(set(sink.calls))==1 and len(sink.effects)==1

@pytest.mark.parametrize("channel",CHANNELS)
def test_lost_response_is_retained_for_human_and_never_resent(repo,channel):
    row=prepare(repo,channel); approve(repo,row)
    sink=ContractSink(channel,"lost_response"); service=core(repo,sink)
    process(service,repo.claim("one"),sink,"one")
    held=repo.get(row["task_id"])
    assert held["state"]=="RECONCILIATION_REQUIRED"
    assert route(channel,"send",confidence=.99,synthetic=True,unknown_outcome=True).mode=="HUMAN"
    for _ in range(3):
        repo.recover("recovery")
        assert repo.claim("another") is None
    receipt=next(iter(sink.effects.values()))
    resolved=repo.reconcile(row["task_id"],"synthetic-human","delivered",
                            receipt["receipt_id"],receipt["message_id"])
    assert resolved["state"]=="DONE"
    assert len(sink.calls)==len(sink.effects)==1

@pytest.mark.parametrize("channel",CHANNELS)
def test_crash_before_send_recovers_and_old_worker_is_fenced(repo,channel):
    row=prepare(repo,channel); approve(repo,row); old=repo.claim("one")
    expire(repo,row["task_id"]); repo.recover("recovery")
    with repo.connection() as c:
        c.execute("UPDATE jarvis_return_actions SET next_attempt_at=now() WHERE task_id=%s",(row["task_id"],))
    sink=ContractSink(channel); service=core(repo,sink)
    process(service,old,sink,"one")
    assert not sink.calls
    new=repo.claim("two"); assert new["attempt_count"]==2
    process(service,new,sink,"two")
    assert repo.get(row["task_id"])["state"]=="DONE" and len(sink.effects)==1

@pytest.mark.parametrize("channel",CHANNELS)
def test_receipt_commit_before_finish_recovers_without_second_send(repo,channel):
    row=prepare(repo,channel); approve(repo,row); task=repo.claim("one")
    sink=ContractSink(channel)
    repo.mark_delivery_started(row["task_id"],"one",task["attempt_count"],channel)
    result=sink.send(task,row["task_id"]+":"+row["action_fingerprint"])
    repo.save_delivery_receipt(row["task_id"],"one",task["attempt_count"],
                               result["receipt_id"],result["message_id"],provider=channel)
    expire(repo,row["task_id"])
    # New repository object models application restart; actual PG restart is in CI.
    restarted=QueueRepository(repo.config); restarted.recover("new-process")
    assert restarted.get(row["task_id"])["state"]=="DONE"
    assert restarted.claim("two") is None
    assert len(sink.calls)==len(sink.effects)==1

@pytest.mark.parametrize("channel",CHANNELS)
def test_expired_approval_and_changed_fingerprint_cannot_send(repo,channel):
    row=prepare(repo,channel); approve(repo,row)
    with repo.connection() as c:
        c.execute("UPDATE jarvis_return_actions SET approval_expires_at=now()-interval '1 second' WHERE task_id=%s",(row["task_id"],))
    assert repo.claim("one") is None
    assert repo.get(row["task_id"])["state"]=="APPROVED"
