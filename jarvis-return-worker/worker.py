import asyncio
import json
import logging
import time
from .dispatcher import TransportDispatcher
from .lease import LeaseManager
from .orchestrator import Orchestrator


def _log(task, owner, provider, state, started, provider_message_id=None):
    record={'task_id':task.get('task_id'),'correlation_id':task.get('correlation_id'),
            'attempt':task.get('attempt_count'),'lease_owner':owner,'provider':provider,
            'provider_message_id':provider_message_id,'state':state,
            'duration_ms':round((time.monotonic()-started)*1000,2)}
    logging.getLogger(__name__).info(json.dumps(record,separators=(',',':'),sort_keys=True))


class Worker:
    def __init__(self, repository, transport, stop, orchestrator=None):
        self.repository, self.transport, self.stop = repository, transport, stop
        self.owner = repository.config.worker_id
        self.leases = LeaseManager(repository,self.owner)
        self.dispatcher=TransportDispatcher()
        if transport is not None:
            name=getattr(transport,'provider','fake')
            self.dispatcher.register(name,transport,enabled=True)
        self.orchestrator=orchestrator or Orchestrator(repository,self.dispatcher)
        self.last_tick = 0.0

    async def process(self, task):
        started=time.monotonic()
        provider=getattr(self.transport,'provider','fake')
        heartbeat=asyncio.create_task(self.leases.heartbeat(task))
        try:
            await self.orchestrator.process(task,self.owner)
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.getLogger(__name__).warning('orchestrator processing failed')
            try:
                await asyncio.to_thread(self.repository.fail,task['task_id'],self.owner,task['attempt_count'])
            except Exception:
                pass
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat,return_exceptions=True)
            state='DONE' if task.get('delivery_receipt') else 'processed'
            _log(task,self.owner,provider,state,started,task.get('provider_message_id'))

    async def run(self):
        while not self.stop.is_set():
            self.last_tick=asyncio.get_running_loop().time()
            try:
                task=await asyncio.to_thread(self.orchestrator.claim,self.owner)
                if task:
                    await self.process(task)
                    continue
            except Exception:
                logging.getLogger(__name__).warning('queue unavailable')
            try:
                await asyncio.wait_for(self.stop.wait(),self.repository.config.poll_seconds)
            except TimeoutError:
                pass
