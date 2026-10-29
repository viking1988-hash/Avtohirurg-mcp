import os,uuid
from types import SimpleNamespace
import pytest
from jarvis_return_worker.events import EventBus
from jarvis_return_worker.outbox import OutboxReplay,append_event
from jarvis_return_worker.queue import QueueRepository,Conflict
from jarvis_return_worker.webhook_adapter import WebhookAdapter
from jarvis_return_worker.transport import Classification

@pytest.fixture
def repo():
 url=os.environ.get("JARVIS_WORKER_CI_DATABASE_URL")
 if not url:pytest.skip("ephemeral PostgreSQL CI only")
 r=QueueRepository(SimpleNamespace(database_url=url,worker_id="webhook-ci",lease_seconds=30,heartbeat_seconds=5,max_attempts=3))
 r.migrate()
 yield r
 with r.connection() as c:
  ids=[x["event_id"] for x in c.execute("SELECT event_id FROM jarvis_outbox_events WHERE aggregate_type='webhook' AND causation_id LIKE 'webhook-ci-%'").fetchall()]
  if ids:
   c.execute("DELETE FROM jarvis_webhook_operation_events WHERE event_id=ANY(%s)",(ids,))
   c.execute("DELETE FROM jarvis_webhook_operations WHERE event_id=ANY(%s)",(ids,))
   c.execute("DELETE FROM jarvis_outbox_audit WHERE event_id=ANY(%s)",(ids,))
   c.execute("DELETE FROM jarvis_processed_events WHERE event_id=ANY(%s)",(ids,))
   c.execute("DELETE FROM jarvis_outbox_deliveries WHERE event_id=ANY(%s)",(ids,))
   c.execute("DELETE FROM jarvis_outbox_events WHERE event_id=ANY(%s)",(ids,))

def materialize(r):
 cause="webhook-ci-"+str(uuid.uuid4())
 with r.connection() as c:
  eid=append_event(c,aggregate_id=cause,aggregate_type="webhook",event_type="WebhookRequested",
   event_version=1,payload={"method":"POST","path":"/events","body":{"synthetic":True}},
   correlation_id="corr-"+cause,causation_id=cause)
 bus=EventBus();WebhookAdapter().register(bus)
 replay=OutboxReplay(r,bus,owner="webhook-replay-ci");replay.register_subscribers()
 assert replay.replay_one(event_id=eid)
 return eid,replay

def receipt(eid,response="response"):
 return {"request_id":"request-"+eid,"response_id":response+"-"+eid,"http_status":202,
  "provider_status":"accepted","receipt":{"receipt_id":"receipt-"+eid},"duration_ms":1.5,"retry_count":0}

def test_webhook_outbox_dedup_receipt_and_version_fencing(repo):
 eid,replay=materialize(repo)
 assert replay.replay_one(event_id=eid) is False
 op=repo.get_webhook_operation(eid)
 assert op["state"]=="PENDING" and op["idempotency_key"]=="jarvis-webhook:"+eid
 claim=repo.claim_webhook_operation("worker-a")
 assert claim["attempt_count"]==1 and claim["version"]==1
 with pytest.raises(Conflict):
  repo.finish_webhook_operation(eid,"worker-a",1,0,receipt(eid))
 saved=repo.finish_webhook_operation(eid,"worker-a",1,claim["version"],receipt(eid))
 assert saved["state"]=="DONE" and saved["http_status"]==202 and saved["response_id"]==receipt(eid)["response_id"]
 assert repo.claim_webhook_operation("worker-a") is None

def test_expired_webhook_lease_and_unknown_never_resend(repo):
 eid,replay=materialize(repo);claim=repo.claim_webhook_operation("worker-before-restart")
 with repo.connection() as c:
  c.execute("UPDATE jarvis_webhook_operations SET lease_until=clock_timestamp()-interval '1 second' WHERE event_id=%s",(eid,))
 assert repo.claim_webhook_operation("worker-after-restart") is None
 saved=repo.get_webhook_operation(eid)
 assert saved["state"]=="RECONCILIATION_REQUIRED"
 assert saved["reconciliation_reason"]=="lease_expired_after_http_attempt"
 assert replay.replay_one(event_id=eid) is False
 with pytest.raises(Conflict):repo.finish_webhook_operation(eid,"worker-before-restart",claim["attempt_count"],claim["version"],receipt(eid))
 assert repo.claim_webhook_operation("worker-after-restart") is None

def test_unknown_reconciliation_is_optimistically_locked(repo):
 eid,_=materialize(repo);claim=repo.claim_webhook_operation("worker")
 repo.fail_webhook_operation(eid,"worker",claim["attempt_count"],claim["version"],Classification.UNKNOWN,
   "timeout",details={"request_id":"request-unknown","retry_count":0})
 op=repo.get_webhook_operation(eid)
 with pytest.raises(Conflict):
  repo.resolve_webhook_reconciliation(eid,"operator",op["version"]-1,"delivered",{"receipt":{"id":"proof"}})
 saved=repo.resolve_webhook_reconciliation(eid,"operator",op["version"],"delivered",
   {"receipt":{"id":"proof"},"reason":"synthetic evidence"})
 assert saved["state"]=="DONE"
 assert repo.claim_webhook_operation("worker") is None


def test_duplicate_provider_response_is_reconciliation_required(repo):
 eid1,_=materialize(repo);eid2,_=materialize(repo)
 op1=repo.claim_webhook_operation("response-worker-a")
 op2=repo.claim_webhook_operation("response-worker-b")
 assert op1["event_id"]!=op2["event_id"]
 first=receipt(eid1,"shared-response");first["response_id"]="shared-response"
 second=receipt(eid2,"shared-response");second["response_id"]="shared-response"
 assert repo.finish_webhook_operation(eid1,"response-worker-a",op1["attempt_count"],op1["version"],first)["state"]=="DONE"
 saved=repo.finish_webhook_operation(eid2,"response-worker-b",op2["attempt_count"],op2["version"],second)
 assert saved["state"]=="RECONCILIATION_REQUIRED"
 assert saved["reconciliation_reason"]=="duplicate_provider_response_id"
 assert saved["response_id"] is None
 assert saved["receipt"]["duplicate_response_id"]=="shared-response"

def test_orchestrator_is_only_provider_entrypoint(repo):
 import asyncio
 from jarvis_return_worker.dispatcher import TransportDispatcher
 from jarvis_return_worker.orchestrator import Orchestrator
 from jarvis_return_worker.outbox import OutboxReplay
 class MockProvider:
  provider="webhook"
  def __init__(self):self.calls=0
  def send(self,task,key):
   self.calls+=1
   return {"request_id":"mock-request","response_id":"mock-response-"+key,
    "http_status":200,"provider_status":"success","receipt":{"receipt_id":"mock-receipt"},
    "duration_ms":1,"retry_count":0}
 provider=MockProvider();dispatcher=TransportDispatcher();dispatcher.register("webhook",provider)
 service=Orchestrator(repo,dispatcher)
 eid=service.request_webhook("POST","/events",{"synthetic":True},
   "corr-webhook-e2e-"+str(uuid.uuid4()),"webhook-ci-e2e-"+str(uuid.uuid4()))
 registry=EventBus();WebhookAdapter().register(registry)
 replay=OutboxReplay(repo,registry,owner="webhook-e2e-replay");replay.register_subscribers()
 assert replay.replay_one(event_id=eid)
 op=service.claim_webhook_operation("webhook-e2e-worker")
 asyncio.run(service.process_webhook_operation(op,"webhook-e2e-worker"))
 saved=service.read_webhook_operation(eid)
 assert saved["state"]=="DONE" and provider.calls==1
 assert not hasattr(provider,"repository")


def test_http_500_delivery_reconciliation_persists_evidence(repo):
 eid,_=materialize(repo)
 claim=repo.claim_webhook_operation("worker-http-500")
 assert claim["request_id"]
 repo.fail_webhook_operation(eid,"worker-http-500",claim["attempt_count"],claim["version"],
   Classification.UNKNOWN,"server outcome ambiguous",
   details={"request_id":claim["request_id"],"http_status":500,"retry_count":0})
 pending=repo.get_webhook_operation(eid)
 assert pending["state"]=="RECONCILIATION_REQUIRED" and pending["http_status"]==500
 saved=repo.resolve_webhook_reconciliation(eid,"operator",pending["version"],"delivered",
   {"request_id":pending["request_id"],"response_id":"evidence-response",
    "provider_status":"accepted","receipt":{"receipt_id":"evidence-receipt"},
    "reason":"synthetic provider status confirms delivery","source":"sandbox_sink_status"})
 assert saved["state"]=="DONE" and saved["http_status"]==500
 assert saved["reconciled_at"] and saved["reconciliation_evidence"]["receipt"]=={"receipt_id":"evidence-receipt"}
 assert repo.claim_webhook_operation("worker-after-reconciliation") is None


def test_orchestrator_checks_provider_status_only_for_unknown_operation(repo):
 eid,_=materialize(repo)
 claim=repo.claim_webhook_operation("worker-status-check")
 repo.fail_webhook_operation(eid,"worker-status-check",claim["attempt_count"],claim["version"],
   Classification.UNKNOWN,"ambiguous write",details={"request_id":claim["request_id"]})
 class Provider:
  provider="webhook"
  def __init__(self):self.calls=[]
  def send(self,task,key):
   raise AssertionError("status-only test must not dispatch a delivery")
  def reconcile(self,task):
   self.calls.append(task)
   return {"request_id":task["request_id"],"response_id":"status-response",
     "http_status":200,"provider_status":"accepted","receipt":{"receipt_id":"status-receipt"}}
 provider=Provider()
 from jarvis_return_worker.dispatcher import TransportDispatcher
 from jarvis_return_worker.orchestrator import Orchestrator
 dispatcher=TransportDispatcher();dispatcher.register("webhook",provider)
 service=Orchestrator(repo,dispatcher)
 result=service.check_webhook_status(eid)
 assert result["provider_status"]=="accepted" and result["receipt"]["receipt_id"]=="status-receipt"
 assert provider.calls==[{"method":"POST","path":"/events","correlation_id":repo.get_webhook_operation(eid)["correlation_id"],
   "request_id":claim["request_id"],"idempotency_key":"jarvis-webhook:"+eid}]
 assert repo.get_webhook_operation(eid)["state"]=="RECONCILIATION_REQUIRED"


def test_webhook_remaining_lease_budget_fenced_in_postgres(repo):
 eid,_=materialize(repo)
 claim=repo.claim_webhook_operation("worker-budget")
 assert claim is not None and claim["event_id"]==eid
 with repo.connection() as c:
  c.execute("""UPDATE jarvis_webhook_operations
    SET lease_until=clock_timestamp()+interval '10 seconds'
    WHERE event_id=%s""",(eid,))
 with pytest.raises(Conflict,match="insufficient Webhook lease remaining"):
  repo.verify_webhook_dispatch_lease(eid,"worker-budget",
    claim["attempt_count"],claim["version"],20)
 assert repo.verify_webhook_dispatch_lease(eid,"worker-budget",
    claim["attempt_count"],claim["version"],0)
 with repo.connection() as c:
  c.execute("""UPDATE jarvis_webhook_operations
    SET lease_until=clock_timestamp()-interval '1 second'
    WHERE event_id=%s""",(eid,))
 with pytest.raises(Conflict,match="stale Webhook lease"):
  repo.verify_webhook_dispatch_lease(eid,"worker-budget",
    claim["attempt_count"],claim["version"],0)


def test_webhook_lease_fencing_rejects_stale_version_and_owner(repo):
 eid,_=materialize(repo)
 claim=repo.claim_webhook_operation("worker-original")
 assert claim is not None and claim["event_id"]==eid
 for owner,attempt,version in (
     ("worker-other",claim["attempt_count"],claim["version"]),
     ("worker-original",claim["attempt_count"]+1,claim["version"]),
     ("worker-original",claim["attempt_count"],claim["version"]+1),
 ):
  with pytest.raises(Conflict,match="stale Webhook lease"):
   repo.verify_webhook_dispatch_lease(eid,owner,attempt,version,0)
 assert repo.verify_webhook_dispatch_lease(
  eid,"worker-original",claim["attempt_count"],claim["version"],0)


def test_webhook_reconciled_operation_cannot_dispatch_with_old_claim(repo):
 eid,_=materialize(repo)
 claim=repo.claim_webhook_operation("worker-paused")
 assert claim is not None
 with repo.connection() as c:
  c.execute("""UPDATE jarvis_webhook_operations
    SET lease_until=clock_timestamp()-interval '1 second'
    WHERE event_id=%s""",(eid,))
 assert repo.claim_webhook_operation("worker-recovery") is None
 saved=repo.get_webhook_operation(eid)
 assert saved["state"]=="RECONCILIATION_REQUIRED"
 with pytest.raises(Conflict,match="stale Webhook lease"):
  repo.verify_webhook_dispatch_lease(
    eid,"worker-paused",claim["attempt_count"],claim["version"],0)


def test_webhook_budget_validation_does_not_mutate_claim(repo):
 eid,_=materialize(repo)
 claim=repo.claim_webhook_operation("worker-readonly")
 assert claim is not None
 before=repo.get_webhook_operation(eid)
 with pytest.raises(Conflict,match="invalid Webhook remaining lease budget"):
  repo.verify_webhook_dispatch_lease(
    eid,"worker-readonly",claim["attempt_count"],claim["version"],-1)
 with pytest.raises(Conflict,match="invalid Webhook remaining lease budget"):
  repo.verify_webhook_dispatch_lease(
    eid,"worker-readonly",claim["attempt_count"],claim["version"],
    repo.config.lease_seconds+1)
 after=repo.get_webhook_operation(eid)
 assert after["state"]==before["state"]=="PROCESSING"
 assert after["version"]==before["version"]
 assert after["attempt_count"]==before["attempt_count"]
 assert after["lease_owner"]==before["lease_owner"]


def test_webhook_expired_claim_cannot_record_success(repo):
 eid,_=materialize(repo)
 claim=repo.claim_webhook_operation("worker-expired")
 assert claim is not None
 with repo.connection() as c:
  c.execute("""UPDATE jarvis_webhook_operations
    SET lease_until=clock_timestamp()-interval '1 second'
    WHERE event_id=%s""",(eid,))
 with pytest.raises(Conflict,match="stale Webhook lease"):
  repo.finish_webhook_operation(
    eid,"worker-expired",claim["attempt_count"],claim["version"],receipt(eid))
 saved=repo.get_webhook_operation(eid)
 assert saved["state"]=="PROCESSING"
 assert saved["response_id"] is None
 assert repo.claim_webhook_operation("worker-recovery") is None
 assert repo.get_webhook_operation(eid)["state"]=="RECONCILIATION_REQUIRED"


def test_webhook_expired_claim_cannot_schedule_retry(repo):
 eid,_=materialize(repo)
 claim=repo.claim_webhook_operation("worker-expired-failure")
 assert claim is not None
 with repo.connection() as c:
  c.execute("""UPDATE jarvis_webhook_operations
    SET lease_until=clock_timestamp()-interval '1 second'
    WHERE event_id=%s""",(eid,))
 with pytest.raises(Conflict,match="stale Webhook lease"):
  repo.fail_webhook_operation(
    eid,"worker-expired-failure",claim["attempt_count"],claim["version"],
    Classification.RETRYABLE,"timeout")
 saved=repo.get_webhook_operation(eid)
 assert saved["state"]=="PROCESSING"
 assert saved["attempt_count"]==claim["attempt_count"]
 assert repo.claim_webhook_operation("worker-recovery") is None
 assert repo.get_webhook_operation(eid)["state"]=="RECONCILIATION_REQUIRED"


def test_webhook_reconciliation_blocks_reclaim_after_stale_failure(repo):
 eid,_=materialize(repo)
 claim=repo.claim_webhook_operation("worker-stale")
 assert claim is not None
 with repo.connection() as c:
  c.execute("""UPDATE jarvis_webhook_operations
    SET lease_until=clock_timestamp()-interval '1 second'
    WHERE event_id=%s""",(eid,))
 assert repo.claim_webhook_operation("worker-recovery") is None
 with pytest.raises(Conflict,match="stale Webhook lease"):
  repo.fail_webhook_operation(
    eid,"worker-stale",claim["attempt_count"],claim["version"],
    Classification.RETRYABLE,"retry_requested")
 assert repo.get_webhook_operation(eid)["state"]=="RECONCILIATION_REQUIRED"
 assert repo.claim_webhook_operation("worker-retry") is None


def test_webhook_reconciliation_blocks_stale_success_receipt(repo):
 eid,_=materialize(repo)
 claim=repo.claim_webhook_operation("worker-delayed-success")
 assert claim is not None
 with repo.connection() as c:
  c.execute("""UPDATE jarvis_webhook_operations
    SET lease_until=clock_timestamp()-interval '1 second'
    WHERE event_id=%s""",(eid,))
 assert repo.claim_webhook_operation("worker-recovery") is None
 assert repo.get_webhook_operation(eid)["state"]=="RECONCILIATION_REQUIRED"
 with pytest.raises(Conflict,match="stale Webhook lease"):
  repo.finish_webhook_operation(
    eid,"worker-delayed-success",claim["attempt_count"],claim["version"],receipt(eid))
 saved=repo.get_webhook_operation(eid)
 assert saved["state"]=="RECONCILIATION_REQUIRED"
 assert saved["response_id"] is None
 assert repo.claim_webhook_operation("worker-another") is None


def test_webhook_reconciliation_rejects_unverified_delivery(repo):
 eid,_=materialize(repo)
 claim=repo.claim_webhook_operation("worker-unknown")
 assert claim is not None
 repo.fail_webhook_operation(
  eid,"worker-unknown",claim["attempt_count"],claim["version"],
  Classification.UNKNOWN,"network_outcome_unknown")
 saved=repo.get_webhook_operation(eid)
 assert saved["state"]=="RECONCILIATION_REQUIRED"
 with pytest.raises(Conflict,match="delivery evidence required"):
  repo.resolve_webhook_reconciliation(
    eid,"operator",saved["version"],"delivered",{})
 assert repo.get_webhook_operation(eid)["state"]=="RECONCILIATION_REQUIRED"
 assert repo.claim_webhook_operation("worker-retry") is None


def test_webhook_reconciliation_requires_current_version(repo):
 eid,_=materialize(repo)
 claim=repo.claim_webhook_operation("worker-unknown-version")
 assert claim is not None
 repo.fail_webhook_operation(
  eid,"worker-unknown-version",claim["attempt_count"],claim["version"],
  Classification.UNKNOWN,"network_outcome_unknown")
 saved=repo.get_webhook_operation(eid)
 assert saved["state"]=="RECONCILIATION_REQUIRED"
 with pytest.raises(Conflict):
  repo.resolve_webhook_reconciliation(
    eid,"operator",claim["version"],"delivered",
    {"response_id":"synthetic-response"})
 unchanged=repo.get_webhook_operation(eid)
 assert unchanged["state"]=="RECONCILIATION_REQUIRED"
 assert unchanged["version"]==saved["version"]
 assert unchanged["response_id"] is None


def test_webhook_reconciliation_rejects_unauthenticated_operator(repo):
 eid,_=materialize(repo)
 claim=repo.claim_webhook_operation("worker-operator-check")
 assert claim is not None
 repo.fail_webhook_operation(
  eid,"worker-operator-check",claim["attempt_count"],claim["version"],
  Classification.UNKNOWN,"network_outcome_unknown")
 saved=repo.get_webhook_operation(eid)
 assert saved["state"]=="RECONCILIATION_REQUIRED"
 with pytest.raises(Conflict,match="authenticated operator"):
  repo.resolve_webhook_reconciliation(
    eid,"",saved["version"],"delivered",{"response_id":"synthetic-response"})
 unchanged=repo.get_webhook_operation(eid)
 assert unchanged["state"]=="RECONCILIATION_REQUIRED"
 assert unchanged["version"]==saved["version"]


def test_webhook_reconciliation_rejects_invalid_outcome(repo):
 eid,_=materialize(repo)
 claim=repo.claim_webhook_operation("worker-invalid-outcome")
 assert claim is not None
 repo.fail_webhook_operation(
  eid,"worker-invalid-outcome",claim["attempt_count"],claim["version"],
  Classification.UNKNOWN,"network_outcome_unknown")
 saved=repo.get_webhook_operation(eid)
 assert saved["state"]=="RECONCILIATION_REQUIRED"
 with pytest.raises(Conflict,match="valid outcome required"):
  repo.resolve_webhook_reconciliation(
    eid,"operator",saved["version"],"retry",{"response_id":"synthetic-response"})
 unchanged=repo.get_webhook_operation(eid)
 assert unchanged["state"]=="RECONCILIATION_REQUIRED"
 assert unchanged["version"]==saved["version"]
 assert repo.claim_webhook_operation("worker-retry") is None


def test_webhook_reconciliation_rejects_delivery_without_receipt_or_response_id(repo):
 eid,_=materialize(repo)
 claim=repo.claim_webhook_operation("worker-no-evidence")
 assert claim is not None
 repo.fail_webhook_operation(
  eid,"worker-no-evidence",claim["attempt_count"],claim["version"],
  Classification.UNKNOWN,"network_outcome_unknown")
 saved=repo.get_webhook_operation(eid)
 for evidence in (None,{},{"response_id":""},{"receipt":None}):
  with pytest.raises(Conflict,match="delivery evidence required"):
   repo.resolve_webhook_reconciliation(
    eid,"operator",saved["version"],"delivered",evidence)
 unchanged=repo.get_webhook_operation(eid)
 assert unchanged["state"]=="RECONCILIATION_REQUIRED"
 assert unchanged["version"]==saved["version"]


def test_webhook_unknown_outcome_cannot_be_completed_by_stale_worker(repo):
 eid,_=materialize(repo)
 claim=repo.claim_webhook_operation("worker-unknown-late")
 assert claim is not None
 repo.fail_webhook_operation(
  eid,"worker-unknown-late",claim["attempt_count"],claim["version"],
  Classification.UNKNOWN,"network_outcome_unknown")
 saved=repo.get_webhook_operation(eid)
 assert saved["state"]=="RECONCILIATION_REQUIRED"
 with pytest.raises(Conflict,match="stale Webhook lease"):
  repo.finish_webhook_operation(
    eid,"worker-unknown-late",claim["attempt_count"],claim["version"],receipt(eid))
 unchanged=repo.get_webhook_operation(eid)
 assert unchanged["state"]=="RECONCILIATION_REQUIRED"
 assert unchanged["response_id"] is None


def test_webhook_reconciliation_rejects_whitespace_and_malformed_evidence(repo):
 eid,_=materialize(repo)
 claim=repo.claim_webhook_operation("worker-invalid-evidence")
 assert claim is not None
 repo.fail_webhook_operation(
  eid,"worker-invalid-evidence",claim["attempt_count"],claim["version"],
  Classification.UNKNOWN,"network_outcome_unknown")
 saved=repo.get_webhook_operation(eid)
 for actor,evidence in (
     ("   ",{"response_id":"provider-id"}),
     ("operator",{"response_id":"   "}),
     ("operator",["not-a-mapping"]),
 ):
  with pytest.raises(Conflict):
   repo.resolve_webhook_reconciliation(
    eid,actor,saved["version"],"delivered",evidence)
 unchanged=repo.get_webhook_operation(eid)
 assert unchanged["state"]=="RECONCILIATION_REQUIRED"
 assert unchanged["version"]==saved["version"]


def test_webhook_reconciliation_with_response_id_persists_durable_receipt(repo):
 eid,_=materialize(repo)
 claim=repo.claim_webhook_operation("worker-receipt")
 assert claim is not None
 repo.fail_webhook_operation(
  eid,"worker-receipt",claim["attempt_count"],claim["version"],
  Classification.UNKNOWN,"network_outcome_unknown")
 pending=repo.get_webhook_operation(eid)
 assert pending["state"]=="RECONCILIATION_REQUIRED"
 saved=repo.resolve_webhook_reconciliation(
  eid,"operator",pending["version"],"delivered",
  {"response_id":"synthetic-provider-response","source":"isolated-ci"})
 assert saved["state"]=="DONE"
 assert saved["response_id"]=="synthetic-provider-response"
 assert saved["receipt"]["response_id"]=="synthetic-provider-response"
 assert saved["version"]==pending["version"]+1
 assert repo.claim_webhook_operation("worker-retry") is None


def test_webhook_reconciliation_not_delivered_closes_without_retry(repo):
 eid,_=materialize(repo)
 claim=repo.claim_webhook_operation("worker-not-delivered")
 assert claim is not None
 repo.fail_webhook_operation(
  eid,"worker-not-delivered",claim["attempt_count"],claim["version"],
  Classification.UNKNOWN,"network_outcome_unknown")
 pending=repo.get_webhook_operation(eid)
 assert pending["state"]=="RECONCILIATION_REQUIRED"
 saved=repo.resolve_webhook_reconciliation(
  eid,"operator",pending["version"],"not_delivered",
  {"reason":"verified-not-delivered","source":"isolated-ci"})
 assert saved["state"]=="FAILED"
 assert saved["version"]==pending["version"]+1
 assert repo.claim_webhook_operation("worker-retry") is None


def test_webhook_reconciliation_cannot_be_applied_twice(repo):
 eid,_=materialize(repo)
 claim=repo.claim_webhook_operation("worker-double-reconcile")
 assert claim is not None
 repo.fail_webhook_operation(
  eid,"worker-double-reconcile",claim["attempt_count"],claim["version"],
  Classification.UNKNOWN,"network_outcome_unknown")
 pending=repo.get_webhook_operation(eid)
 first=repo.resolve_webhook_reconciliation(
  eid,"operator",pending["version"],"delivered",
  {"response_id":"synthetic-provider-response"})
 assert first["state"]=="DONE"
 with pytest.raises(Conflict,match="not awaiting reconciliation"):
  repo.resolve_webhook_reconciliation(
   eid,"operator",pending["version"],"not_delivered",
   {"reason":"conflicting-decision"})
 unchanged=repo.get_webhook_operation(eid)
 assert unchanged["state"]=="DONE"
 assert unchanged["version"]==first["version"]
 assert unchanged["response_id"]=="synthetic-provider-response"


def test_stale_webhook_claim_is_fenced_before_dispatch(repo):
 eid,_=materialize(repo)
 claim=repo.claim_webhook_operation('worker-a')
 assert claim['event_id']==eid
 repo.verify_webhook_dispatch_lease(eid,'worker-a',claim['attempt_count'],claim['version'],0)
 with repo.connection() as c:
  c.execute("UPDATE jarvis_webhook_operations SET lease_until=clock_timestamp()-interval '1 second' WHERE event_id=%s",(eid,))
 with pytest.raises(Conflict):
  repo.verify_webhook_dispatch_lease(eid,'worker-a',claim['attempt_count'],claim['version'],0)


def test_webhook_dispatch_rejects_budget_larger_than_lease(repo):
 eid,_=materialize(repo)
 claim=repo.claim_webhook_operation('budget-worker')
 with pytest.raises(Conflict,match='invalid Webhook remaining lease budget'):
  repo.verify_webhook_dispatch_lease(eid,'budget-worker',claim['attempt_count'],claim['version'],repo.config.lease_seconds+1)


def test_concurrent_same_response_id_preserves_duplicate_receipt(repo):
 from concurrent.futures import ThreadPoolExecutor
 from threading import Barrier
 eid1,_=materialize(repo)
 eid2,_=materialize(repo)
 first=repo.claim_webhook_operation('race-worker-a')
 second=repo.claim_webhook_operation('race-worker-b')
 assert {first['event_id'],second['event_id']}=={eid1,eid2}
 barrier=Barrier(2)
 def complete(claim,owner):
  result=receipt(claim['event_id'])
  result['response_id']='shared-race-'+str(uuid.uuid5(uuid.NAMESPACE_URL,str(eid1)))
  barrier.wait(timeout=10)
  return repo.finish_webhook_operation(claim['event_id'],owner,claim['attempt_count'],claim['version'],result)
 with ThreadPoolExecutor(max_workers=2) as pool:
  a=pool.submit(complete,first,'race-worker-a')
  b=pool.submit(complete,second,'race-worker-b')
  states=[a.result(timeout=20),b.result(timeout=20)]
 assert sorted(x['state'] for x in states)==['DONE','RECONCILIATION_REQUIRED']
 duplicate=next(x for x in states if x['state']=='RECONCILIATION_REQUIRED')
 assert duplicate['reconciliation_reason']=='duplicate_provider_response_id'
 assert duplicate['receipt']['duplicate_response_id']==next(x for x in states if x['state']=='DONE')['response_id']


def test_webhook_budget_rejection_never_calls_provider(repo,monkeypatch):
 import asyncio
 from jarvis_return_worker.dispatcher import TransportDispatcher
 from jarvis_return_worker.orchestrator import Orchestrator
 eid,_=materialize(repo)
 claim=repo.claim_webhook_operation('budget-blocked-worker')
 class SpyProvider:
  provider='webhook'
  calls=0
  def send(self,*args):
   self.calls+=1
   raise AssertionError('provider must not be called without sufficient lease')
 spy=SpyProvider()
 dispatcher=TransportDispatcher()
 dispatcher.register('webhook',spy)
 monkeypatch.setenv('JARVIS_WEBHOOK_BASE_URL','https://synthetic.invalid')
 monkeypatch.setenv('JARVIS_WEBHOOK_TIMEOUT_SECONDS','8')
 monkeypatch.setenv('JARVIS_WEBHOOK_MAX_RETRIES','2')
 asyncio.run(Orchestrator(repo,dispatcher).process_webhook_operation(claim,'budget-blocked-worker'))
 assert spy.calls==0
 assert repo.get_webhook_operation(eid)['state']=='RECONCILIATION_REQUIRED'
