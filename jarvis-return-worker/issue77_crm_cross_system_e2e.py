"""Disposable cross-system E2E for issue #77. Synthetic data only; never Production."""
import asyncio
import os
from urllib.parse import urlsplit
from uuid import uuid4

import psycopg

from jarvis_return_worker.config import Config
from jarvis_return_worker.crm_intake import CRMIntakeProvider
from jarvis_return_worker.crm_intake_store import CRMIntakeAdapter, CRMIntakeStore
from jarvis_return_worker.dispatcher import TransportDispatcher
from jarvis_return_worker.events import EventBus
from jarvis_return_worker.orchestrator import Orchestrator
from jarvis_return_worker.provider import ProviderAdapter
from jarvis_return_worker.outbox import OutboxReplay
from jarvis_return_worker.queue import QueueRepository
from jarvis_return_worker.worker import CRMIntakeOperationWorker

SOURCE_ID = "jarvis:" + str(uuid4())
CORRELATION_ID = "issue77-e2e-" + str(uuid4())
SYNTHETIC_NAME = "Synthetic Issue77 E2E"
SYNTHETIC_PHONE = "000000000"
ACTOR = "synthetic-e2e-human-approval"


def require(name):
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError("missing_" + name)
    return value


def verify_crm_target(url):
    parsed = urlsplit(url)
    if (parsed.scheme not in ("postgres", "postgresql")
            or parsed.hostname != "cf7-e2e-postgres-disposable.railway.internal"
            or parsed.path != "/avtohirurg_staging"
            or parsed.query or parsed.fragment):
        raise RuntimeError("refusing_non_disposable_crm_database")


def cleanup_crm(database_url, source_id):
    pool = psycopg.connect(database_url, connect_timeout=8)
    try:
        identity = pool.execute("SELECT current_database() AS db").fetchone()[0]
        if identity != "avtohirurg_staging":
            raise RuntimeError("crm_database_identity_mismatch")
        result = pool.execute(
            """DELETE FROM inbound_leads
               WHERE source_id=%s AND customer_name=%s AND phone=%s""",
            (source_id, SYNTHETIC_NAME, SYNTHETIC_PHONE),
        )
        if result.rowcount not in (0, 1):
            raise RuntimeError("crm_synthetic_cleanup_row_count_mismatch")
        remaining = pool.execute(
            "SELECT count(*) FROM inbound_leads WHERE source_id=%s", (source_id,)
        ).fetchone()[0]
        if remaining:
            raise RuntimeError("crm_synthetic_cleanup_failed")
        pool.commit()
    finally:
        pool.close()


def cleanup_jarvis(repo, source_id):
    with repo.connection() as conn:
        rows = conn.execute(
            "SELECT event_id FROM jarvis_outbox_events WHERE causation_id=%s",
            (source_id,),
        ).fetchall()
        event_ids = [row["event_id"] for row in rows]
        if event_ids:
            conn.execute("DELETE FROM jarvis_outbox_audit WHERE event_id = ANY(%s)", (event_ids,))
            conn.execute("DELETE FROM jarvis_processed_events WHERE event_id = ANY(%s)", (event_ids,))
            conn.execute("DELETE FROM jarvis_outbox_deliveries WHERE event_id = ANY(%s)", (event_ids,))
            conn.execute("DELETE FROM jarvis_outbox_events WHERE event_id = ANY(%s)", (event_ids,))
        conn.execute("DELETE FROM jarvis_crm_intake_events WHERE source_id=%s", (source_id,))
        conn.execute("DELETE FROM jarvis_crm_intake_requests WHERE source_id=%s", (source_id,))
        remaining = conn.execute(
            "SELECT count(*) AS n FROM jarvis_crm_intake_requests WHERE source_id=%s",
            (source_id,),
        ).fetchone()["n"]
        if remaining:
            raise RuntimeError("jarvis_synthetic_cleanup_failed")


async def run_worker_until_terminal(orchestrator, store, source_id):
    stop = asyncio.Event()
    worker = CRMIntakeOperationWorker(orchestrator, stop)
    task = asyncio.create_task(worker.run())
    try:
        for _ in range(150):
            row = await asyncio.to_thread(store.get, source_id)
            if row and row["state"] in {
                "DONE", "FAILED", "RECONCILIATION_REQUIRED", "DELIVERED_CONFIRMED"
            }:
                return row
            await asyncio.sleep(0.2)
        raise TimeoutError("crm_intake_worker_e2e_timeout")
    finally:
        stop.set()
        try:
            await asyncio.wait_for(task, timeout=10)
        except TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def main():
    config = Config.from_env()
    if os.environ.get("JARVIS_TRANSPORT", "fake") != "fake":
        raise RuntimeError("only_fake_primary_transport_is_allowed")
    crm_url = require("JARVIS_CRM_INTAKE_STAGING_URL")
    crm_key = require("JARVIS_CRM_INTAKE_API_KEY")
    crm_database_url = require("CRM_E2E_DATABASE_URL")
    verify_crm_target(crm_database_url)

    repo = QueueRepository(config)
    repo.migrate()
    store = CRMIntakeStore(repo)
    payload = {
        "source_id": SOURCE_ID,
        "name": SYNTHETIC_NAME,
        "phone": SYNTHETIC_PHONE,
        "make": "Synthetic",
        "model": "E2E",
        "vehicle_year": 2020,
        "modification": "Synthetic only",
        "desired_date": "",
        "notes": "issue77 synthetic integration test",
        "symptom": "synthetic winter diagnostic symptom",
        "urgency": "soon",
        "followup_consent": False,
    }
    try:
        provider = ProviderAdapter(CRMIntakeProvider(crm_url, crm_key, timeout_seconds=8))
        dispatcher = TransportDispatcher()
        dispatcher.register("crm-intake", provider, enabled=True)
        store.prepare(payload, CORRELATION_ID, actor="synthetic-e2e-intake")
        registry = EventBus()
        CRMIntakeAdapter().register(registry)
        replay = OutboxReplay(repo, registry, owner="issue77-cross-system-e2e")
        replay.register_subscribers()
        if not replay.replay_one():
            raise RuntimeError("prepared_outbox_event_not_replayed")
        approved = store.approve(SOURCE_ID, expected_version=0, actor=ACTOR)
        if approved["state"] != "PENDING":
            raise RuntimeError("human_approval_transition_failed")

        orchestrator = Orchestrator(repo, dispatcher, event_bus=registry, intake_store=store)
        result = asyncio.run(run_worker_until_terminal(orchestrator, store, SOURCE_ID))
        if result["state"] != "DONE" or not result.get("external_lead_id"):
            raise RuntimeError("worker_dispatch_not_receipt_confirmed")

        with psycopg.connect(crm_database_url, connect_timeout=8) as pool:
            db_name = pool.execute("SELECT current_database() AS db").fetchone()[0]
            if db_name != "avtohirurg_staging":
                raise RuntimeError("crm_database_identity_mismatch")
            rows = pool.execute(
                """SELECT id,customer_name,phone FROM inbound_leads
                   WHERE source_id=%s AND customer_name=%s AND phone=%s""",
                (SOURCE_ID, SYNTHETIC_NAME, SYNTHETIC_PHONE),
            ).fetchall()
            if len(rows) != 1 or str(rows[0][0]) != str(result["external_lead_id"]):
                raise RuntimeError("crm_persisted_row_or_receipt_mismatch")
            lead_id = str(rows[0][0])
        print("ISSUE77_JARVIS_TO_CRM_E2E_PASS prepared approved outbox_replayed worker_dispatched receipt_matches_db")
    finally:
        # Always clean the exact UUID/name/phone synthetic identity, including
        # ambiguous worker outcomes where CRM may have committed before timeout.
        try:
            cleanup_crm(crm_database_url, SOURCE_ID)
        finally:
            cleanup_jarvis(repo, SOURCE_ID)
            if store.get(SOURCE_ID) is not None:
                raise RuntimeError("jarvis_synthetic_cleanup_verification_failed")

    print("ISSUE77_CROSS_SYSTEM_E2E_CLEANUP_PASS crm=1 jarvis=1")
    print("SYNTHETIC_LEAD_ID=" + lead_id)


if __name__ == "__main__":
    main()
