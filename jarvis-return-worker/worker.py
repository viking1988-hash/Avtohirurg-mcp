import asyncio
import logging
from .approval import valid_approval
from .lease import LeaseManager
from .queue import Conflict

class Worker:
    def __init__(self, repository, transport, stop):
        self.repository, self.transport, self.stop = repository, transport, stop
        self.owner = repository.config.worker_id
        self.leases = LeaseManager(repository,self.owner)
        self.last_tick = 0.0

    async def process(self, task):
        # No transport call without exact approval; repository repeats under lock.
        if not valid_approval(task):
            await asyncio.to_thread(self.repository.fail,task['task_id'],self.owner,task['attempt_count'])
            return
        heartbeat = asyncio.create_task(self.leases.heartbeat(task))
        async def effect():
            await asyncio.to_thread(self.repository.heartbeat,task['task_id'],self.owner,task['attempt_count'])
            receipt = await asyncio.to_thread(self.transport.deliver,task)
            await asyncio.to_thread(self.repository.finish,task['task_id'],self.owner,task['attempt_count'],receipt)
        delivery = asyncio.create_task(effect())
        try:
            done,_=await asyncio.wait([heartbeat,delivery],return_when=asyncio.FIRST_COMPLETED)
            if heartbeat in done:
                await heartbeat  # heartbeat failure stops processing; fencing protects in-flight DB operations
                raise Conflict('heartbeat stopped')
            await delivery
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.getLogger(__name__).warning('task processing failed; receipt reconciliation retained')
            try:
                await asyncio.to_thread(self.repository.fail,task['task_id'],self.owner,task['attempt_count'])
            except Exception:
                pass  # expired lease remains for Recovery Worker
        finally:
            heartbeat.cancel()
            delivery.cancel()
            await asyncio.gather(heartbeat,delivery,return_exceptions=True)

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
