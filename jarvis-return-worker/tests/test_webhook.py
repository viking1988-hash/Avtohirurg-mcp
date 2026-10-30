import json,socket,struct,threading,time,uuid
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from jarvis_return_worker.http_client import HTTPClient
from jarvis_return_worker.webhook_provider import WebhookProvider
from jarvis_return_worker.transport import Classification,DeliveryError

class MockHandler(BaseHTTPRequestHandler):
    statuses=[]
    delay=0
    reset=False
    calls=0
    headers_seen={}
    def log_message(self,*args):pass
    def do_GET(self):self._handle()
    def do_POST(self):self._handle()
    def do_PUT(self):self._handle()
    def do_PATCH(self):self._handle()
    def do_DELETE(self):self._handle()
    def _handle(self):
        type(self).calls+=1
        type(self).headers_seen={k.lower():v for k,v in self.headers.items()}
        if type(self).delay:time.sleep(type(self).delay)
        if type(self).reset:
            self.connection.setsockopt(socket.SOL_SOCKET,socket.SO_LINGER,struct.pack("ii",1,0))
            self.connection.close();return
        status=type(self).statuses.pop(0) if type(self).statuses else 200
        body=json.dumps({"receipt_id":"mock-receipt","status":"accepted","private":"not-persisted"}).encode()
        self.send_response(status);self.send_header("Content-Type","application/json")
        self.send_header("X-Response-ID","mock-response");self.send_header("Content-Length",str(len(body)))
        self.end_headers()
        try:self.wfile.write(body)
        except (BrokenPipeError,ConnectionResetError):pass

@pytest.fixture
def mock_server():
    MockHandler.statuses=[];MockHandler.delay=0;MockHandler.reset=False;MockHandler.calls=0;MockHandler.headers_seen={}
    server=ThreadingHTTPServer(("127.0.0.1",0),MockHandler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown();server.server_close();thread.join(timeout=2)

def provider(base,timeout=1,retries=1):
    return WebhookProvider(base,timeout_seconds=timeout,max_retries=retries,allow_insecure_localhost=True)

def task(method="POST"):
    return {"method":method,"path":"/events","body":{"event":"test"},"correlation_id":"corr-test","request_id":"request-test"}

@pytest.mark.parametrize("method",["GET","POST","PUT","PATCH","DELETE"])
def test_http_client_supports_standard_methods_and_context_headers(mock_server,method):
    result=provider(mock_server).send(task(method),"idem-test")
    assert result["http_status"]==200 and result["provider_status"]=="accepted"
    assert result["request_id"]=="request-test" and result["response_id"]=="mock-response"
    assert MockHandler.headers_seen["x-correlation-id"]=="corr-test"
    assert MockHandler.headers_seen["idempotency-key"]=="idem-test"

@pytest.mark.parametrize("status", [200,202])
def test_success_and_receipt_are_minimized(mock_server,status):
    MockHandler.statuses=[status]
    result=provider(mock_server).send(task(),"idem")
    assert result["http_status"]==status
    assert result["receipt"]=={"receipt_id":"mock-receipt","status":"accepted"}
    assert "private" not in repr(result)

@pytest.mark.parametrize("status", [400,401,404])
def test_client_errors_are_permanent(mock_server,status):
    MockHandler.statuses=[status]
    with pytest.raises(DeliveryError) as error: provider(mock_server).send(task(),"idem")
    assert error.value.classification==Classification.PERMANENT
    assert MockHandler.calls==1

def test_429_is_retryable_without_blind_post_retry(mock_server):
    MockHandler.statuses=[429]
    with pytest.raises(DeliveryError) as error: provider(mock_server).send(task(),"idem")
    assert error.value.classification==Classification.RETRYABLE
    assert MockHandler.calls==1

def test_server_error_is_unknown_without_blind_post_retry(mock_server):
    MockHandler.statuses=[500]
    with pytest.raises(DeliveryError) as error: provider(mock_server).send(task(),"idem")
    assert error.value.classification==Classification.UNKNOWN
    assert MockHandler.calls==1

def test_conflict_is_unknown_for_side_effecting_request(mock_server):
    MockHandler.statuses=[409]
    with pytest.raises(DeliveryError) as error: provider(mock_server).send(task(),"idem")
    assert error.value.classification==Classification.UNKNOWN
    assert MockHandler.calls==1

def test_get_retries_with_bounded_backoff(mock_server):
    MockHandler.statuses=[429,200]
    client=HTTPClient(timeout_seconds=1,max_retries=1,opener=None,sleep=lambda _:None,random_fn=lambda:0)
    webhook=WebhookProvider(mock_server,http_client=client,allow_insecure_localhost=True)
    from jarvis_return_worker.metrics import _webhook_requests
    before=_webhook_requests._value.get()
    result=webhook.send(task("GET"),"idem")
    assert result["retry_count"]==1 and MockHandler.calls==2
    assert _webhook_requests._value.get()-before==2

def test_timeout_and_connection_reset_are_unknown_without_retry(mock_server):
    MockHandler.delay=0.2
    with pytest.raises(DeliveryError) as error: provider(mock_server,timeout=.05).send(task(),"idem")
    assert error.value.classification==Classification.UNKNOWN
    MockHandler.delay=0;MockHandler.reset=True;MockHandler.calls=0
    with pytest.raises(DeliveryError) as error: provider(mock_server).send(task(),"idem")
    assert error.value.classification==Classification.UNKNOWN
    assert MockHandler.calls==1

@pytest.mark.parametrize("url",["http://example.com","https://127.0.0.1","https://user:pass@hooks.example.com","https://hooks.example.com?token=x"])
def test_provider_rejects_insecure_or_credential_bearing_endpoint(url):
    with pytest.raises(ValueError):WebhookProvider(url)

@pytest.mark.parametrize("path",["https://evil.example/x","//evil.example/x","/../private","/safe?token=x","/safe#fragment"])
def test_provider_rejects_untrusted_paths(path):
    p=provider("http://127.0.0.1:9")
    with pytest.raises(DeliveryError):p.send({"method":"POST","path":path},"idem")

def test_provider_surface_and_metrics(mock_server):
    p=provider(mock_server)
    assert p.checkStatus({"path":"/events"})=="accepted"
    assert p.getReceipt("response-1")["http_status"]==200
    assert p.cancel(task(),"idem")["http_status"]==200
    from prometheus_client import generate_latest
    from jarvis_return_worker import metrics
    rendered=generate_latest(metrics._registry).decode()
    for name in ("webhook_requests_total","webhook_success_total","webhook_failed_total","webhook_unknown_total","webhook_replayed_total","webhook_duplicate_prevented_total","webhook_latency_seconds_bucket"):
        assert name in rendered


def test_webhook_endpoint_credentials_alone_do_not_enable_outbound(monkeypatch):
    from jarvis_return_worker.app import make_dispatcher
    monkeypatch.setenv("JARVIS_TRANSPORT", "fake")
    monkeypatch.delenv("JARVIS_WEBHOOK_OUTBOUND_APPROVED", raising=False)
    monkeypatch.setenv("JARVIS_WEBHOOK_BASE_URL", "https://example.test")
    monkeypatch.setenv("JARVIS_WEBHOOK_TOKEN", "synthetic-ci-token")
    monkeypatch.delenv("JARVIS_CRM_SANDBOX_URL", raising=False)
    monkeypatch.delenv("JARVIS_CRM_SANDBOX_TOKEN", raising=False)
    monkeypatch.delenv("JARVIS_EMAIL_SANDBOX_SMTP_HOST", raising=False)
    monkeypatch.delenv("JARVIS_HTTP_SANDBOX_URL", raising=False)
    monkeypatch.delenv("JARVIS_HTTP_SANDBOX_TOKEN", raising=False)
    config = SimpleNamespace(telegram_bot_token=None, telegram_test_chat_id=None)

    dispatcher = make_dispatcher(config, None)

    assert "webhook" not in dispatcher.names()
    assert "fake" in dispatcher.names()


def test_webhook_outbound_requires_exact_opt_in_without_sending(monkeypatch):
    from jarvis_return_worker.app import make_dispatcher
    monkeypatch.setenv("JARVIS_TRANSPORT", "fake")
    monkeypatch.setenv("JARVIS_WEBHOOK_OUTBOUND_APPROVED", "1")
    monkeypatch.setenv("JARVIS_WEBHOOK_BASE_URL", "https://example.test")
    monkeypatch.setenv("JARVIS_WEBHOOK_TOKEN", "synthetic-ci-token")
    monkeypatch.delenv("JARVIS_CRM_SANDBOX_URL", raising=False)
    monkeypatch.delenv("JARVIS_CRM_SANDBOX_TOKEN", raising=False)
    monkeypatch.delenv("JARVIS_EMAIL_SANDBOX_SMTP_HOST", raising=False)
    monkeypatch.delenv("JARVIS_HTTP_SANDBOX_URL", raising=False)
    monkeypatch.delenv("JARVIS_HTTP_SANDBOX_TOKEN", raising=False)
    config = SimpleNamespace(telegram_bot_token=None, telegram_test_chat_id=None)

    dispatcher = make_dispatcher(config, None)

    assert "webhook" in dispatcher.names()
    assert dispatcher.dispatch("webhook").provider == "webhook"
