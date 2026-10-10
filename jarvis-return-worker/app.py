import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from .config import Config
from .queue import QueueRepository
from .transport import FakeTransport
from .worker import Worker
from .lease import RecoveryWorker
from .api import router
from .health import ready

@asynccontextmanager
async def lifespan(app):
    config=Config.from_env()
    repo=QueueRepository(config)
    await asyncio.to_thread(repo.migrate)
    stop=asyncio.Event()
    worker=Worker(repo,FakeTransport(repo,config.worker_id),stop)
    recovery=RecoveryWorker(repo,stop)
    app.state.config,app.state.repository,app.state.stop=config,repo,stop
    app.state.loops=[asyncio.create_task(worker.run()),asyncio.create_task(recovery.run())]
    logging.getLogger(__name__).warning('Sandbox worker ready; Fake Transport only')
    try:
        yield
    finally:
        stop.set()
        try:
            await asyncio.wait_for(asyncio.gather(*app.state.loops),config.shutdown_seconds)
        except TimeoutError:
            for task in app.state.loops:
                task.cancel()
            await asyncio.gather(*app.state.loops,return_exceptions=True)
        logging.getLogger(__name__).warning('Sandbox worker stopped')

app=FastAPI(title='Jarvis Return Worker — Sandbox',lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
app.include_router(router)

@app.get('/health')
async def health():
    return {'status':'ok','service':'jarvis-return-worker','transport':'fake'}

@app.get('/ready')
async def readiness(request:Request):
    return await ready(request)
