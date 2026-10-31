import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from jarvis_return_worker.approval import action_fingerprint
from jarvis_return_worker.dispatcher import DispatcherError, TransportDispatcher
from jarvis_return_worker.events import EventBus, OrchestratorEvent
from jarvis_return_worker.orchestrator import Orchestrator
from jarvis_return_worker.policy import PolicyEngine
from jarvis_return_worker.transport import Classification, DeliveryError, FakeTransport


NOW=datetime.now(timezone.utc)


def approved_task(provider="alpha"):
    fp=action_fingerprint("task-1","sandbox:test","payload")
    return {
        "task_id":"task-1","destination_key":"sandbox:test","payload":"payload",
        "correlation_id":"corr-1","provider":provider,"approved_by":"operator",
        "approved_at":NOW,"approval_expires_at":NOW+timedelta(minutes=5),
        "approved_fingerprint":fp,"action_fingerprint":fp,"attempt_count":1,
    }


class Repo:
    def __init__(self):
        self.calls=[]
        self.config=type("Config",(),{"worker_id":"test-worker"})()
    def heartbeat(self,*args): self.calls.append("heartbeat")
    def mark_delivery_started(self,*args): self.calls.append("mark")
    def verify_dispatch_lease(self,*args): return True
    def save_delivery_receipt(self,*args): self.calls.append("receipt")
    def finish(self,*args): self.calls.append("finish")
    def fail(self,*args): self.calls.append("fail")
    def retry_confirmed_rejection(self,*args): self.calls.append("retry")
    def fail_permanent_rejection(self,*args): self.calls.append("permanent")
    def recover(self,*args): self.calls.append("recover"); return 0
    def claim(self,*args): self.calls.append("claim"); return None
    def cancel(self,*args): self.calls.append("cancel"); return {"state":"CANCELLED"}
    def requeue(self,*args): self.calls.append("requeue"); return {"state":"READY"}


class Provider:
    def __init__(self,name,result=None,error=None):
        self.provider=name
        self.result=result or {"receipt_id":"receipt:"+name,"message_id":"msg:"+name}
        self.error=error
        self.calls=[]
    def send(self,task,key):
        self.calls.append((task["task_id"],key))
        if self.error: raise self.error
        return self.result


def dispatcher(*providers):
    value=TransportDispatcher()
    for name,provider,enabled in providers:
        value.register(name,provider,enabled=enabled)
    return value


def run(orchestrator,task=None):
    return asyncio.run(orchestrator.process(task or approved_task(),"test-worker"))


def test_dispatcher_registers_and_switches_providers_without_core_changes():
    repo=Repo()
    a,b=Provider("alpha"),Provider("beta")
    core=Orchestrator(repo,dispatcher(("alpha",a,True),("beta",b,True)))
    run(core,approved_task("alpha"))
    run(core,approved_task("beta"))
    assert len(a.calls)==len(b.calls)==1
    assert repo.calls.count("finish")==2


@pytest.mark.parametrize("name",[None,"missing"])
def test_dispatcher_rejects_unknown_provider(name):
    dispatcher_value=dispatcher(("fake",FakeTransport(),True))
    with pytest.raises(DispatcherError):
        dispatcher_value.dispatch(name or "missing")


def test_dispatcher_fails_closed_for_disabled_transport():
    d=dispatcher(("email",Provider("email"),False))
    with pytest.raises(DispatcherError,match="provider_disabled"):
        d.dispatch("email")


def test_policy_only_allows_retry_after_definitive_provider_rejection():
    policy=PolicyEngine()
    task=approved_task()
    assert policy.delivery_failure(DeliveryError(Classification.RETRYABLE,"rejected"),task).action=="retry"
    assert policy.may_resend(task,Classification.RETRYABLE)
    assert policy.delivery_failure(DeliveryError(Classification.UNKNOWN,"timeout"),task).action=="reconcile"
    assert not policy.may_resend(task,Classification.UNKNOWN)
    assert not policy.may_resend({**task,"delivery_receipt":"already-sent"},Classification.RETRYABLE)


def test_unknown_delivery_never_retries_and_enters_recovery_path():
    repo=Repo()
    provider=Provider("alpha",error=DeliveryError(Classification.UNKNOWN,"timeout"))
    run(Orchestrator(repo,dispatcher(("alpha",provider,True))))
    assert len(provider.calls)==1
    assert repo.calls==["heartbeat","mark","fail"]


def test_explicit_retryable_rejection_is_decided_by_policy():
    repo=Repo()
    provider=Provider("alpha",error=DeliveryError(Classification.RETRYABLE,"rate_limited",3))
    run(Orchestrator(repo,dispatcher(("alpha",provider,True))))
    assert len(provider.calls)==1
    assert repo.calls==["heartbeat","mark","retry"]


def test_dispatcher_failure_after_send_marker_requires_reconciliation():
    class BrokenDispatcher:
        def dispatch(self,name): raise DispatcherError("provider_disabled")
    repo=Repo()
    run(Orchestrator(repo,BrokenDispatcher()))
    assert repo.calls==["heartbeat","mark","fail"]


def test_outbox_registry_does_not_publish_volatile_copy_during_delivery():
    repo=Repo()
    provider=Provider("alpha")
    bus=EventBus()
    core=Orchestrator(repo,dispatcher(("alpha",provider,True)),event_bus=bus)
    run(core)
    assert len(provider.calls)==1
    assert repo.calls[-2:]==["receipt","finish"]


def test_event_bus_rejects_nontransactional_publish():
    bus=EventBus()
    event=OrchestratorEvent("TaskApproved","task-1","corr-1",None,"READY","APPROVED")
    with pytest.raises(RuntimeError,match="business transaction"):
        bus.publish(event)


def test_fake_provider_has_no_queue_dependency_and_is_repeatable():
    fake=FakeTransport()
    task=approved_task("fake")
    key=task["task_id"]+":"+task["action_fingerprint"]
    first=fake.send(task,key)
    second=fake.send(task,key)
    assert first==second
    assert "repository" not in vars(fake)


def test_orchestrator_owns_queue_and_cancellation_facades():
    repo=Repo()
    core=Orchestrator(repo,dispatcher(("fake",FakeTransport(),True)))
    assert core.claim("worker") is None
    assert core.cancel("task-1","operator")["state"]=="CANCELLED"
    assert core.requeue("task-1","operator")["state"]=="READY"
    assert repo.calls==["claim","cancel","requeue"]


def test_orchestrator_metrics_are_exported():
    from jarvis_return_worker import metrics
    class Stats:
        def stats(self): return {}
    metrics.observe_orchestrator("test",0.01,"fake")
    metrics.transport_dispatch("fake")
    metrics.policy_retry()
    metrics.policy_reconcile()
    metrics.event_published("TaskFinished")
    metrics.dispatcher_failure("provider_disabled")
    metrics.provider_call("fake","send")
    output=metrics.render_metrics(Stats()).decode()
    for name in ("orchestrator_tasks_total","orchestrator_pipeline_seconds",
        "transport_dispatch_total","policy_retry_total","policy_reconcile_total",
        "event_bus_events_total","dispatcher_failures_total","provider_calls_total"):
        assert name in output


def test_policy_engine_error_fails_closed_before_provider_call():
    class BrokenPolicy:
        def approval(self,task): raise RuntimeError("policy unavailable")
    repo=Repo()
    provider=Provider("alpha")
    core=Orchestrator(repo,dispatcher(("alpha",provider,True)),policy=BrokenPolicy())
    run(core)
    assert provider.calls==[]
    assert repo.calls==["fail"]


def test_expired_dispatch_lease_never_calls_external_provider():
    from jarvis_return_worker.queue import Conflict
    class ExpiredRepo(Repo):
        def verify_dispatch_lease(self,*args):
            self.calls.append("fence")
            raise Conflict("lease expired at dispatch boundary")
    repo=ExpiredRepo()
    provider=Provider("alpha")
    run(Orchestrator(repo,dispatcher(("alpha",provider,True))))
    assert provider.calls==[]
    assert repo.calls==["heartbeat","mark","fence","fail"]


def test_misclassified_policy_retry_cannot_bypass_unknown_provider_error():
    from jarvis_return_worker.policy import PolicyDecision
    class UnsafePolicy(PolicyEngine):
        def delivery_failure(self,error,task):
            return PolicyDecision("retry","incorrect_retry_decision")
    repo=Repo()
    provider=Provider("alpha",error=DeliveryError(Classification.UNKNOWN,"ambiguous_timeout"))
    run(Orchestrator(repo,dispatcher(("alpha",provider,True)),policy=UnsafePolicy()))
    assert len(provider.calls)==1
    assert "retry" not in repo.calls
    assert repo.calls==["heartbeat","mark","fail"]


def test_lease_expires_after_fence_before_provider_call_is_not_atomic():
    """Document TOCTOU: a successful DB fence does not reserve a remote send."""
    class LeaseExpiresAfterFence(Repo):
        def __init__(self):
            super().__init__()
            self.expired=False
        def verify_dispatch_lease(self,*args):
            self.calls.append("fence")
            self.expired=True  # Simulated lease expiry after the successful check.
            return True
    class ObservingProvider(Provider):
        def send(self,task,key):
            assert repo.expired
            return super().send(task,key)
    repo=LeaseExpiresAfterFence()
    provider=ObservingProvider("alpha",error=DeliveryError(Classification.UNKNOWN,"lost_response"))
    run(Orchestrator(repo,dispatcher(("alpha",provider,True))))
    assert len(provider.calls)==1  # External effect may start after lease expiry.
    assert "retry" not in repo.calls
    assert repo.calls==["heartbeat","mark","fence","fail"]


def test_two_workers_shared_marker_blocks_second_external_send():
    """Two orchestrators share a synthetic durable marker; only one reaches provider."""
    from jarvis_return_worker.queue import Conflict
    import threading
    class SharedMarkerRepo(Repo):
        def __init__(self):
            super().__init__()
            self.lock=threading.Lock()
            self.started=False
        def mark_delivery_started(self,*args):
            with self.lock:
                if self.started:
                    raise Conflict("delivery marker already persisted")
                self.started=True
                self.calls.append("mark")
        def verify_dispatch_lease(self,*args):
            assert self.started
            return True
    repo=SharedMarkerRepo()
    provider=Provider("alpha",error=DeliveryError(Classification.UNKNOWN,"lost_response"))
    first=Orchestrator(repo,dispatcher(("alpha",provider,True)))
    second=Orchestrator(repo,dispatcher(("alpha",provider,True)))
    async def both():
        await asyncio.gather(first.process(approved_task(),"test-worker"),
                             second.process(approved_task(),"test-worker"))
    asyncio.run(both())
    assert len(provider.calls)==1
    assert repo.calls.count("mark")==1
    assert "retry" not in repo.calls


def test_stale_worker_receipt_and_failure_are_fenced_after_remote_send():
    """A stale sender cannot commit a receipt or overwrite reconciliation state."""
    from jarvis_return_worker.queue import Conflict
    class StaleRepo(Repo):
        def save_delivery_receipt(self,*args):
            self.calls.append("stale_receipt_rejected")
            raise Conflict("lease owner changed after remote send")
        def fail(self,*args):
            self.calls.append("stale_failure_rejected")
            raise Conflict("reconciliation owned by recovery worker")
    repo=StaleRepo()
    provider=Provider("alpha")
    run(Orchestrator(repo,dispatcher(("alpha",provider,True))))
    assert len(provider.calls)==1
    assert repo.calls==["heartbeat","mark","stale_receipt_rejected","stale_failure_rejected"]
    assert "finish" not in repo.calls
    assert "retry" not in repo.calls


def test_worker_cancelled_at_dispatch_fence_does_not_send_or_clear_marker():
    """Cancellation at the dispatch boundary must not erase uncertain-send evidence."""
    class CancelAtFenceRepo(Repo):
        def verify_dispatch_lease(self,*args):
            self.calls.append("fence")
            raise asyncio.CancelledError()
    repo=CancelAtFenceRepo()
    provider=Provider("alpha")
    with pytest.raises(asyncio.CancelledError):
        run(Orchestrator(repo,dispatcher(("alpha",provider,True))))
    assert provider.calls==[]
    assert repo.calls==["heartbeat","mark","fence"]
    assert "retry" not in repo.calls


@pytest.mark.parametrize("failure", [
    "accepted_then_response_lost",
    "timeout_after_possible_acceptance",
    "http_500_ambiguous",
    "worker_crash_during_send",
])
def test_telegram_p1_ambiguous_acceptance_never_auto_resends(failure):
    """Synthetic remote acceptance can precede loss of local acknowledgement.

    This tests only orchestration policy; it does not call Telegram or prove
    remote exactly-once semantics.
    """
    repo=Repo()
    class AcceptedButUnconfirmed(Provider):
        def __init__(self):
            super().__init__("alpha")
            self.synthetic_remote_effects=0
        def send(self,task,key):
            self.calls.append((task["task_id"],key))
            self.synthetic_remote_effects+=1
            if failure=="worker_crash_during_send":
                raise SystemExit("synthetic process interruption")
            raise DeliveryError(Classification.UNKNOWN,failure)
    provider=AcceptedButUnconfirmed()
    core=Orchestrator(repo,dispatcher(("alpha",provider,True)))
    if failure=="worker_crash_during_send":
        with pytest.raises(SystemExit,match="synthetic process interruption"):
            run(core)
    else:
        run(core)
    assert provider.synthetic_remote_effects==1
    assert len(provider.calls)==1
    assert repo.calls[:2]==["heartbeat","mark"]
    assert "receipt" not in repo.calls
    assert "finish" not in repo.calls
    assert "retry" not in repo.calls
    assert not core.policy.may_resend(
        {**approved_task(),"delivery_started_at":NOW},
        Classification.UNKNOWN)


def test_two_distinct_workers_after_successful_fences_can_both_reach_receiver():
    """Security regression evidence: lease fencing alone does not deduplicate remote effects."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    barrier=threading.Barrier(2,timeout=10)
    lock=threading.Lock()
    delivered=[]
    class RaceRepo(Repo):
        def mark_delivery_started(self,*args):
            self.calls.append("mark")
        def verify_dispatch_lease(self,*args):
            barrier.wait()
            return True
    class RaceProvider(Provider):
        def send(self,task,key):
            with lock:
                delivered.append((task["task_id"],key))
            raise DeliveryError(Classification.UNKNOWN,"response_lost_after_acceptance")

    repo=RaceRepo()
    provider=RaceProvider("alpha")
    def worker(owner):
        asyncio.run(Orchestrator(repo,dispatcher(("alpha",provider,True))).process(
            approved_task(),owner))
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(worker,("worker-a","worker-b")))
    assert len(delivered)==2, "Both sends are reachable without receiver dedup"
    assert len({key for _,key in delivered})==1, "Idempotency key must remain stable"
    assert "retry" not in repo.calls, "UNKNOWN must not trigger automatic resend"


def test_receiver_atomic_dedup_limits_two_fenced_workers_to_one_effect():
    """A receiver-side atomic idempotency record, not a lease fence, prevents duplicates."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    fence=threading.Barrier(2,timeout=10)
    lock=threading.Lock()
    accepted={}
    requests=[]
    class RaceRepo(Repo):
        def verify_dispatch_lease(self,*args):
            fence.wait()
            return True
    class DeduplicatingReceiver(Provider):
        def send(self,task,key):
            with lock:
                requests.append(key)
                if key not in accepted:
                    accepted[key]={"receipt_id":"receiver-receipt","message_id":"receiver-msg"}
                return dict(accepted[key])

    repo=RaceRepo()
    receiver=DeduplicatingReceiver("alpha")
    def worker(owner):
        asyncio.run(Orchestrator(repo,dispatcher(("alpha",receiver,True))).process(
            approved_task(),owner))
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(worker,("worker-a","worker-b")))
    assert len(requests)==2
    assert len(set(requests))==1
    assert len(accepted)==1
    assert repo.calls.count("retry")==0
