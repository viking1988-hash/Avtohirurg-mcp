"""Single application service for queue-to-provider delivery lifecycle."""
import asyncio
import logging
import time
from .events import EventBus, OrchestratorEvent
from .policy import PolicyEngine
from .queue import Conflict
from .transport import Classification, DeliveryError


class Orchestrator:
    def __init__(self, repository, dispatcher, policy=None, event_bus=None):
        self.repository=repository
        self.dispatcher=dispatcher
        self.policy=policy or PolicyEngine()
        self.event_bus=event_bus or EventBus()

    def enqueue(self, *args, **kwargs): return self.repository.enqueue(*args, **kwargs)
    def list_tasks(self, limit=100): return self.repository.list(limit)
    def claim(self, owner): return self.repository.claim(owner)
    def recover(self, owner): return self.repository.recover(owner)
    def cancel(self, task_id, actor): return self.repository.cancel(task_id, actor)
    def requeue(self, task_id, actor): return self.repository.requeue(task_id, actor)

    def provider_receipt(self, name, message_id):
        from .metrics import provider_call
        provider=self.dispatcher.dispatch(name)
        provider_call(name,"get_receipt")
        return provider.get_receipt(message_id)

    def provider_status(self, name, message_id):
        from .metrics import provider_call
        provider=self.dispatcher.dispatch(name)
        provider_call(name,"check_status")
        return provider.check_status(message_id)

    def provider_reconcile(self, name, task):
        from .metrics import provider_call
        provider=self.dispatcher.dispatch(name)
        provider_call(name,"reconcile")
        return provider.reconcile(task)

    def provider_cancel(self, name, message_id):
        from .metrics import provider_call
        provider=self.dispatcher.dispatch(name)
        provider_call(name,"cancel")
        return provider.cancel(message_id)

    def approve(self, task_id, fingerprint, expires_at, actor):
        result=self.repository.approve(task_id,fingerprint,expires_at,actor)
        self._publish(OrchestratorEvent("TaskApproved",task_id,result.get("correlation_id"),None,"READY","APPROVED",actor))
        return result

    def read_task(self, task_id, actor):
        from .reconciliation import read_task
        return read_task(self.repository,task_id,actor)

    def read_events(self, task_id, actor):
        from .reconciliation import read_events
        return read_events(self.repository,task_id,actor)

    def read_delivery(self, task_id, actor):
        from .reconciliation import read_delivery
        return read_delivery(self.repository,task_id,actor)

    def reconcile(self, task_id, actor, action, expected_version, **kwargs):
        from .reconciliation import mutate
        result=mutate(self.repository,task_id,actor,action,expected_version,**kwargs)
        self._publish(OrchestratorEvent("ReconciliationCompleted",task_id,result.get("correlation_id"),
            result.get("provider"),"RECONCILIATION_REQUIRED",result.get("state"),kwargs.get("reason")))
        if result.get("state") in ("DONE","DELIVERED_CONFIRMED"):
            self._publish(OrchestratorEvent("TaskFinished",task_id,result.get("correlation_id"),
                result.get("provider"),"RECONCILIATION_REQUIRED",result.get("state")))
        return result

    def _publish(self, event):
        try:
            self.event_bus.publish(event)
        except Exception:
            logging.getLogger(__name__).exception("orchestrator event observer failed")

    async def process(self, task, owner):
        started=time.monotonic()
        from .metrics import observe_orchestrator
        provider_name=task.get("provider") or self._provider_for(task)
        try:
            approval=self.policy.approval(task)
        except Exception:
            await asyncio.to_thread(self.repository.fail,task["task_id"],owner,task["attempt_count"])
            observe_orchestrator("policy_error",time.monotonic()-started,"unknown")
            return
        if approval.action != "send":
            await asyncio.to_thread(self.repository.fail,task["task_id"],owner,task["attempt_count"])
            return
        try:
            await asyncio.to_thread(self.repository.heartbeat,task["task_id"],owner,task["attempt_count"])
            await asyncio.to_thread(self.repository.mark_delivery_started,task["task_id"],owner,task["attempt_count"],provider_name)
            task["delivery_started_at"]=True
            self._publish(OrchestratorEvent("DeliveryStarted",task["task_id"],task.get("correlation_id"),provider_name,"PROCESSING","PROCESSING"))
            try:
                provider=self.dispatcher.dispatch(provider_name)
            except Exception as exc:
                from .metrics import dispatcher_failure
                dispatcher_failure(str(exc))
                raise
            result=await asyncio.to_thread(self._send,provider,task)
            receipt=result.get("receipt_id") or result.get("receipt") or result.get("message_id")
            message_id=result.get("message_id")
            if not receipt:
                raise DeliveryError(Classification.UNKNOWN,"provider_receipt_missing")
            await asyncio.to_thread(self.repository.save_delivery_receipt,task["task_id"],owner,
                task["attempt_count"],receipt,message_id,result.get("chat_id"),result.get("response"),provider_name)
            task["delivery_receipt"]=receipt
            task["provider_message_id"]=message_id
            await asyncio.to_thread(self.repository.finish,task["task_id"],owner,task["attempt_count"],receipt)
            self._publish(OrchestratorEvent("DeliverySucceeded",task["task_id"],task.get("correlation_id"),provider_name,"PROCESSING","DONE"))
            self._publish(OrchestratorEvent("TaskFinished",task["task_id"],task.get("correlation_id"),provider_name,"PROCESSING","DONE"))
            observe_orchestrator("success",time.monotonic()-started,provider_name)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            decision=self.policy.delivery_failure(exc,task)
            try:
                if decision.action=="retry" and self.policy.may_resend(task,Classification.RETRYABLE):
                    await asyncio.to_thread(self.repository.retry_confirmed_rejection,task["task_id"],owner,task["attempt_count"],getattr(exc,"retry_after",None))
                    from .metrics import policy_retry
                    policy_retry()
                elif decision.action=="fail":
                    if isinstance(exc,DeliveryError) and exc.classification==Classification.PERMANENT:
                        await asyncio.to_thread(self.repository.fail_permanent_rejection,task["task_id"],owner,task["attempt_count"],exc.code)
                    else:
                        await asyncio.to_thread(self.repository.fail,task["task_id"],owner,task["attempt_count"])
                    self._publish(OrchestratorEvent("DeliveryFailed",task["task_id"],task.get("correlation_id"),provider_name,"PROCESSING","FAILED",decision.reason))
                elif decision.action=="complete":
                    pass
                else:
                    await asyncio.to_thread(self.repository.fail,task["task_id"],owner,task["attempt_count"])
                    from .metrics import policy_reconcile
                    policy_reconcile()
                    self._publish(OrchestratorEvent("DeliveryUnknown",task["task_id"],task.get("correlation_id"),provider_name,"PROCESSING","RECONCILIATION_REQUIRED",decision.reason))
                    self._publish(OrchestratorEvent("ReconciliationRequired",task["task_id"],task.get("correlation_id"),provider_name,"PROCESSING","RECONCILIATION_REQUIRED",decision.reason))
                observe_orchestrator("failure",time.monotonic()-started,provider_name)
            except Conflict:
                logging.getLogger(__name__).warning("orchestrator state transition fenced")
        return

    def _provider_for(self, task):
        if task.get("destination_key","").startswith("sandbox:telegram:"):
            return "telegram"
        return "fake"

    def _send(self, provider, task):
        from .metrics import provider_call,transport_dispatch,dispatcher_failure
        name=getattr(provider,"provider","unknown")
        try:
            transport_dispatch(name)
            provider_call(name,"send")
            key=f"{task['task_id']}:{task.get('action_fingerprint','')}"
            return provider.send(task,key)
        except Exception as exc:
            if exc.__class__.__name__=="DispatcherError":
                dispatcher_failure(str(exc))
            raise
