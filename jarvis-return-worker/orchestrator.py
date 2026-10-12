"""Single application service for queue-to-provider delivery lifecycle."""
import asyncio
import logging
import time
from .dispatcher import DispatcherError
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

    def claim(self, owner):
        return self.repository.claim(owner)

    def recover(self, owner):
        return self.repository.recover(owner)

    def cancel(self, task_id, actor):
        return self.repository.cancel(task_id, actor)

    def _publish(self, event):
        try:
            self.event_bus.publish(event)
        except Exception:
            # Durable queue audit is authoritative; observer faults never replay a send.
            logging.getLogger(__name__).exception("orchestrator event observer failed")

    async def process(self, task, owner):
        started=time.monotonic()
        provider_name=task.get("provider") or self._provider_for(task)
        if self.policy.approval(task).action != "send":
            await asyncio.to_thread(self.repository.fail,task["task_id"],owner,task["attempt_count"])
            return
        heartbeat=asyncio.create_task(asyncio.sleep(0))
        try:
            await asyncio.to_thread(self.repository.heartbeat,task["task_id"],owner,task["attempt_count"])
            await asyncio.to_thread(self.repository.mark_delivery_started,task["task_id"],owner,task["attempt_count"],provider_name)
            self._publish(OrchestratorEvent("DeliveryStarted",task["task_id"],task.get("correlation_id"),provider_name,"PROCESSING","PROCESSING"))
            provider=self.dispatcher.dispatch(provider_name)
            from .metrics import observe_orchestrator
            result=await asyncio.to_thread(self._send,provider,task)
            receipt=result.get("receipt_id") or result.get("receipt") or result.get("message_id")
            message_id=result.get("message_id")
            if not receipt:
                raise DeliveryError(Classification.UNKNOWN,"provider_receipt_missing")
            await asyncio.to_thread(self.repository.save_delivery_receipt,task["task_id"],owner,
                task["attempt_count"],receipt,message_id,result.get("chat_id"),result.get("response"),provider_name)
            task["delivery_receipt"]=receipt
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
                    retry_after=getattr(exc,"retry_after",None)
                    await asyncio.to_thread(self.repository.retry_confirmed_rejection,task["task_id"],owner,task["attempt_count"],retry_after)
                    from .metrics import policy_retry
                    policy_retry()
                elif decision.action=="fail":
                    if isinstance(exc,DeliveryError) and exc.classification==Classification.PERMANENT:
                        await asyncio.to_thread(self.repository.fail_permanent_rejection,task["task_id"],owner,task["attempt_count"],exc.code)
                    else:
                        await asyncio.to_thread(self.repository.fail,task["task_id"],owner,task["attempt_count"])
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
        finally:
            heartbeat.cancel()
        return

    def _provider_for(self, task):
        destination=task.get("destination_key","")
        if destination.startswith("sandbox:telegram:"):
            return "telegram"
        configured=getattr(self.repository.config,"transport",None)
        return configured or "fake"

    def _send(self, provider, task):
        from .metrics import provider_call,transport_dispatch
        provider_call(getattr(provider,"provider","unknown"),"send")
        transport_dispatch(getattr(provider,"provider","unknown"))
        key=f"{task['task_id']}:{task.get('action_fingerprint','')}"
        return provider.send(task,key)
