import io
import json
from urllib.error import HTTPError, URLError
from types import SimpleNamespace

import pytest

from jarvis_return_worker.http_provider import HTTPProvider
from jarvis_return_worker.transport import Classification, DeliveryError


class Response:
    def __init__(self, status=200, data=None):
        self.status = status
        self._data = json.dumps(data or {}).encode()
    def read(self, size): return self._data[:size]
    def __enter__(self): return self
    def __exit__(self, *_): return None


def provider(opener):
    return HTTPProvider("https://booking-sandbox.up.railway.app/api", "test-secret-token",
                        timeout_seconds=3, opener=opener)


def test_send_posts_stable_idempotency_and_keeps_only_receipt_fields():
    calls=[]
    def opener(request, timeout):
        calls.append((request,timeout))
        return Response(data={"message_id":"msg-42", "receipt":"r-42", "status":"accepted",
                              "delivery_time":"2026-10-14T20:00:00Z", "customer_name":"must-not-persist"})
    result=provider(opener).send({"destination_key":"sandbox:test","payload":"synthetic"},"task:1")
    request,timeout=calls[0]
    assert request.get_method()=="POST"
    assert request.full_url=="https://booking-sandbox.up.railway.app/api/messages"
    assert request.get_header("Idempotency-key")=="task:1"
    assert request.get_header("Authorization")=="Bearer test-secret-token"
    assert json.loads(request.data)=={"destination":"sandbox:test","payload":"synthetic"}
    assert timeout==3
    assert result=={"message_id":"msg-42","receipt":"r-42","status":"accepted",
                    "delivery_time":"2026-10-14T20:00:00Z","provider_message_id":"msg-42"}
    assert "customer_name" not in result


@pytest.mark.parametrize("url",[
    "http://booking-sandbox.up.railway.app", "https://booking.production.up.railway.app",
    "https://api.example.com", "https://user:password@booking-sandbox.up.railway.app",
    "https://booking-sandbox.up.railway.app?token=leak", "https://booking-sandbox.up.railway.app#fragment",
])
def test_rejects_non_sandbox_or_ambiguous_endpoint(url):
    with pytest.raises(ValueError): HTTPProvider(url,"secret")


def test_timeout_is_unknown_and_does_not_expose_credentials_or_network_details():
    def opener(*_args,**_kwargs): raise URLError("failed for https://user:secret@private/ and Bearer secret")
    with pytest.raises(DeliveryError) as raised:
        provider(opener).send({"destination_key":"sandbox:x","payload":"y"},"stable-key")
    assert raised.value.classification==Classification.UNKNOWN
    assert "secret" not in str(raised.value) and "private" not in str(raised.value)


@pytest.mark.parametrize("status,expected",[(429,Classification.RETRYABLE),(400,Classification.PERMANENT),(500,Classification.UNKNOWN)])
def test_http_error_classes_are_conservative(status,expected):
    def opener(*_args,**_kwargs):
        raise HTTPError("https://booking-sandbox.up.railway.app",status,"hidden body",{"Retry-After":"7"},io.BytesIO(b"secret response"))
    with pytest.raises(DeliveryError) as raised:
        provider(opener).send({"destination_key":"sandbox:x","payload":"y"},"stable-key")
    assert raised.value.classification==expected
    assert "secret" not in str(raised.value)
    if status==429: assert raised.value.retry_after==7


def test_success_without_receipt_is_unknown_and_never_claimed_as_success():
    with pytest.raises(DeliveryError) as raised:
        provider(lambda *_a,**_k:Response(data={"message_id":"maybe-sent"})).send(
            {"destination_key":"sandbox:x","payload":"y"},"stable-key")
    assert raised.value.classification==Classification.UNKNOWN
    assert raised.value.code=="http_provider_receipt_missing"


def test_success_without_message_id_is_unknown_and_payload_encoding_failure_is_permanent():
    with pytest.raises(DeliveryError) as missing_id:
        provider(lambda *_a,**_k:Response(data={"receipt_id":"receipt-only"})).send(
            {"destination_key":"sandbox:x","payload":"y"},"stable-key")
    assert missing_id.value.classification==Classification.UNKNOWN
    with pytest.raises(DeliveryError) as invalid_payload:
        provider(lambda *_a,**_k:pytest.fail("must not issue an HTTP call")).send(
            {"destination_key":"sandbox:x","payload":object()},"stable-key")
    assert invalid_payload.value.classification==Classification.PERMANENT


def test_redirect_handler_refuses_cross_host_credential_forwarding():
    from jarvis_return_worker.http_provider import _NoRedirect
    from urllib.request import Request
    with pytest.raises(HTTPError):
        _NoRedirect().redirect_request(Request("https://booking-sandbox.up.railway.app/messages"),
            None,302,"Found",{},"https://other.example/private")


def test_http_is_optional_provider_not_a_primary_worker_transport(monkeypatch):
    from jarvis_return_worker.config import Config
    monkeypatch.setenv("JARVIS_TRANSPORT","http")
    monkeypatch.setenv("JARVIS_SANDBOX_PROJECT_ID","sandbox-project")
    monkeypatch.setenv("JARVIS_SANDBOX_ENVIRONMENT_ID","sandbox-staging")
    monkeypatch.setenv("RAILWAY_PROJECT_ID","sandbox-project")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_ID","sandbox-staging")
    with pytest.raises(ValueError,match="unsupported transport"):
        Config.from_env()


def test_http_destination_routes_via_orchestrator_not_dispatcher_special_case():
    from jarvis_return_worker.orchestrator import Orchestrator
    service=Orchestrator(None,None)
    assert service._provider_for({"destination_key":"sandbox:http:synthetic"})=="http"
    assert service._provider_for({"destination_key":"sandbox:telegram:-1000000000000"})=="telegram"
    assert service._provider_for({"destination_key":"sandbox:test"})=="fake"


def test_update_cancel_status_and_receipt_use_encoded_provider_id():
    calls=[]
    def opener(request,**_kwargs):
        calls.append(request)
        if request.get_method()=="GET": return Response(data={"status":"delivered","receipt_id":"r"})
        return Response(data={"message_id":"id/with space","receipt_id":"r2","status":"updated"})
    client=provider(opener)
    assert client.update({"provider_message_id":"id/with space","payload":"x"},"update-key")["receipt_id"]=="r2"
    assert calls[-1].full_url.endswith("/messages/id%2Fwith%20space")
    assert client.cancel({"message_id":"id-1"},"cancel-key")["status"]=="updated"
    assert calls[-1].full_url.endswith("/messages/id-1/cancel")
    assert client.checkStatus({"message_id":"id-1"})=="delivered"
    assert client.get_receipt("id-1")=={"receipt_id":"r","status":"delivered"}


def test_http_metrics_are_rendered():
    from jarvis_return_worker import metrics
    class Stats:
        def stats(self): return {}
    metrics.observe_http_provider("success",0.01)
    metrics.observe_http_provider("unknown",0.02)
    output=metrics.render_metrics(Stats()).decode()
    for metric in ("http_provider_requests_total","http_provider_success_total",
                   "http_provider_failed_total","http_provider_unknown_total",
                   "http_provider_latency_seconds"):
        assert metric in output


def test_generic_http_provider_registers_alongside_existing_providers(monkeypatch):
    from jarvis_return_worker.app import make_dispatcher
    monkeypatch.setenv("JARVIS_TRANSPORT","fake")
    monkeypatch.setenv("JARVIS_CRM_SANDBOX_URL","https://crm-sandbox.up.railway.app")
    monkeypatch.setenv("JARVIS_CRM_SANDBOX_TOKEN","synthetic-crm-token")
    monkeypatch.setenv("JARVIS_EMAIL_SANDBOX_SMTP_HOST","mailpit.railway.internal")
    monkeypatch.setenv("JARVIS_HTTP_SANDBOX_URL","https://booking-sandbox.up.railway.app")
    monkeypatch.setenv("JARVIS_HTTP_SANDBOX_TOKEN","synthetic-http-token")
    config=SimpleNamespace(telegram_bot_token="synthetic-bot",telegram_test_chat_id="-1000000000000")
    dispatcher=make_dispatcher(config,None)
    assert dispatcher.names()==("crm","email","fake","http","telegram")
    assert dispatcher.dispatch("http").provider=="http"
