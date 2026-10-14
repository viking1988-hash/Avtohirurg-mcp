from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient


def make_client():
    calls = []

    class OrchestratorStub:
        def request_email(self, *args):
            calls.append(args)
            return "synthetic-email-event-id"

    app = FastAPI()
    from jarvis_return_worker.api import router
    app.include_router(router)
    app.state.config = SimpleNamespace(
        approval_token="sandbox-approval-test-token",
        read_token="sandbox-read-test-token",
        operator_id="sandbox-test-operator",
    )
    app.state.orchestrator = OrchestratorStub()
    return TestClient(app), calls


def request_body(to="qa@example.test"):
    return {
        "to": to,
        "subject": "Synthetic Sandbox verification",
        "body": "This message is for an isolated test sink only.",
        "correlation_id": "email-smoke-correlation",
        "causation_id": "email-smoke-causation",
    }


def test_email_request_requires_approval_and_delegates_to_orchestrator():
    client, calls = make_client()
    body = request_body()

    assert client.post("/email/requests", json=body).status_code == 401
    assert client.post(
        "/email/requests",
        headers={"Authorization": "Bearer sandbox-read-test-token"},
        json=body,
    ).status_code == 401

    response = client.post(
        "/email/requests",
        headers={"Authorization": "Bearer sandbox-approval-test-token"},
        json=body,
    )
    assert response.status_code == 200
    assert response.json() == "synthetic-email-event-id"
    assert calls == [(
        "qa@example.test",
        "Synthetic Sandbox verification",
        "This message is for an isolated test sink only.",
        "email-smoke-correlation",
        "email-smoke-causation",
    )]


def test_email_request_rejects_non_sandbox_recipient_before_orchestrator():
    client, calls = make_client()
    response = client.post(
        "/email/requests",
        headers={"Authorization": "Bearer sandbox-approval-test-token"},
        json=request_body("customer@example.com"),
    )
    assert response.status_code == 422
    assert calls == []
