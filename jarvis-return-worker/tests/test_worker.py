import asyncio
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from jarvis_return_worker.approval import action_fingerprint,valid_approval
from jarvis_return_worker.config import Config
from jarvis_return_worker.transport import TelegramTransport,EmailTransport,WhatsAppTransport,CRMTransport
from jarvis_return_worker.worker import Worker
from jarvis_return_worker.app import app

NOW=datetime.now(timezone.utc)

def task():
    fp=action_fingerprint('test','sandbox:test','payload')
    return dict(task_id='test',destination_key='sandbox:test',payload='payload',approved_by='human',
        approved_at=NOW,approval_expires_at=NOW+timedelta(minutes=5),
        approved_fingerprint=fp,action_fingerprint=fp,attempt_count=1)

@pytest.mark.parametrize('transport',[TelegramTransport,EmailTransport,WhatsAppTransport,CRMTransport])
def test_real_transport_disabled(transport):
    with pytest.raises(RuntimeError): transport()

@pytest.mark.parametrize('change',[{'approved_by':None},{'payload':'changed'},
    {'approval_expires_at':NOW-timedelta(seconds=1)},{'destination_key':'telegram:real'}])
def test_approval_integrity(change):
    assert not valid_approval({**task(),**change})

def test_dsn_guards():
    import secrets
    from urllib.parse import urlunsplit
    password=secrets.token_urlsafe(32)
    url=urlunsplit(('postgresql','jarvis_sandbox:'+password+'@jarvis-sandbox-postgres.railway.internal','/jarvis_return_queue_test','',''))
    c=Config(url,secrets.token_urlsafe(32),secrets.token_urlsafe(32),'human','worker')
    c.validate()
    from dataclasses import replace
    with pytest.raises(ValueError): replace(c,database_url=c.database_url+'?host=production').validate()
    with pytest.raises(ValueError): replace(c,approval_token=c.read_token).validate()
    assert password not in repr(c)

class Repo:
    config=SimpleNamespace(worker_id='worker',heartbeat_seconds=1)
    def __init__(self): self.calls=[]
    def heartbeat(self,*args): self.calls.append('heartbeat')
    def finish(self,*args): self.calls.append('finish')
    def fail(self,*args): self.calls.append('fail')

class Transport:
    def __init__(self,repo): self.repo=repo
    def deliver(self,task): self.repo.calls.append('transport'); return 'receipt'

def test_worker_sequence():
    repo=Repo()
    asyncio.run(Worker(repo,Transport(repo),asyncio.Event()).process(task()))
    assert repo.calls==['heartbeat','transport','finish']

def test_worker_rejects_before_transport():
    repo=Repo()
    asyncio.run(Worker(repo,Transport(repo),asyncio.Event()).process({**task(),'payload':'changed'}))
    assert repo.calls==['fail']

def test_api_authorization_separation():
    app.state.config=SimpleNamespace(read_token='r'*32,approval_token='a'*32,operator_id='human')
    client=TestClient(app)
    assert client.get('/queue').status_code==401
    assert client.get('/metrics').status_code==401
    body={'task_id':'test','fingerprint':'a'*64,'expires_at':(NOW+timedelta(minutes=5)).isoformat()}
    assert client.post('/approve',json=body,headers={'Authorization':'Bearer '+'r'*32}).status_code==401
    assert client.post('/cancel',json={'task_id':'test'},headers={'Authorization':'Bearer '+'r'*32}).status_code==401
    assert client.post('/requeue',json={'task_id':'test'}).status_code==401
    assert client.get('/health').status_code==200

def test_worker_heartbeat_failure_never_calls_transport():
    class Broken(Repo):
        def heartbeat(self,*args):
            self.calls.append('heartbeat_failed')
            raise RuntimeError('database unavailable')
    repo=Broken()
    asyncio.run(Worker(repo,Transport(repo),asyncio.Event()).process(task()))
    assert repo.calls==['heartbeat_failed','fail']

def test_stop_event_prevents_new_claim():
    class NoClaims(Repo):
        def claim(self,owner): raise AssertionError('claimed after stop')
    async def scenario():
        stop=asyncio.Event();stop.set()
        await Worker(NoClaims(),None,stop).run()
    asyncio.run(scenario())

def test_api_does_not_accept_caller_actor_or_real_destination():
    app.state.config=SimpleNamespace(read_token='r'*32,approval_token='a'*32,operator_id='human')
    client=TestClient(app)
    payload={'task_id':'test','destination':'telegram:real','payload':'x','correlation_id':'x'}
    assert client.post('/enqueue',json=payload,headers={'Authorization':'Bearer '+'r'*32}).status_code==422
    payload={'task_id':'test','fingerprint':'a'*64,'expires_at':NOW.isoformat(),'actor':'forged-human'}
    assert client.post('/approve',json=payload,headers={'Authorization':'Bearer '+'a'*32}).status_code==422
