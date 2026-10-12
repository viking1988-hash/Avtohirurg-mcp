import asyncio
import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from .config import Config
from .queue import QueueRepository
from .transport import FakeTransport, TelegramTransport
from .dispatcher import TransportDispatcher
from .orchestrator import Orchestrator
from .worker import Worker
from .lease import RecoveryWorker
from .api import router
from .health import ready


def make_dispatcher(config, repository):
    dispatcher=TransportDispatcher()
    selected=os.environ.get('JARVIS_TRANSPORT','fake')
    if selected=='telegram':
        # This registration is runtime opt-in only; Sandbox remains configured as fake.
        dispatcher.register('telegram',TelegramTransport(config.telegram_bot_token,config.telegram_test_chat_id,
            int(os.environ.get('JARVIS_TELEGRAM_TIMEOUT_SECONDS','10'))),enabled=True)
    elif selected=='fake':
        dispatcher.register('fake',FakeTransport(float(os.environ.get('JARVIS_FAKE_DELAY_SECONDS','0'))),enabled=True)
    else:
        raise ValueError('unknown transport; refusing startup')
    return dispatcher


def make_transport(config, repository):
    dispatcher=make_dispatcher(config,repository)
    selected=os.environ.get('JARVIS_TRANSPORT','fake')
    return dispatcher.dispatch(selected).client


@asynccontextmanager
async def lifespan(app):
    config=Config.from_env()
    repo=QueueRepository(config)
    await asyncio.to_thread(repo.migrate)
    stop=asyncio.Event()
    dispatcher=make_dispatcher(config,repo)
    orchestrator=Orchestrator(repo,dispatcher)
    transport=dispatcher.dispatch(os.environ.get('JARVIS_TRANSPORT','fake')).client
    worker=Worker(repo,transport,stop,orchestrator=orchestrator)
    recovery=RecoveryWorker(orchestrator,stop)
    app.state.config,app.state.repository,app.state.stop,app.state.orchestrator=config,repo,stop,orchestrator
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
