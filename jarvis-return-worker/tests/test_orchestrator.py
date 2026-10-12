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


def test_event_observer_failure_does_not_repeat_provider_send():
    repo=Repo()
    provider=Provider("alpha")
    bus=EventBus([lambda event: (_ for _ in ()).throw(RuntimeError("observer failed"))])
    core=Orchestrator(repo,dispatcher(("alpha",provider,True)),event_bus=bus)
    run(core)
    assert len(provider.calls)==1
    assert repo.calls[-2:]==["receipt","finish"]


def test_event_bus_deduplicates_repeated_notification_key():
    bus=EventBus()
    event=OrchestratorEvent("TaskApproved","task-1","corr-1",None,"READY","APPROVED")
    assert bus.publish(event) is True
    assert bus.publish(event) is False


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
