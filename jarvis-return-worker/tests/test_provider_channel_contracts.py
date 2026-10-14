"""Provider-shaped tests with synthetic payloads and mocked transports only."""
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis_return_worker.api import router
from jarvis_return_worker.dispatcher import DispatcherError, TransportDispatcher
from jarvis_return_worker.policy import PolicyEngine
from jarvis_return_worker.transport import Classification, DeliveryError, TelegramTransport


class Response:
    status = 200
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self, limit): return self.payload[:limit]


def test_telegram_provider_uses_only_allowlisted_chat_and_returns_minimal_receipt(monkeypatch):
    calls = []
    def fake_urlopen(request, timeout):
        calls.append((request, timeout))
        return Response({"ok": True, "result": {
            "message_id": 73, "chat": {"id": -100123}, "text": "sensitive echoed text"}})
    monkeypatch.setattr("jarvis_return_worker.transport.urlopen", fake_urlopen)
    provider = TelegramTransport("synthetic-bot-token", "-100123", timeout_seconds=3)

    result = provider.deliver({
        "destination_key": "sandbox:telegram:-100123",
        "payload": "Synthetic test only",
    })

    request, timeout = calls[0]
    assert timeout == 3
    assert request.full_url.endswith("/sendMessage")
    assert json.loads(request.data) == {
        "chat_id": "-100123", "text": "Synthetic test only"}
    assert result == {
        "receipt_id": "telegram:-100123:73",
        "message_id": "73",
        "chat_id": "-100123",
        "response": {"ok": True, "message_id": "73", "chat_id": "-100123"},
    }
    assert "sensitive echoed text" not in repr(result)


def test_telegram_unknown_result_is_human_reconciliation_not_retry(monkeypatch):
    def timeout(*args, **kwargs):
        raise TimeoutError("synthetic timeout")
    monkeypatch.setattr("jarvis_return_worker.transport.urlopen", timeout)
    provider = TelegramTransport("synthetic-bot-token", "-100123", timeout_seconds=3)
    with pytest.raises(DeliveryError) as raised:
        provider.deliver({
            "destination_key": "sandbox:telegram:-100123",
            "payload": "Synthetic test only",
        })
    assert raised.value.classification == Classification.UNKNOWN
    decision = PolicyEngine().delivery_failure(raised.value, {"delivery_started_at": "set"})
    assert decision.action == "reconcile"
    assert not PolicyEngine().may_resend(
        {"delivery_started_at": "set"}, raised.value.classification)


def test_telegram_wrong_chat_fails_before_network(monkeypatch):
    monkeypatch.setattr(
        "jarvis_return_worker.transport.urlopen",
        lambda *args, **kwargs: pytest.fail("network must not be called"),
    )
    provider = TelegramTransport("synthetic-bot-token", "-100123", timeout_seconds=3)
    with pytest.raises(DeliveryError) as raised:
        provider.deliver({
            "destination_key": "sandbox:telegram:-999",
            "payload": "Synthetic test only",
        })
    assert raised.value.classification == Classification.PERMANENT


def test_sms_provider_is_unregistered_and_dispatch_fails_closed():
    with pytest.raises(DispatcherError, match="provider_not_registered"):
        TransportDispatcher().dispatch("sms")


def test_email_outbox_request_requires_authenticated_human_and_test_recipient():
    class OrchestratorStub:
        def __init__(self): self.requests = []
        def request_email(self, *args):
            self.requests.append(args)
            return "synthetic-event-id"

    app = FastAPI()
    app.state.config = SimpleNamespace(
        approval_token="synthetic-approval-token",
        read_token="synthetic-read-token",
        operator_id="synthetic-human",
    )
    app.state.orchestrator = OrchestratorStub()
    app.include_router(router)
    client = TestClient(app)
    payload = {
        "to": "qa@example.test",
        "subject": "Synthetic approval check",
        "body": "No real email is sent",
        "correlation_id": "corr-synthetic-1",
        "causation_id": "cause-synthetic-1",
    }

    rejected = client.post("/email/requests", json=payload)
    assert rejected.status_code == 401
    assert app.state.orchestrator.requests == []

    invalid_recipient = client.post("/email/requests", json={
        **payload, "to": "customer@example.com"})
    assert invalid_recipient.status_code == 422
    assert app.state.orchestrator.requests == []

    accepted = client.post("/email/requests", json=payload, headers={
        "Authorization": "Bearer synthetic-approval-token"})
    assert accepted.status_code == 200
    assert accepted.json() == "synthetic-event-id"
    assert len(app.state.orchestrator.requests) == 1
    assert app.state.orchestrator.requests[0][0] == "qa@example.test"
