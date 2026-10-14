import asyncio
import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from psycopg.types.json import Jsonb
from .config import Config
from .queue import QueueRepository
from .transport import FakeTransport, TelegramTransport
from .dispatcher import TransportDispatcher
from .orchestrator import Orchestrator
from .worker import Worker, CRMOperationWorker, EmailOperationWorker
from .lease import RecoveryWorker
from .api import router
from .health import ready
from .events import EventBus
from .outbox import OutboxReplay, ReplayWorker
from .crm import CRMAdapter, CRMProvider
from .email_adapter import EmailAdapter
from .email_provider import EmailProvider
from .http_provider import HTTPProvider


def _project_event(conn,event):
    conn.execute(
        """INSERT INTO jarvis_event_projection
           (event_id,subscriber_name,aggregate_id,event_type,event_version,payload)
           VALUES (%s,'jarvis.lifecycle-projector',%s,%s,%s,%s)
           ON CONFLICT (event_id,subscriber_name) DO NOTHING""",
        (event["event_id"],event["aggregate_id"],event["event_type"],
         event["event_version"],Jsonb(event["payload"])),
    )


def make_dispatcher(config, repository):
    dispatcher=TransportDispatcher()
    selected=os.environ.get('JARVIS_TRANSPORT','fake')
    # Keep the provider registry composable: selecting the primary transport
    # does not unregister other Sandbox providers that have credentials.
    dispatcher.register('fake',FakeTransport(float(os.environ.get('JARVIS_FAKE_DELAY_SECONDS','0'))),enabled=True)
    if config.telegram_bot_token and config.telegram_test_chat_id:
        dispatcher.register('telegram',TelegramTransport(config.telegram_bot_token,config.telegram_test_chat_id,
            int(os.environ.get('JARVIS_TELEGRAM_TIMEOUT_SECONDS','10'))),enabled=True)
    if selected=='telegram':
        if 'telegram' not in dispatcher.names():
            raise ValueError('Telegram requires Sandbox bot credentials and a test chat')
    elif selected!='fake' and selected!='http':
        raise ValueError('unknown transport; refusing startup')
    crm_url=os.environ.get('JARVIS_CRM_SANDBOX_URL','')
    crm_token=os.environ.get('JARVIS_CRM_SANDBOX_TOKEN','')
    if crm_url or crm_token:
        if not crm_url or not crm_token:
            raise ValueError('CRM Sandbox endpoint and token must be configured together')
        dispatcher.register('crm',CRMProvider(crm_url,crm_token,
            int(os.environ.get('JARVIS_CRM_TIMEOUT_SECONDS','8'))),enabled=True)
    email_host=os.environ.get('JARVIS_EMAIL_SANDBOX_SMTP_HOST','')
    if email_host:
        dispatcher.register('email',EmailProvider(email_host,
            int(os.environ.get('JARVIS_EMAIL_SANDBOX_SMTP_PORT','1025')),
            os.environ.get('JARVIS_EMAIL_SANDBOX_FROM','jarvis@localhost.test'),
            int(os.environ.get('JARVIS_EMAIL_TIMEOUT_SECONDS','8'))),enabled=True)
    http_url=os.environ.get('JARVIS_HTTP_SANDBOX_URL','')
    http_token=os.environ.get('JARVIS_HTTP_SANDBOX_TOKEN','')
    if http_url or http_token:
        if not http_url or not http_token:
            raise ValueError('HTTP Sandbox endpoint and token must be configured together')
        dispatcher.register('http',HTTPProvider(http_url,http_token,
            int(os.environ.get('JARVIS_HTTP_TIMEOUT_SECONDS','8'))),enabled=True)
    if selected=='http' and 'http' not in dispatcher.names():
        raise ValueError('HTTP Sandbox endpoint and token are required')
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
    registry=EventBus()
    registry.register("jarvis.lifecycle-projector","*",_project_event,version=1)
    CRMAdapter().register(registry)
    EmailAdapter().register(registry)
    replay=OutboxReplay(repo,registry,owner=config.worker_id)
    orchestrator=Orchestrator(repo,dispatcher,event_bus=registry)
    transport=dispatcher.dispatch(os.environ.get('JARVIS_TRANSPORT','fake')).client
    worker=Worker(repo,transport,stop,orchestrator=orchestrator)
    recovery=RecoveryWorker(orchestrator,stop)
    replay_worker=ReplayWorker(replay,stop,poll_seconds=config.poll_seconds)
    loops=[asyncio.create_task(worker.run()),asyncio.create_task(recovery.run()),
           asyncio.create_task(replay_worker.run())]
    if 'crm' in dispatcher.names():
        loops.append(asyncio.create_task(CRMOperationWorker(orchestrator,stop).run()))
    if 'email' in dispatcher.names():
        loops.append(asyncio.create_task(EmailOperationWorker(orchestrator,stop).run()))
    app.state.config,app.state.repository,app.state.stop,app.state.orchestrator=config,repo,stop,orchestrator
    app.state.event_registry,app.state.outbox_replay=registry,replay
    app.state.loops=loops
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

