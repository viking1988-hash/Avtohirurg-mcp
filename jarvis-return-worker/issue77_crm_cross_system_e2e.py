"""Disposable cross-system E2E for issue #77. Synthetic data only; never Production."""
import asyncio
import hashlib
import struct
import urllib.error
import urllib.request
import zlib
from datetime import datetime, timedelta, timezone
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


def cleanup_crm(database_url, case):
    with psycopg.connect(database_url, connect_timeout=8) as db:
        if db.execute("SELECT current_database()").fetchone()[0] != "avtohirurg_staging":
            raise RuntimeError("crm_database_identity_mismatch")
        source_id = case["source_id"]
        lead_rows = db.execute(
            """SELECT id,booking_id FROM inbound_leads
               WHERE source_id=%s AND customer_name=%s AND phone=%s""",
            (source_id, case["name"], case["phone"]),
        ).fetchall()
        booking_ids = {str(row[1]) for row in lead_rows if row[1] is not None}
        if case.get("booking_id"):
            booking_ids.add(str(case["booking_id"]))
        if booking_ids:
            ids = list(booking_ids)
            diagnostic_ids = [str(row[0]) for row in db.execute(
                "SELECT id FROM diagnostic_cases WHERE booking_id=ANY(%s::bigint[])", (ids,)
            ).fetchall()]
            if diagnostic_ids:
                db.execute("DELETE FROM diagnostic_case_media WHERE diagnostic_case_id=ANY(%s::bigint[])", (diagnostic_ids,))
                db.execute("DELETE FROM repeat_visit_drafts WHERE diagnostic_case_id=ANY(%s::bigint[])", (diagnostic_ids,))
                db.execute("DELETE FROM diagnostic_cases WHERE id=ANY(%s::bigint[])", (diagnostic_ids,))
            db.execute("DELETE FROM staff_audit_events WHERE booking_id=ANY(%s::bigint[])", (ids,))
            db.execute("DELETE FROM booking_status_history WHERE booking_id=ANY(%s::bigint[])", (ids,))
            db.execute("UPDATE inbound_leads SET booking_id=NULL WHERE source_id=%s", (source_id,))
            linked = db.execute(
                "SELECT DISTINCT customer_id,vehicle_id FROM bookings WHERE id=ANY(%s::bigint[])", (ids,)
            ).fetchall()
            vehicles = list({str(row[1]) for row in linked if row[1] is not None})
            customers = list({str(row[0]) for row in linked if row[0] is not None})
            db.execute("DELETE FROM bookings WHERE id=ANY(%s::bigint[])", (ids,))
            if vehicles:
                db.execute("DELETE FROM vehicles WHERE id=ANY(%s::bigint[])", (vehicles,))
            if customers:
                db.execute("DELETE FROM customers WHERE id=ANY(%s::bigint[])", (customers,))
        db.execute(
            "DELETE FROM inbound_leads WHERE source_id=%s AND customer_name=%s AND phone=%s",
            (source_id, case["name"], case["phone"]),
        )
        if db.execute("SELECT count(*) FROM inbound_leads WHERE source_id=%s", (source_id,)).fetchone()[0]:
            raise RuntimeError("crm_synthetic_lead_cleanup_failed")
        if booking_ids and db.execute(
            "SELECT count(*) FROM bookings WHERE id=ANY(%s::bigint[])", (list(booking_ids),)
        ).fetchone()[0]:
            raise RuntimeError("crm_synthetic_booking_cleanup_failed")


def cleanup_crm_fixtures(database_url, fixture):
    if not fixture:
        return
    with psycopg.connect(database_url, connect_timeout=8) as db:
        if db.execute("SELECT current_database()").fetchone()[0] != "avtohirurg_staging":
            raise RuntimeError("crm_database_identity_mismatch")
        staff_id, service_id, bay_id = fixture.get("staff_id"), fixture.get("service_id"), fixture.get("bay_id")
        if staff_id:
            db.execute("DELETE FROM staff_audit_events WHERE staff_user_id=%s", (staff_id,))
            db.execute("DELETE FROM staff_sessions WHERE staff_user_id=%s", (staff_id,))
            db.execute("DELETE FROM staff_users WHERE id=%s", (staff_id,))
            if db.execute("SELECT count(*) FROM staff_users WHERE id=%s", (staff_id,)).fetchone()[0]:
                raise RuntimeError("crm_synthetic_staff_cleanup_failed")
        if service_id:
            db.execute("DELETE FROM services WHERE id=%s", (service_id,))
            if db.execute("SELECT count(*) FROM services WHERE id=%s", (service_id,)).fetchone()[0]:
                raise RuntimeError("crm_synthetic_service_cleanup_failed")
        if bay_id:
            db.execute("DELETE FROM work_bays WHERE id=%s", (bay_id,))
            if db.execute("SELECT count(*) FROM work_bays WHERE id=%s", (bay_id,)).fetchone()[0]:
                raise RuntimeError("crm_synthetic_bay_cleanup_failed")


def http_json(url, method="GET", payload=None, headers=None, raw_body=None, expected=(200,)):
    request_headers = dict(headers or {})
    body = raw_body
    if payload is not None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(url, data=body, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as error:
        status, raw = error.code, error.read()
    except Exception as error:
        raise RuntimeError("crm_lifecycle_http_transport_failed") from error
    if status not in expected:
        raise RuntimeError("crm_lifecycle_unexpected_http_status:" + str(status))
    try:
        decoded = json.loads(raw.decode("utf-8")) if raw else {}
    except Exception as error:
        raise RuntimeError("crm_lifecycle_invalid_json_response") from error
    return status, decoded


def synthetic_png():
    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xffffffff)
    header = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00\xff"))
            + chunk(b"IEND", b""))


def prepare_crm_lifecycle(database_url, crm_url):
    parsed = urlsplit(crm_url)
    public_host = "cf7-pr73-api-isolated-staging.up.railway.app"
    internal_host = "cf7-pr73-api-isolated.railway.internal"
    allowed_target = (
        parsed.hostname == public_host and parsed.scheme == "https"
        or parsed.hostname == internal_host and parsed.scheme in ("http", "https")
    )
    if (not allowed_target or parsed.path.rstrip("/") not in ("", "/api/v1/leads")
            or parsed.query or parsed.fragment):
        raise RuntimeError("refusing_non_isolated_crm_target"
                           + "_scheme=" + parsed.scheme
                           + "_host=" + str(parsed.hostname)
                           + "_path=" + parsed.path)
    origin = os.environ.get("CRM_PUBLIC_ORIGIN", "https://" + public_host)
    if origin != "https://" + public_host:
        raise RuntimeError("crm_lifecycle_origin_mismatch")
    base = parsed.scheme + "://" + parsed.netloc
    expected_host = public_host
    suffix = uuid4().hex[:12]
    username, password = "i77e2e_" + suffix, "synthetic-issue77-e2e-password"
    salt = os.urandom(16)
    password_hash = "scrypt$v1$" + salt.hex() + "$" + hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=64, maxmem=64 * 1024 * 1024
    ).hex()
    fixture = {"base": base, "origin": origin, "username": username, "expected_host": public_host, "staff_id": None,
               "service_id": None, "bay_id": None, "cookie": None, "token": None}
    try:
        with psycopg.connect(database_url, connect_timeout=8) as db:
            if db.execute("SELECT current_database()").fetchone()[0] != "avtohirurg_staging":
                raise RuntimeError("crm_database_identity_mismatch")
            fixture["staff_id"] = str(db.execute(
                "INSERT INTO staff_users(username,password_hash,role) VALUES (%s,%s,'dispatcher') RETURNING id",
                (username, password_hash),
            ).fetchone()[0])
            fixture["service_id"] = str(db.execute(
                "INSERT INTO services(name,duration_minutes,price_from_kopecks) VALUES (%s,60,0) RETURNING id",
                ("TEST Issue77 E2E " + suffix,),
            ).fetchone()[0])
            fixture["bay_id"] = str(db.execute(
                "INSERT INTO work_bays(name) VALUES (%s) RETURNING id", ("TEST Issue77 E2E " + suffix,)
            ).fetchone()[0])
        request = urllib.request.Request(
            base + "/api/v1/staff/login",
            data=json.dumps({"username": username, "password": password}).encode("utf-8"),
            headers={"Origin": origin, "Host": expected_host, "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                if response.status != 200:
                    raise RuntimeError("crm_lifecycle_login_failed")
                cookie_header = response.headers.get("Set-Cookie", "")
                login = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            raise RuntimeError("crm_lifecycle_login_http_" + str(error.code))
        token_match = re.search(r"__Host-crm_session=([^;]+)", cookie_header)
        if not token_match or str(login.get("user", {}).get("id")) != fixture["staff_id"]:
            raise RuntimeError("crm_lifecycle_staff_session_missing")
        fixture["cookie"] = "__Host-crm_session=" + token_match.group(1)
        fixture["token"] = token_match.group(1)
        _, session = http_json(base + "/api/v1/staff/me", headers={"Cookie": fixture["cookie"]})
        if str(session.get("user", {}).get("id")) != fixture["staff_id"]:
            raise RuntimeError("crm_lifecycle_staff_session_mismatch")
        return fixture
    except Exception:
        cleanup_crm_fixtures(database_url, fixture)
        raise


def exercise_crm_lifecycle(database_url, fixture, case, index):
    lead_id = str(case["lead_id"])
    base, origin, cookie = fixture["base"], fixture["origin"], fixture["cookie"]
    session_headers = {"Origin": origin, "Host": fixture["expected_host"], "Cookie": cookie, "Content-Type": "application/json"}
    read_headers = {"Origin": origin, "Host": fixture["expected_host"], "Cookie": cookie, "Content-Type": "application/json"}
    start = datetime(2099, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=(index - 1) * 90)
    booking_input = {
        "service_id": fixture["service_id"], "work_bay_id": fixture["bay_id"],
        "starts_at": start.isoformat().replace("+00:00", "Z"),
        "ends_at": (start + timedelta(minutes=60)).isoformat().replace("+00:00", "Z"),
    }
    booking_url = base + "/api/v1/desk/leads/" + lead_id + "/booking"
    if index == 1:
        _, denied = http_json(
            booking_url, "POST", booking_input,
            {"Origin": origin, "Content-Type": "application/json"}, expected=(401,),
        )
        if denied.get("error") != "unauthorized":
            raise RuntimeError("crm_lifecycle_unauthorized_booking_not_denied")
    _, converted = http_json(booking_url, "POST", booking_input, session_headers, expected=(201,))
    booking = converted.get("booking", {})
    if booking.get("status") != "new" or not booking.get("id"):
        raise RuntimeError("crm_lifecycle_booking_draft_invalid")
    case["booking_id"] = str(booking["id"])
    confirm_url = base + "/api/v1/dispatcher/bookings/" + case["booking_id"] + "/confirm"
    if index == 1:
        _, denied = http_json(
            confirm_url, "POST", {"requestId": "issue77-fake"},
            {"Authorization": "Bearer " + "x" * 43, "Content-Type": "application/json"},
            expected=(401,),
        )
        if denied.get("error") != "unauthorized":
            raise RuntimeError("crm_lifecycle_fake_bearer_not_denied")
    _, confirmed_booking = http_json(
        confirm_url, "POST", {"requestId": "issue77-cross-" + case["source_id"].split(":")[1]},
        {"Authorization": "Bearer " + fixture["token"], "Content-Type": "application/json"},
        expected=(200,),
    )
    if not confirmed_booking.get("confirmed"):
        raise RuntimeError("crm_lifecycle_booking_confirmation_failed")
    report = {
        "symptom": "Synthetic Issue77 diagnostic symptom " + str(index),
        "urgency": "routine",
        "facts": [{"text": "Synthetic condition reproduced in isolated E2E", "source": "functional_test"}],
        "measurements": [{"name": "Synthetic measurement", "value": index,
                          "unit": "unit", "method": "Synthetic test fixture"}],
        "evidence": [], "confirmed_faults": [],
        "recommendations": ["Repeat synthetic inspection after 30 days"],
        "confidence_limits": ["Synthetic evidence does not describe a real vehicle"],
    }
    _, created = http_json(
        base + "/api/v1/desk/bookings/" + case["booking_id"] + "/diagnostic-case", "POST",
        {"report": report}, session_headers, expected=(201,),
    )
    diagnostic = created.get("diagnostic_case", {})
    if not diagnostic.get("id"):
        raise RuntimeError("crm_lifecycle_diagnostic_case_missing")
    case["diagnostic_case_id"] = str(diagnostic["id"])
    _, uploaded = http_json(
        base + "/api/v1/desk/diagnostic-cases/" + case["diagnostic_case_id"] + "/evidence",
        "POST", headers={"Origin": origin, "Host": fixture["expected_host"], "Cookie": cookie, "Content-Type": "image/png",
                         "X-Diagnostic-Media-Consent": "attested"},
        raw_body=synthetic_png(), expected=(201,),
    )
    evidence, diagnostic = uploaded.get("media", {}), uploaded.get("diagnostic_case", {})
    if not evidence.get("id") or not diagnostic.get("version"):
        raise RuntimeError("crm_lifecycle_synthetic_evidence_missing")
    linked_report = dict(report)
    linked_report["evidence"] = [{"id": evidence["id"], "kind": "photo",
                                  "sha256": evidence["sha256"], "caption": evidence["caption"]}]
    linked_report["confirmed_faults"] = [{
        "text": "Synthetic fault linked to stored test evidence", "evidence_ids": [evidence["id"]]
    }]
    _, patched = http_json(
        base + "/api/v1/desk/diagnostic-cases/" + case["diagnostic_case_id"], "PATCH",
        {"expected_version": diagnostic["version"], "report": linked_report},
        session_headers, expected=(200,),
    )
    diagnostic = patched.get("diagnostic_case", {})
    if patched.get("report_status") != "ready_for_specialist_review" or not diagnostic.get("version"):
        raise RuntimeError("crm_lifecycle_report_review_state_invalid")
    _, human_confirmed = http_json(
        base + "/api/v1/desk/diagnostic-cases/" + case["diagnostic_case_id"] + "/confirm", "POST",
        {"expected_version": diagnostic["version"]}, session_headers, expected=(200,),
    )
    diagnostic = human_confirmed.get("diagnostic_case", {})
    if diagnostic.get("status") != "confirmed" or human_confirmed.get("repair_authorized") is not False:
        raise RuntimeError("crm_lifecycle_report_confirmation_invalid")
    repeat_path = base + "/api/v1/desk/diagnostic-cases/" + case["diagnostic_case_id"] + "/repeat-visit-draft"
    _, repeat_state = http_json(repeat_path, headers=read_headers)
    if repeat_state.get("consent_available") is not True:
        raise RuntimeError("crm_lifecycle_repeat_consent_missing")
    due_at = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat().replace("+00:00", "Z")
    repeat_payload = {
        "case_version": diagnostic["version"], "due_at": due_at,
        "recommendation": report["recommendations"][0],
        "idempotency_key": "issue77-cross-repeat-" + case["source_id"].split(":")[1],
    }
    _, repeat_result = http_json(repeat_path, "POST", repeat_payload, session_headers, expected=(200, 201))
    draft = repeat_result.get("repeat_visit_draft", {})
    if draft.get("status") != "draft" or repeat_result.get("message_sent") is not False:
        raise RuntimeError("crm_lifecycle_repeat_draft_invalid")
    case["repeat_visit_id"] = str(draft["id"])
    _, replay = http_json(repeat_path, "POST", repeat_payload, session_headers, expected=(200,))
    replay_draft = replay.get("repeat_visit_draft", {})
    if str(replay_draft.get("id")) != case["repeat_visit_id"]:
        raise RuntimeError("crm_lifecycle_repeat_idempotency_failed")
    _, readback = http_json(repeat_path, headers=read_headers)
    saved = readback.get("repeat_visit_draft", {})
    if str(saved.get("id")) != case["repeat_visit_id"] or saved.get("recommendation") != repeat_payload["recommendation"]:
        raise RuntimeError("crm_lifecycle_repeat_draft_readback_mismatch")
    with psycopg.connect(database_url, connect_timeout=8) as db:
        persisted = db.execute(
            """SELECT b.status,dc.status,rv.status,dc.report
               FROM bookings b JOIN diagnostic_cases dc ON dc.booking_id=b.id
               JOIN repeat_visit_drafts rv ON rv.diagnostic_case_id=dc.id WHERE b.id=%s""",
            (case["booking_id"],),
        ).fetchone()
        if not persisted or persisted[0] != "confirmed" or persisted[1] != "confirmed" or persisted[2] != "draft":
            raise RuntimeError("crm_lifecycle_persisted_state_mismatch")
        if persisted[3].get("publication_allowed") is not False or persisted[3].get("repair_assignment") is not None:
            raise RuntimeError("crm_lifecycle_authority_boundary_mismatch")
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
    crm_fixture = None

    try:
        crm_fixture = prepare_crm_lifecycle(crm_database_url, crm_url)
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
                "followup_consent": True,
            }
            case = {"source_id": source_id, "name": name, "phone": phone, "payload": payload, "booking_id": None, "diagnostic_case_id": None, "repeat_visit_id": None}
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
            case["lead_id"] = str(result["external_lead_id"])
            exercise_crm_lifecycle(crm_database_url, crm_fixture, case, index)
            verified += 1

        if verified != CASE_COUNT:
            raise RuntimeError("cross_system_case_count_mismatch verified=" + str(verified))
        print("ISSUE77_JARVIS_TO_CRM_E2E_PASS cases=100 approved outbox_replayed "
              "worker_dispatched exact_replays=100 receipt_and_vehicle_context_readback=100")
        print("ISSUE77_FULL_JARVIS_CRM_LIFECYCLE_E2E_PASS cases=100 staff_booking_confirmed "
              "evidence_report_human_confirmed repeat_visit_draft")
    finally:
        cleanup_failures = 0
        for case in cases:
            try:
                cleanup_crm(crm_database_url, case)
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
        try:
            cleanup_crm_fixtures(crm_database_url, crm_fixture)
        except Exception:
            cleanup_failures += 1
        if cleanup_failures:
            raise RuntimeError("synthetic_cleanup_verification_failed failures="
                               + str(cleanup_failures))

    print("ISSUE77_CROSS_SYSTEM_E2E_CLEANUP_PASS crm=100 jarvis=100 lifecycle=100")


if __name__ == "__main__":
    main()
