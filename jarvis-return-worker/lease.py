import asyncio

class LeaseManager:
    def __init__(self, repository, worker_id):
        self.repository, self.worker_id = repository, worker_id

    async def heartbeat(self, task):
        while True:
            await asyncio.sleep(self.repository.config.heartbeat_seconds)
            await asyncio.to_thread(self.repository.heartbeat,task['task_id'],self.worker_id,task['attempt_count'])

class RecoveryWorker:
    def __init__(self, repository, stop):
        self.repository, self.stop = repository, stop

    async def run(self):
        while not self.stop.is_set():
            try:
                await asyncio.to_thread(self.repository.recover,self.repository.config.worker_id)
            except Exception:
                # No exception text/DSN/payload in logs.
                __import__('logging').getLogger(__name__).warning('recovery unavailable')
            try:
                await asyncio.wait_for(self.stop.wait(),self.repository.config.recovery_seconds)
            except TimeoutError:
                pass
