import io
import json
import os
import asyncio
import uuid
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest

from jarvis_return_worker.crm import CRMAdapter, CRMProvider, EVENT_OPERATIONS
from jarvis_return_worker.transport import Classification, DeliveryError


class Response:
    def __init__(self, data, status=200):
        self.status = status
        self.raw = json.dumps(data).encode()
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self, limit): return self.raw[:limit]


def event(event_type="BookingCreated"):
    return {"event_id":"event-1", "event_type":event_type,
            "aggregate_id":"booking-123", "correlation_id":"corr-1",
            "payload":{"booking_id":"booking-123", "customer_name":"test"}}


def test_adapter_maps_booking_lifecycle_events_to_durable_commands():
    assert EVENT_OPERATIONS == {
        "BookingCreated":"send", "BookingUpdated":"update",
        "BookingCancelled":"cancel", "BookingConfirmed":"update",
        "BookingCompleted":"update",
    }
    for name, operation in EVENT_OPERATIONS.items():
        command=CRMAdapter.adapt(event(name))
        assert command["operation"]==operation
        assert command["booking_id"]=="booking-123"
        assert command["idempotency_key"]=="jarvis-crm:event-1"
        assert command["payload"]["status"]


def test_adapter_ignores_unrelated_and_malformed_events():
    assert CRMAdapter.adapt(event("TaskCreated")) is None
    malformed=event()
    malformed["aggregate_id"]=""
    malformed["payload"]={}
    assert CRMAdapter.adapt(malformed) is None


def test_provider_requires_sandbox_or_staging_https_endpoint():
    with pytest.raises(ValueError): CRMProvider("http://crm-sandbox.up.railway.app", "secret")
    with pytest.raises(ValueError): CRMProvider("https://crm-staging.up.railway.app", "secret")
    with pytest.raises(ValueError): CRMProvider("https://crm-production-sandbox.up.railway.app", "secret")
    with pytest.raises(ValueError): CRMProvider("https://crm-sandbox.example", "secret")
    with pytest.raises(ValueError): CRMProvider("https://embedded:secret@crm-sandbox.up.railway.app", "secret")
    with pytest.raises(ValueError): CRMProvider("https://crm-sandbox.up.railway.app", "")


def test_provider_sends_idempotency_and_minimizes_receipt_without_logging_token():
    captured={}
    def opener(request, timeout):
        captured["request"]=request
        captured["timeout"]=timeout
        return Response({"external_booking_id":"ext-9", "status":"created",
                         "receipt_id":"receipt-9", "customer_phone":"private"})
    provider=CRMProvider("https://crm-sandbox.up.railway.app/api", "never-log-this", opener=opener)
    result=provider.send({"booking_id":"b-9"}, "stable-key")
    assert captured["request"].get_header("Idempotency-key")=="stable-key"
    assert "never-log-this" not in repr(result)
    assert result=={"external_booking_id":"ext-9", "status":"created", "receipt_id":"receipt-9"}


@pytest.mark.parametrize("status,classification",[
    (429,Classification.RETRYABLE),(401,Classification.PERMANENT),
    (500,Classification.UNKNOWN),
])
def test_provider_http_errors_are_classified_without_response_leak(status,classification):
    def opener(request, timeout):
        raise HTTPError(request.full_url,status,"private body",{},io.BytesIO(b"sensitive response"))
    provider=CRMProvider("https://crm-sandbox.up.railway.app", "secret-token", opener=opener)
    with pytest.raises(DeliveryError) as error:
        provider.update({"booking_id":"b-1"},"event-key")
    assert error.value.classification==classification
    assert "secret-token" not in str(error.value)
    assert "sensitive" not in str(error.value)


def test_network_timeout_is_unknown_and_reconcile_remains_read_only():
    calls=[]
    def opener(request, timeout):
        calls.append(request.get_method())
        if request.get_method()=="GET":
            return Response({"status":"confirmed","external_booking_id":"ext-1"})
        raise URLError("timeout")
    provider=CRMProvider("https://crm-sandbox.up.railway.app", "secret-token", opener=opener)
    with pytest.raises(DeliveryError) as error:
        provider.send({"booking_id":"b-1"},"event-key")
    assert error.value.classification==Classification.UNKNOWN
    assert provider.reconcile({"booking_id":"b-1"})["status"]=="confirmed"
    assert calls==["POST","GET"]


def test_mutation_without_receipt_is_unknown_and_never_success():
    provider=CRMProvider("https://crm-sandbox.up.railway.app","secret-token",
                         opener=lambda *a,**k:Response({"status":"created","external_booking_id":"e"}))
    with pytest.raises(DeliveryError) as error:
        provider.send({"booking_id":"b"},"event-key")
    assert error.value.classification==Classification.UNKNOWN


def test_orchestrator_delivers_crm_operation_and_persists_provider_receipt():
    from jarvis_return_worker.dispatcher import TransportDispatcher
    from jarvis_return_worker.orchestrator import Orchestrator
    class Repo:
        def __init__(self): self.saved=[]; self.failures=[]
        def finish_crm_operation(self,*args): self.saved.append(args)
        def fail_crm_operation(self,*args): self.failures.append(args)
    class Provider:
        provider='crm'
        def __init__(self): self.calls=[]
        def send(self,task,key):
            self.calls.append((task,key))
            return {'receipt_id':'receipt-1','external_booking_id':'external-1','status':'created'}
    repo=Repo(); provider=Provider(); dispatcher=TransportDispatcher()
    dispatcher.register('crm',provider)
    orchestrator=Orchestrator(repo,dispatcher)
    op={'event_id':'event-1','idempotency_key':'jarvis-crm:event-1','booking_id':'booking-1',
        'operation':'send','state':'PROCESSING','attempt_count':1,'payload':{'booking_id':'booking-1'}}
    asyncio.run(orchestrator.process_crm_operation(op,'crm-worker'))
    assert len(provider.calls)==1
    assert provider.calls[0][1]=='jarvis-crm:event-1'
    assert len(repo.saved)==1 and repo.failures==[]


def test_unknown_crm_operation_is_not_retried():
    from jarvis_return_worker.dispatcher import TransportDispatcher
    from jarvis_return_worker.orchestrator import Orchestrator
    class Repo:
        def __init__(self): self.saved=[]; self.failures=[]
        def finish_crm_operation(self,*args): self.saved.append(args)
        def fail_crm_operation(self,*args): self.failures.append(args)
    class Provider:
        provider='crm'
        def __init__(self): self.calls=0
        def send(self,task,key):
            self.calls+=1
            raise DeliveryError(Classification.UNKNOWN,'timeout')
    repo=Repo(); provider=Provider(); dispatcher=TransportDispatcher()
    dispatcher.register('crm',provider)
    op={'event_id':'event-2','idempotency_key':'jarvis-crm:event-2','booking_id':'booking-2',
        'operation':'send','state':'PROCESSING','attempt_count':1,'payload':{'booking_id':'booking-2'}}
    asyncio.run(Orchestrator(repo,dispatcher).process_crm_operation(op,'crm-worker'))
    assert provider.calls==1 and repo.saved==[]
    assert len(repo.failures)==1 and repo.failures[0][3]==Classification.UNKNOWN


def test_crm_metrics_are_exposed():
    from jarvis_return_worker import metrics
    class Stats:
        def stats(self): return {}
    metrics.observe_crm("success",0.01)
    metrics.observe_crm("unknown",0.02)
    metrics.prevent_crm_duplicate()
    output=metrics.render_metrics(Stats()).decode()
    for name in ("crm_requests_total","crm_success_total","crm_failed_total",
                 "crm_unknown_total","crm_reconciliation_total",
                 "crm_duplicate_prevented_total","crm_latency_seconds"):
        assert name in output


@pytest.fixture(scope="module")
def repository():
    url=os.environ.get("JARVIS_WORKER_CI_DATABASE_URL")
    if not url: pytest.skip("PostgreSQL integration URL is not configured")
    from jarvis_return_worker.queue import QueueRepository
    repo=QueueRepository(SimpleNamespace(database_url=url,worker_id="crm-tests",
        lease_seconds=30,heartbeat_seconds=5,max_attempts=3))
    repo.migrate()
    return repo


def test_outbox_replay_creates_one_crm_operation_and_survives_replay(repository):
    from jarvis_return_worker.events import EventBus
    from jarvis_return_worker.outbox import OutboxReplay, append_event
    suffix=str(uuid.uuid4())
    with repository.connection() as conn:
        event_id=append_event(conn,aggregate_id="booking-"+suffix,aggregate_type="booking",
            event_type="BookingCreated",event_version=1,
            payload={"booking_id":"booking-"+suffix,"synthetic":True},
            correlation_id="corr-"+suffix,causation_id="cause-"+suffix)
    registry=EventBus()
    CRMAdapter().register(registry)
    replay=OutboxReplay(repository,registry,owner="crm-replay-test")
    replay.register_subscribers()
    assert replay.replay_one() is True
    assert replay.replay_one() is False
    with repository.connection() as conn:
        rows=conn.execute("SELECT * FROM jarvis_crm_operations WHERE event_id=%s",(event_id,)).fetchall()
    assert len(rows)==1
    assert rows[0]["operation"]=="send"
    assert rows[0]["state"]=="PENDING"
    assert rows[0]["idempotency_key"]=="jarvis-crm:"+event_id
    assert rows[0]["external_booking_id"] is None
    assert rows[0]["provider_receipt"] is None


def materialize_crm_operation(repository):
    from jarvis_return_worker.events import EventBus
    from jarvis_return_worker.outbox import OutboxReplay, append_event
    suffix=str(uuid.uuid4())
    with repository.connection() as conn:
        event_id=append_event(conn,aggregate_id="booking-"+suffix,aggregate_type="booking",
            event_type="BookingCreated",event_version=1,
            payload={"booking_id":"booking-"+suffix,"synthetic":True},
            correlation_id="corr-"+suffix,causation_id="cause-"+suffix)
    registry=EventBus(); CRMAdapter().register(registry)
    replay=OutboxReplay(repository,registry,owner="crm-materializer")
    replay.register_subscribers()
    # Replay oldest matching event until this unique source event is materialized.
    for _ in range(25):
        if repository.get_crm_operation(event_id): break
        assert replay.replay_one() is True
    assert repository.get_crm_operation(event_id)
    return event_id


def test_two_crm_workers_cannot_claim_same_operation(repository):
    from concurrent.futures import ThreadPoolExecutor
    event_id=materialize_crm_operation(repository)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims=list(pool.map(repository.claim_crm_operation,('crm-worker-a','crm-worker-b')))
    owned=[claim for claim in claims if claim and claim['event_id']==event_id]
    assert len(owned)==1


def test_expired_crm_lease_requires_reconciliation_and_fences_old_worker(repository):
    from jarvis_return_worker.queue import Conflict
    event_id=materialize_crm_operation(repository)
    claim=repository.claim_crm_operation('crm-worker-old')
    assert claim and claim['event_id']==event_id
    with repository.connection() as conn:
        conn.execute("UPDATE jarvis_crm_operations SET lease_until=clock_timestamp()-interval '1 second' WHERE event_id=%s",(event_id,))
    assert repository.claim_crm_operation('crm-worker-new') is None
    row=repository.get_crm_operation(event_id)
    assert row['state']=='RECONCILIATION_REQUIRED'
    assert row['reconciliation_reason']=='lease_expired_after_claim'
    with pytest.raises(Conflict,match='stale CRM lease'):
        repository.finish_crm_operation(event_id,'crm-worker-old',claim['attempt_count'],
            {'receipt_id':'r','external_booking_id':'x','status':'created'})


def test_crm_receipt_and_done_commit_together_and_finish_is_idempotently_fenced(repository):
    from jarvis_return_worker.queue import Conflict
    event_id=materialize_crm_operation(repository)
    claim=repository.claim_crm_operation('crm-worker-once')
    result={'receipt_id':'receipt-once','external_booking_id':'external-once','status':'created'}
    done=repository.finish_crm_operation(event_id,'crm-worker-once',claim['attempt_count'],result)
    assert done['state']=='DONE'
    assert done['provider_receipt']=='receipt-once'
    assert done['external_booking_id']=='external-once'
    with pytest.raises(Conflict,match='stale CRM lease'):
        repository.finish_crm_operation(event_id,'crm-worker-once',claim['attempt_count'],result)
    persisted=repository.get_crm_operation(event_id)
    assert persisted['state']=='DONE' and persisted['provider_receipt']=='receipt-once'


def test_unknown_crm_result_persists_reconciliation_and_is_not_claimable(repository):
    from jarvis_return_worker.transport import Classification
    event_id=materialize_crm_operation(repository)
    claim=repository.claim_crm_operation('crm-unknown-worker')
    saved=repository.fail_crm_operation(event_id,'crm-unknown-worker',claim['attempt_count'],
                                        Classification.UNKNOWN,'crm_network_outcome_unknown')
    assert saved['state']=='RECONCILIATION_REQUIRED'
    assert saved['reconciliation_reason']=='crm_network_outcome_unknown'
    assert repository.claim_crm_operation('crm-after-unknown') is None
    assert repository.get_crm_operation(event_id)['state']=='RECONCILIATION_REQUIRED'
