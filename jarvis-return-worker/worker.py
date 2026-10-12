import asyncio
import json
import logging
import time
from .approval import valid_approval
from .lease import LeaseManager
from .queue import Conflict
from .transport import Classification, DeliveryError


def _log(task, owner, provider, state, started, provider_message_id=None):
    # Structured JSON fields only. Transport exceptions, URLs, tokens and message text are excluded.
    record={'task_id':task.get('task_id'),'correlation_id':task.get('correlation_id'),
            'attempt':task.get('attempt_count'),'lease_owner':owner,'provider':provider,
            'provider_message_id':provider_message_id,'state':state,
            'duration_ms':round((time.monotonic()-started)*1000,2)}
    logging.getLogger(__name__).info(json.dumps(record,separators=(',',':'),sort_keys=True))


class Worker:
    def __init__(self, repository, transport, stop):
        self.repository, self.transport, self.stop = repository, transport, stop
        self.owner = repository.config.worker_id
        self.leases = LeaseManager(repository,self.owner)
        self.last_tick = 0.0

    async def process(self, task):
        started=time.monotonic()
        provider=getattr(self.transport,'provider','fake')
        if not valid_approval(task):
            await asyncio.to_thread(self.repository.fail,task['task_id'],self.owner,task['attempt_count'])
            _log(task,self.owner,provider,'PERMANENT',started)
            return
        heartbeat = asyncio.create_task(self.leases.heartbeat(task))
        async def effect():
            await asyncio.to_thread(self.repository.heartbeat,task['task_id'],self.owner,task['attempt_count'])
            await asyncio.to_thread(self.repository.mark_delivery_started,task['task_id'],self.owner,task['attempt_count'])
            result = await asyncio.to_thread(self.transport.deliver,task)
            if isinstance(result,dict):
                receipt=result.get('receipt_id') or result.get('receipt') or result.get('message_id')
                message_id=result.get('message_id')
                chat_id=result.get('chat_id')
                response=result.get('response')
            else:
                receipt=str(result) if result is not None else None
                message_id=chat_id=response=None
            if not receipt:
                raise DeliveryError(Classification.UNKNOWN,'transport_receipt_missing')
            await asyncio.to_thread(self.repository.save_delivery_receipt,task['task_id'],self.owner,
                task['attempt_count'],receipt,message_id,chat_id,response)
            await asyncio.to_thread(self.repository.finish,task['task_id'],self.owner,task['attempt_count'],receipt)
            return message_id
        delivery = asyncio.create_task(effect())
        state='SUCCESS'
        message_id=None
        try:
            done,_=await asyncio.wait([heartbeat,delivery],return_when=asyncio.FIRST_COMPLETED)
            if heartbeat in done:
                await heartbeat
                raise Conflict('heartbeat stopped')
            message_id=await delivery
        except asyncio.CancelledError:
            raise
        except DeliveryError as exc:
            state=exc.classification.value
            try:
                if exc.classification == Classification.RETRYABLE:
                    await asyncio.to_thread(self.repository.retry_confirmed_rejection,task['task_id'],
                        self.owner,task['attempt_count'],exc.retry_after)
                elif exc.classification == Classification.PERMANENT:
                    await asyncio.to_thread(self.repository.fail_permanent_rejection,task['task_id'],
                        self.owner,task['attempt_count'],exc.code)
                else:
                    await asyncio.to_thread(self.repository.fail,task['task_id'],self.owner,task['attempt_count'])
            except Exception:
                pass
        except Exception:
            # Once the marker exists, unclassified failures are UNKNOWN. fail() will
            # choose RECONCILIATION_REQUIRED unless a durable receipt already exists.
            state='UNKNOWN'
            try:
                await asyncio.to_thread(self.repository.fail,task['task_id'],self.owner,task['attempt_count'])
            except Exception:
                pass
        finally:
            heartbeat.cancel()
            delivery.cancel()
            await asyncio.gather(heartbeat,delivery,return_exceptions=True)
            _log(task,self.owner,provider,state,started,message_id)

    async def run(self):
        while not self.stop.is_set():
            self.last_tick=asyncio.get_running_loop().time()
            try:
                task=await asyncio.to_thread(self.repository.claim,self.owner)
                if task:
                    await self.process(task)
                    continue
            except Exception:
                logging.getLogger(__name__).warning('queue unavailable')
            try:
                await asyncio.wait_for(self.stop.wait(),self.repository.config.poll_seconds)
            except TimeoutError:
                pass
