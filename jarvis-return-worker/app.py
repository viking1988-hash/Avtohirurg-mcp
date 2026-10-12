import asyncio
import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from .config import Config
from .queue import QueueRepository
from .transport import FakeTransport, TelegramTransport
from .worker import Worker
from .lease import RecoveryWorker
from .api import router
from .health import ready


def make_transport(config, repository):
    if os.environ.get('JARVIS_TRANSPORT','fake') == 'telegram':
        return TelegramTransport(config.telegram_bot_token,config.telegram_test_chat_id,
                                 int(os.environ.get('JARVIS_TELEGRAM_TIMEOUT_SECONDS','10')))
    return FakeTransport(repository,config.worker_id,float(os.environ.get('JARVIS_FAKE_DELAY_SECONDS','0')))


@asynccontextmanager
async def lifespan(app):
    config=Config.from_env()
    repo=QueueRepository(config)
    await asyncio.to_thread(repo.migrate)
    stop=asyncio.Event()
    worker=Worker(repo,make_transport(config,repo),stop)
    recovery=RecoveryWorker(repo,stop)
    app.state.config,app.state.repository,app.state.stop=config,repo,stop
    app.state.loops=[asyncio.create_task(worker.run()),asyncio.create_task(recovery.run())]
    logging.getLogger(__name__).warning('sandbox worker ready transport=%s',os.environ.get('JARVIS_TRANSPORT','fake'))
    try:
        yield
    finally:
        stop.set()
        try:
            await asyncio.wait_for(asyncio.gather(*app.state.loops),config.shutdown_seconds)
        except TimeoutError:
            for task in app.state.loops: task.cancel()
            await asyncio.gather(*app.state.loops,return_exceptions=True)
        logging.getLogger(__name__).warning('sandbox worker stopped')


app=FastAPI(title='Jarvis Return Worker — Sandbox',lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
app.include_router(router)

@app.get('/health')
async def health():
    return {'status':'ok','service':'jarvis-return-worker','transport':os.environ.get('JARVIS_TRANSPORT','fake')}

@app.get('/ready')
async def readiness(request:Request):
    return await ready(request)
