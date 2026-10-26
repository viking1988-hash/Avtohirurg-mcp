import asyncio
import os
import uuid
from types import SimpleNamespace

import pytest

from jarvis_return_worker.crm_intake_store import CRMIntakeStore
from jarvis_return_worker.queue import Conflict, QueueRepository
from jarvis_return_worker.dispatcher import TransportDispatcher
from jarvis_return_worker.orchestrator import Orchestrator


@pytest.fixture(scope="module")
def repository():
    url = os.environ.get("JARVIS_WORKER_CI_DATABASE_URL")
    if not url:
        pytest.skip("PostgreSQL integration URL is not configured")
    repo = QueueRepository(SimpleNamespace(
        database_url=url,
        worker_id="crm-intake-tests",
        lease_seconds=30,
        heartbeat_seconds=5,
        max_attempts=3,
    ))
    repo.migrate()
    return repo


def test_intake_is_durable_idempotent_and_requires_human_approval(repository):
    source_id = "jarvis:" + str(uuid.uuid4())
    correlation_id = "synthetic-intake-" + str(uuid.uuid4())
    body = {
        "source_id": source_id,
        "name": "Synthetic customer",
        "phone": "000000000",
        "make": "Synthetic",
        "model": "Test",
        "vehicle_year": 2020,
        "modification": "Synthetic trim",
        "desired_date": "",
        "notes": "synthetic CI only",
        "symptom": "synthetic vibration",
        "urgency": "soon",
        "followup_consent": False,
    }
    store = CRMIntakeStore(repository)
    try:
        first = store.prepare(body, correlation_id, "synthetic-ci")
        assert first["state"] == "PREPARED"
        assert first["status"] == "approval_required"
        assert first["deduplicated"] is False

        replay = store.prepare(body, correlation_id, "synthetic-ci")
        assert replay["source_id"] == source_id
        assert replay["deduplicated"] is True
        assert replay["version"] == first["version"]

        with pytest.raises(Conflict):
            store.prepare({**body, "symptom": "changed synthetic symptom"},
                          correlation_id, "synthetic-ci")

        assert store.claim("synthetic-worker") is None
        approved = store.approve(source_id, first["version"], "synthetic-human")
        assert approved["state"] == "PENDING"
        assert approved["status"] == "pending_external_confirmation"
        assert approved["version"] == first["version"] + 1

        with pytest.raises(Conflict):
            store.approve(source_id, first["version"], "synthetic-human")

        operation = store.claim("synthetic-worker")
        assert operation["state"] == "PROCESSING"
        calls = []

        class FakeCRMIntake:
            provider = "crm-intake"
            def send(self, payload, idempotency_key):
                calls.append((payload, idempotency_key))
                return {"lead_id": 501, "receipt_id": "synthetic-receipt-501", "status": "new"}

            def create_lead(self, payload):
                calls.append((payload, "synthetic-idempotency-key"))
                return {"lead_id": 501, "receipt_id": "synthetic-receipt-501", "status": "new"}

        dispatcher = TransportDispatcher()
        dispatcher.register("crm-intake", FakeCRMIntake())
        orchestrator = Orchestrator(repository, dispatcher, intake_store=store)
        asyncio.run(orchestrator.process_crm_intake_operation(operation, "synthetic-worker"))
        assert len(calls) == 1
        assert calls[0][0]["source_id"] == source_id

        readback = store.get(source_id)
        assert readback["state"] == "DONE"
        assert readback["external_lead_id"] == "501"
        assert "phone" not in readback and "name" not in readback
        events = store.events(source_id)
        assert [event["action"] for event in events] == ["prepared", "approved", "claimed", "completed"]
        assert all("phone" not in event and "name" not in event for event in events)
    finally:
        with repository.connection() as conn:
            conn.execute("DELETE FROM jarvis_crm_intake_events WHERE source_id=%s", (source_id,))
            conn.execute("DELETE FROM jarvis_crm_intake_requests WHERE source_id=%s", (source_id,))
        assert store.get(source_id) is None


def test_unknown_crm_outcome_is_held_for_human_reconciliation(repository):
    source_id = "jarvis:" + str(uuid.uuid4())
    correlation_id = "synthetic-unknown-" + str(uuid.uuid4())
    body = {
        "source_id": source_id,
        "name": "Synthetic customer",
        "phone": "000000000",
        "symptom": "synthetic timeout case",
        "followup_consent": False,
    }
    store = CRMIntakeStore(repository)
    try:
        prepared = store.prepare(body, correlation_id, "synthetic-ci")
        store.approve(source_id, prepared["version"], "synthetic-human")
        operation = store.claim("synthetic-unknown-worker")
        calls = []

        class UnknownCRMIntake:
            provider = "crm-intake"
            def send(self, payload, idempotency_key):
                calls.append((payload, idempotency_key))
                from jarvis_return_worker.transport import Classification, DeliveryError
                raise DeliveryError(Classification.UNKNOWN, "synthetic_timeout")

            def create_lead(self, payload):
                calls.append((payload, "synthetic-idempotency-key"))
                from jarvis_return_worker.transport import Classification, DeliveryError
                raise DeliveryError(Classification.UNKNOWN, "synthetic_timeout")

        dispatcher = TransportDispatcher()
        dispatcher.register("crm-intake", UnknownCRMIntake())
        orchestrator = Orchestrator(repository, dispatcher, intake_store=store)
        asyncio.run(orchestrator.process_crm_intake_operation(operation, "synthetic-unknown-worker"))

        assert len(calls) == 1
        saved = store.get_operation(source_id)
        assert saved["state"] == "RECONCILIATION_REQUIRED"
        assert saved["reconciliation_reason"] == "synthetic_timeout"
        assert store.claim("synthetic-unknown-worker") is None

        reconciled = store.reconcile(
            source_id, saved["version"], "delivered",
            "synthetic operator verified CRM record", "synthetic-human", "501",
        )
        assert reconciled["state"] == "DELIVERED_CONFIRMED"
        assert reconciled["status"] == "crm_lead_saved_operator_confirmed"
        final = store.get_operation(source_id)
        assert final["provider_receipt"] is None
        assert final["reconciled_by"] == "synthetic-human"
        assert [event["action"] for event in store.events(source_id)] == [
            "prepared", "approved", "claimed", "reconciliation_required",
            "reconciled_delivered"
        ]
    finally:
        with repository.connection() as conn:
            conn.execute("DELETE FROM jarvis_crm_intake_events WHERE source_id=%s", (source_id,))
            conn.execute("DELETE FROM jarvis_crm_intake_requests WHERE source_id=%s", (source_id,))
