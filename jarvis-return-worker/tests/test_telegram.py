import asyncio
import io
import json
from datetime import datetime,timedelta,timezone
from email.message import Message
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError, URLError
import pytest
from jarvis_return_worker.transport import TelegramTransport,DeliveryError,Classification
from jarvis_return_worker.metrics import render_metrics
from jarvis_return_worker.worker import Worker
from jarvis_return_worker.approval import action_fingerprint

CHAT='-1001234567890'
TOKEN='test-only-token-do-not-log'

def response(body,status=200):
    class Response:
        def __init__(self): self.status=status
        def read(self,n): return json.dumps(body).encode()
        def __enter__(self): return self
        def __exit__(self,*args): return False
    return Response()

def task():
    now=datetime.now(timezone.utc)
    fp=action_fingerprint('test','sandbox:telegram:'+CHAT,'sandbox test')
    return dict(task_id='test',destination_key='sandbox:telegram:'+CHAT,payload='sandbox test',
                correlation_id='corr-test',approved_by='human',approved_at=now,
                approval_expires_at=now+timedelta(minutes=5),approved_fingerprint=fp,
                action_fingerprint=fp,attempt_count=1)

def valid_result(mid=42):
    return {'ok':True,'result':{'message_id':mid,'chat':{'id':int(CHAT)},'text':'discard this text','from':{'username':'private'}}}

def test_telegram_success_stores_only_minimal_receipt_and_test_chat():
    t=TelegramTransport(TOKEN,CHAT)
    with patch('jarvis_return_worker.transport.urlopen',return_value=response(valid_result())) as call:
        result=t.deliver({**task(),'destination_key':'sandbox:telegram:'+CHAT,'payload':'sandbox test'})
    req=call.call_args.args[0]
    assert TOKEN in req.full_url  # Bot API requires token in URL; no logs or exception expose it.
    assert result=={'receipt_id':f'telegram:{CHAT}:42','message_id':'42','chat_id':CHAT,
                    'response':{'ok':True,'message_id':'42','chat_id':CHAT}}
    assert 'discard this text' not in json.dumps(result) and 'private' not in json.dumps(result)

def test_chat_allowlist_blocks_any_non_test_destination_before_network():
    with patch('jarvis_return_worker.transport.urlopen') as call:
        with pytest.raises(DeliveryError) as err:
            TelegramTransport(TOKEN,CHAT).deliver({**task(),'destination_key':'sandbox:telegram:999','payload':'test'})
    assert err.value.classification==Classification.PERMANENT
    call.assert_not_called()

@pytest.mark.parametrize('error,classification',[
    (HTTPError('https://api.telegram.org/redacted',429,'rate limited',Message(),io.BytesIO(b'{"parameters":{"retry_after":7}}')),Classification.UNKNOWN),
    (HTTPError('https://api.telegram.org/redacted',500,'server',Message(),io.BytesIO(b'{}')),Classification.UNKNOWN),
    (HTTPError('https://api.telegram.org/redacted',401,'unauthorized',Message(),io.BytesIO(b'{}')),Classification.PERMANENT),
    (HTTPError('https://api.telegram.org/redacted',400,'bad chat',Message(),io.BytesIO(b'{}')),Classification.PERMANENT),
    (TimeoutError('connection timeout'),Classification.UNKNOWN),
    (URLError('connection dropped'),Classification.UNKNOWN),
])
def test_error_classification_and_token_not_in_exception(error,classification):
    with patch('jarvis_return_worker.transport.urlopen',side_effect=error) as call:
        with pytest.raises(DeliveryError) as err:
            TelegramTransport(TOKEN,CHAT).deliver({**task(),'destination_key':'sandbox:telegram:'+CHAT,'payload':'test'})
    assert err.value.classification==classification
    assert TOKEN not in str(err.value)
    assert call.call_count==1  # No automatic replay on any response or network error.
    if err.value.code=='telegram_rate_limit_outcome_unknown':
        assert err.value.retry_after==7

def test_metrics_export_required_names():
    class Repo:
        def stats(self): return {}
    output=render_metrics(Repo()).decode()
    for name in ('telegram_send_total','telegram_send_success_total','telegram_send_failure_total',
                 'telegram_send_unknown_total','telegram_duplicate_prevented_total','telegram_api_latency_seconds'):
        assert name in output

class Repo:
    config=SimpleNamespace(worker_id='worker',heartbeat_seconds=1)
    def __init__(self): self.calls=[]
    def heartbeat(self,*args): self.calls.append('heartbeat')
    def mark_delivery_started(self,*args): self.calls.append('mark')
    def verify_dispatch_lease(self,*args): return True
    def save_delivery_receipt(self,*args): self.calls.append(('receipt',args[-3:]))
    def finish(self,*args): self.calls.append('done')
    def fail(self,*args): self.calls.append(('fail',args))
    def retry_confirmed_rejection(self,*args): self.calls.append(('retry',args[-1]))
    def fail_permanent_rejection(self,*args): self.calls.append(('permanent',args[-1]))

def test_worker_receipt_durable_before_done_and_structured_log(caplog):
    caplog.set_level('INFO',logger='jarvis_return_worker.worker')
    repo=Repo()
    class Transport:
        provider='telegram'
        def deliver(self,t): return {'receipt_id':'telegram:chat:7','message_id':'7','chat_id':'chat','response':{'ok':True,'message_id':'7','chat_id':'chat'}}
    asyncio.run(Worker(repo,Transport(),asyncio.Event()).process(task()))
    assert repo.calls[0:2]==['heartbeat','mark']
    assert repo.calls[2][0]=='receipt' and repo.calls[3]=='done'
    record=json.loads(next(r.message for r in caplog.records if r.name=='jarvis_return_worker.worker'))
    assert record=={'task_id':'test','correlation_id':'corr-test','attempt':1,'lease_owner':'worker',
                    'provider':'telegram','provider_message_id':'7','state':'DONE','duration_ms':record['duration_ms']}

def test_worker_unknown_does_not_auto_retry():
    repo=Repo()
    class Transport:
        provider='telegram'
        def deliver(self,t): raise DeliveryError(Classification.UNKNOWN,'timeout')
    asyncio.run(Worker(repo,Transport(),asyncio.Event()).process(task()))
    assert not any(isinstance(x,tuple) and x[0]=='retry' for x in repo.calls)
    assert any(isinstance(x,tuple) and x[0]=='fail' for x in repo.calls)



@pytest.mark.parametrize('body,status,classification',[
    ({'ok':True,'result':{'message_id':51,'chat':{'id':999}}},200,Classification.UNKNOWN),
    ({'ok':True,'result':{'chat':{'id':int(CHAT)}}},200,Classification.UNKNOWN),
    ({'ok':False,'description':'synthetic rejected'},200,Classification.UNKNOWN),
    ({'ok':False,'description':'synthetic upstream failure'},503,Classification.UNKNOWN),
    ({'ok':False,'parameters':{'retry_after':4}},429,Classification.UNKNOWN),
])
def test_telegram_p1_response_mapping_never_invents_receipt(body,status,classification):
    """All transport responses are synthetic; no Telegram request leaves the process."""
    with patch('jarvis_return_worker.transport.urlopen',return_value=response(body,status)) as call:
        with pytest.raises(DeliveryError) as err:
            TelegramTransport(TOKEN,CHAT).deliver(task())
    assert err.value.classification==classification
    assert TOKEN not in str(err.value)
    assert call.call_count==1


def test_telegram_p1_unreadable_accepted_response_is_unknown_not_retryable():
    class InvalidJSON:
        status=200
        def read(self,n): return b'{not valid json'
        def __enter__(self): return self
        def __exit__(self,*args): return False
    with patch('jarvis_return_worker.transport.urlopen',return_value=InvalidJSON()) as call:
        with pytest.raises(DeliveryError) as err:
            TelegramTransport(TOKEN,CHAT).deliver(task())
    assert err.value.classification==Classification.UNKNOWN
    assert err.value.code=='telegram_response_unreadable'
    assert call.call_count==1


def test_telegram_contradictory_http_success_has_distinct_unknown_code():
    with patch('jarvis_return_worker.transport.urlopen', return_value=response({'ok': False, 'description': 'synthetic rejection'}, 200)) as call:
        with pytest.raises(DeliveryError) as err:
            TelegramTransport(TOKEN, CHAT).deliver(task())
    assert err.value.classification == Classification.UNKNOWN
    assert err.value.code == 'telegram_contradictory_response_unknown'
    assert TOKEN not in str(err.value)
    call.assert_called_once()
