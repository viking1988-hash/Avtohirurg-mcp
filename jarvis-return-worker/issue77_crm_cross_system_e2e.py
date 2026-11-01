"""Disposable cross-system E2E for issue #77. Synthetic data only; never Production."""
import asyncio
import json
import os
import re
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

SYNTHETIC_NAME = "Synthetic Issue77 E2E"
ACTOR = "synthetic-e2e-human-approval"
CASE_COUNT = 100


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



def audit_staging_catalog(database_url):
    """Read-only, PII-free snapshot from the isolated staging database."""
    with psycopg.connect(database_url, connect_timeout=8) as db:
        identity = db.execute(
            "SELECT current_database() AS db, inet_server_addr()::text AS host"
        ).fetchone()
        if identity[0] != "avtohirurg_staging":
            raise RuntimeError("catalog_database_identity_mismatch")
        services = db.execute(
            """SELECT id::text, name, duration_minutes, price_from_kopecks::text
               FROM services ORDER BY name, id"""
        ).fetchall()
        bays = db.execute(
            """SELECT id::text, name FROM work_bays
               WHERE active=true ORDER BY name, id"""
        ).fetchall()
        busy = db.execute(
            """SELECT work_bay_id::text, starts_at::text, ends_at::text, status
               FROM bookings
               WHERE status IN ('new','confirmed','in_progress')
                 AND starts_at >= now()
                 AND starts_at < now() + interval '30 days'
               ORDER BY work_bay_id, starts_at"""
        ).fetchall()
    snapshot = {
        "database": "avtohirurg_staging",
        "service_count": len(services),
        "services": [
            {"id": row[0], "name": row[1], "duration_minutes": row[2],
             "price_from_kopecks": row[3]}
            for row in services
        ],
        "active_work_bays": [{"id": row[0], "name": row[1]} for row in bays],
        "busy_intervals_next_30_days": [
            {"work_bay_id": row[0], "starts_at": row[1],
             "ends_at": row[2], "status": row[3]}
            for row in busy
        ],
        "customer_data": "excluded",
    }
    print("ISSUE77_STAGING_CATALOG_SNAPSHOT " +
          json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")))


def cleanup_crm(database_url, source_id, name, phone):
    pool = psycopg.connect(database_url, connect_timeout=8)
    try:
        identity = pool.execute("SELECT current_database() AS db").fetchone()[0]
        if identity != "avtohirurg_staging":
            raise RuntimeError("crm_database_identity_mismatch")
        result = pool.execute(
            """DELETE FROM inbound_leads
               WHERE source_id=%s AND customer_name=%s AND phone=%s""",
            (source_id, name, phone),
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
    audit_staging_catalog(crm_database_url)

    repo = QueueRepository(config)
    repo.migrate()
    store = CRMIntakeStore(repo)
    provider = ProviderAdapter(CRMIntakeProvider(crm_url, crm_key, timeout_seconds=8))
    dispatcher = TransportDispatcher()
    dispatcher.register("crm-intake", provider, enabled=True)
    registry = EventBus()
    CRMIntakeAdapter().register(registry)
    replay = OutboxReplay(repo, registry, owner="issue77-cross-system-e2e")
    replay.register_subscribers()
    orchestrator = Orchestrator(repo, dispatcher, event_bus=registry, intake_store=store)
    cases = []
    verified = 0

    try:
        for index in range(1, CASE_COUNT + 1):
            source_id = "jarvis:" + str(uuid4())
            correlation_id = "issue77-e2e-" + str(uuid4())
            name = f"{SYNTHETIC_NAME} {index:03d}"
            phone = f"000000{index:04d}"
            payload = {
                "source_id": source_id,
                "name": name,
                "phone": phone,
                "make": "Geely",
                "model": "Atlas",
                "vehicle_year": 2022,
                "modification": "Synthetic trim",
                "engine": "2.0T synthetic",
                "transmission": "DCT synthetic",
                "vehicle_mileage": 42000,
                "service_history": "Synthetic service record only",
                "desired_date": "",
                "notes": "issue77 synthetic integration test",
                "symptom": "synthetic winter diagnostic symptom",
                "urgency": "soon",
                "followup_consent": False,
            }
            case = {"source_id": source_id, "name": name, "phone": phone, "payload": payload}
            cases.append(case)
            store.prepare(payload, correlation_id, actor="synthetic-e2e-intake")
            if not replay.replay_one():
                raise RuntimeError("prepared_outbox_event_not_replayed")
            approved = store.approve(source_id, expected_version=0, actor=ACTOR)
            if approved["state"] != "PENDING":
                raise RuntimeError("human_approval_transition_failed")

            result = asyncio.run(run_worker_until_terminal(orchestrator, store, source_id))
            if result["state"] != "DONE" or not result.get("external_lead_id"):
                events = store.events(source_id)
                failure_reason = next((event.get("reason", "") for event in reversed(events)
                                       if event.get("action") in ("failed", "reconciliation_required")), "")
                safe_reason = failure_reason if re.fullmatch(r"[a-zA-Z0-9_:-]{1,200}", failure_reason) else "redacted"
                raise RuntimeError("worker_dispatch_not_receipt_confirmed case="
                                   + str(index) + " state=" + str(result.get("state"))
                                   + " reason=" + safe_reason)

            duplicate = provider.create_lead(payload)
            if str(duplicate["lead_id"]) != str(result["external_lead_id"]):
                raise RuntimeError("crm_exact_replay_lead_id_mismatch case=" + str(index))

            with psycopg.connect(crm_database_url, connect_timeout=8) as pool:
                db_name = pool.execute("SELECT current_database() AS db").fetchone()[0]
                if db_name != "avtohirurg_staging":
                    raise RuntimeError("crm_database_identity_mismatch")
                rows = pool.execute(
                    """SELECT id,customer_name,phone,make,vehicle_model,vehicle_year,modification,
                              vehicle_engine,vehicle_transmission,vehicle_mileage,service_history
                       FROM inbound_leads
                       WHERE source_id=%s AND customer_name=%s AND phone=%s""",
                    (source_id, name, phone),
                ).fetchall()
                expected = (
                    str(result["external_lead_id"]), name, phone,
                    "Geely", "Atlas", 2022, "Synthetic trim", "2.0T synthetic",
                    "DCT synthetic", 42000, "Synthetic service record only",
                )
                if len(rows) != 1:
                    raise RuntimeError("crm_persisted_row_count_mismatch case="
                                       + str(index) + " count=" + str(len(rows)))
                actual = tuple(rows[0])
                fields = (
                    "id", "customer_name", "phone", "make", "vehicle_model", "vehicle_year",
                    "modification", "vehicle_engine", "vehicle_transmission",
                    "vehicle_mileage", "service_history",
                )
                mismatched = []
                if str(actual[0]) != expected[0]:
                    mismatched.append("id")
                mismatched.extend(
                    field for field, got, want in zip(fields[1:], actual[1:], expected[1:])
                    if got != want
                )
                if mismatched:
                    raise RuntimeError("crm_persisted_row_field_mismatch case="
                                       + str(index) + " fields=" + ",".join(mismatched))
            verified += 1

        if verified != CASE_COUNT:
            raise RuntimeError("cross_system_case_count_mismatch verified=" + str(verified))
        print("ISSUE77_JARVIS_TO_CRM_E2E_PASS cases=100 approved outbox_replayed "
              "worker_dispatched exact_replays=100 receipt_and_vehicle_context_readback=100")
    finally:
        cleanup_failures = 0
        for case in cases:
            try:
                cleanup_crm(crm_database_url, case["source_id"], case["name"], case["phone"])
            except Exception:
                cleanup_failures += 1
            try:
                cleanup_jarvis(repo, case["source_id"])
            except Exception:
                cleanup_failures += 1
            try:
                if store.get(case["source_id"]) is not None:
                    cleanup_failures += 1
            except Exception:
                cleanup_failures += 1
        if cleanup_failures:
            raise RuntimeError("synthetic_cleanup_verification_failed failures="
                               + str(cleanup_failures))

    print("ISSUE77_CROSS_SYSTEM_E2E_CLEANUP_PASS crm=100 jarvis=100")


if __name__ == "__main__":
    main()
