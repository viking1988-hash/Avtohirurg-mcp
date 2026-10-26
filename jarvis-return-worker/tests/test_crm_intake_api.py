from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis_return_worker.api import router


def make_client():
    calls = []

    class OrchestratorStub:
        def prepare_crm_intake(self, *args):
            calls.append(("prepare", args))
            return {
                "source_id": args[0]["source_id"],
                "state": "PREPARED",
                "status": "approval_required",
                "version": 0,
                "external_lead_id": None,
                "external_status": None,
                "created_at": None,
                "updated_at": None,
                "deduplicated": False,
            }

        def approve_crm_intake(self, *args):
            calls.append(("approve", args))
            return {
                "source_id": args[0],
                "state": "PENDING",
                "status": "pending_external_confirmation",
                "version": 1,
                "external_lead_id": None,
                "external_status": None,
                "created_at": None,
                "updated_at": None,
                "deduplicated": False,
            }

    app = FastAPI()
    app.include_router(router)
    app.state.config = SimpleNamespace(
        approval_token="sandbox-approval-test-token",
        read_token="sandbox-read-test-token",
        operator_id="sandbox-test-operator",
    )
    app.state.orchestrator = OrchestratorStub()
    return TestClient(app), calls


def test_prepare_intake_uses_read_auth_and_approval_requires_human_token():
    client, calls = make_client()
    source_id = "jarvis:123e4567-e89b-42d3-a456-426614174000"
    payload = {
        "source_id": source_id,
        "name": "Synthetic customer",
        "phone": "000000000",
        "make": "Synthetic",
        "symptom": "synthetic only",
    }
    body = {"payload": payload, "correlation_id": "synthetic-correlation"}

    assert client.post("/crm/intakes", json=body).status_code == 401
    assert client.post(
        "/crm/intakes",
        headers={"Authorization": "Bearer sandbox-approval-test-token"},
        json=body,
    ).status_code == 401
    prepared = client.post(
        "/crm/intakes",
        headers={"Authorization": "Bearer sandbox-read-test-token"},
        json=body,
    )
    assert prepared.status_code == 200
    assert prepared.json()["status"] == "approval_required"

    approve_body = {"expected_version": 0}
    assert client.post(
        f"/crm/intakes/{source_id}/approve",
        headers={"Authorization": "Bearer sandbox-read-test-token"},
        json=approve_body,
    ).status_code == 401
    approved = client.post(
        f"/crm/intakes/{source_id}/approve",
        headers={"Authorization": "Bearer sandbox-approval-test-token"},
        json=approve_body,
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "pending_external_confirmation"
    assert calls[0][0] == "prepare"
    assert calls[1] == ("approve", (source_id, 0, "sandbox-test-operator"))
