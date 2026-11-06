"""Safely clean only synthetic Issue 77 fixtures from disposable staging databases."""
import json
import os
from urllib.parse import urlsplit

import psycopg

from .config import Config
from .issue77_crm_cross_system_e2e import cleanup_jarvis
from .queue import QueueRepository

_CRM_HOST = "cf7-e2e-postgres-disposable.railway.internal"
_CRM_DB = "avtohirurg_staging"
_NAME_SQL = r"^Synthetic Issue77 E2E [0-9]{3}$"
_PHONE_SQL = r"^000000[0-9]{4}$"


def _require_disposable_crm(url):
    parsed = urlsplit(url or "")
    if (parsed.scheme not in ("postgres", "postgresql")
            or parsed.hostname != _CRM_HOST
            or parsed.path != "/" + _CRM_DB
            or parsed.query or parsed.fragment):
        raise RuntimeError("refusing_non_disposable_crm_database")


def _crm_plan(database_url):
    _require_disposable_crm(database_url)
    with psycopg.connect(database_url, connect_timeout=8) as db:
        identity = db.execute(
            "SELECT current_database() AS db"
        ).fetchone()[0]
        if identity != _CRM_DB:
            raise RuntimeError("crm_database_identity_mismatch")
        leads = db.execute(
            """SELECT id::text,source_id,booking_id::text
               FROM inbound_leads
               WHERE customer_name ~ %s AND phone ~ %s""",
            (_NAME_SQL, _PHONE_SQL),
        ).fetchall()
        staff = db.execute(
            "SELECT id::text FROM staff_users WHERE username LIKE 'i77e2e\\_%' ESCAPE '\\'"
        ).fetchall()
        services = db.execute(
            "SELECT id::text FROM services WHERE name LIKE 'TEST Issue77 E2E %'"
        ).fetchall()
        bays = db.execute(
            "SELECT id::text FROM work_bays WHERE name LIKE 'TEST Issue77 E2E %'"
        ).fetchall()
        source_ids = sorted({row[1] for row in leads})
        lead_ids = sorted({row[0] for row in leads})
        booking_ids = {row[2] for row in leads if row[2]}
        staff_ids = {row[0] for row in staff}
        service_ids = {row[0] for row in services}
        bay_ids = {row[0] for row in bays}
        customer_ids, vehicle_ids = set(), set()
        diagnostic_ids = set()
        if booking_ids or staff_ids:
            cases = db.execute(
                """SELECT id::text,booking_id::text
                   FROM diagnostic_cases
                   WHERE booking_id=ANY(%s::bigint[])
                      OR created_by_staff_user_id=ANY(%s::bigint[])""",
                (list(booking_ids), list(staff_ids)),
            ).fetchall()
            diagnostic_ids = {row[0] for row in cases}
            booking_ids.update(row[1] for row in cases if row[1])
        if booking_ids:
            linked = db.execute(
                """SELECT id::text,customer_id::text,vehicle_id::text
                   FROM bookings
                   WHERE id=ANY(%s::bigint[])""",
                (list(booking_ids),),
            ).fetchall()
            customer_ids.update(row[1] for row in linked if row[1])
            vehicle_ids.update(row[2] for row in linked if row[2])
        if customer_ids or vehicle_ids:
            related = db.execute(
                """SELECT id::text,customer_id::text,vehicle_id::text
                   FROM bookings
                   WHERE customer_id=ANY(%s::bigint[])
                      OR vehicle_id=ANY(%s::bigint[])""",
                (list(customer_ids), list(vehicle_ids)),
            ).fetchall()
            booking_ids.update(row[0] for row in related)
            customer_ids.update(row[1] for row in related if row[1])
            vehicle_ids.update(row[2] for row in related if row[2])
            if booking_ids:
                cases = db.execute(
                    """SELECT id::text FROM diagnostic_cases
                       WHERE booking_id=ANY(%s::bigint[])""",
                    (list(booking_ids),),
                ).fetchall()
                diagnostic_ids.update(row[0] for row in cases)
        return {
            "source_ids": source_ids, "lead_ids": lead_ids,
            "booking_ids": sorted(booking_ids), "diagnostic_ids": sorted(diagnostic_ids),
            "customer_ids": sorted(customer_ids), "vehicle_ids": sorted(vehicle_ids),
            "staff_ids": sorted(staff_ids), "service_ids": sorted(service_ids),
            "bay_ids": sorted(bay_ids),
        }


def _apply_crm_cleanup(database_url, plan):
    with psycopg.connect(database_url, connect_timeout=8) as db:
        if db.execute("SELECT current_database()").fetchone()[0] != _CRM_DB:
            raise RuntimeError("crm_database_identity_mismatch")
        booking_ids = plan["booking_ids"]
        diagnostic_ids = plan["diagnostic_ids"]
        source_ids = plan["source_ids"]
        lead_ids = plan["lead_ids"]
        staff_ids = plan["staff_ids"]
        if diagnostic_ids:
            db.execute(
                "DELETE FROM diagnostic_case_media WHERE diagnostic_case_id=ANY(%s::bigint[])",
                (diagnostic_ids,),
            )
            db.execute(
                "DELETE FROM repeat_visit_drafts WHERE diagnostic_case_id=ANY(%s::bigint[])",
                (diagnostic_ids,),
            )
            db.execute("DELETE FROM diagnostic_cases WHERE id=ANY(%s::bigint[])", (diagnostic_ids,))
        if booking_ids:
            db.execute("DELETE FROM staff_audit_events WHERE booking_id=ANY(%s::bigint[])", (booking_ids,))
            db.execute("DELETE FROM booking_status_history WHERE booking_id=ANY(%s::bigint[])", (booking_ids,))
        if staff_ids:
            db.execute("DELETE FROM staff_audit_events WHERE staff_user_id=ANY(%s::bigint[])", (staff_ids,))
        if lead_ids:
            db.execute(
                "UPDATE inbound_leads SET booking_id=NULL WHERE id=ANY(%s::bigint[])",
                (lead_ids,),
            )
        if booking_ids:
            db.execute("DELETE FROM bookings WHERE id=ANY(%s::bigint[])", (booking_ids,))
        if plan["vehicle_ids"]:
            db.execute("DELETE FROM vehicles WHERE id=ANY(%s::bigint[])", (plan["vehicle_ids"],))
        if plan["customer_ids"]:
            db.execute("DELETE FROM customers WHERE id=ANY(%s::bigint[])", (plan["customer_ids"],))
        if lead_ids:
            db.execute("DELETE FROM inbound_leads WHERE id=ANY(%s::bigint[])", (lead_ids,))
        if staff_ids:
            db.execute("DELETE FROM staff_sessions WHERE staff_user_id=ANY(%s::bigint[])", (staff_ids,))
            db.execute("DELETE FROM staff_users WHERE id=ANY(%s::bigint[])", (staff_ids,))
        if plan["service_ids"]:
            db.execute("DELETE FROM services WHERE id=ANY(%s::bigint[])", (plan["service_ids"],))
        if plan["bay_ids"]:
            db.execute("DELETE FROM work_bays WHERE id=ANY(%s::bigint[])", (plan["bay_ids"],))
        remaining = db.execute(
            """SELECT count(*) FROM inbound_leads
               WHERE customer_name ~ %s AND phone ~ %s""",
            (_NAME_SQL, _PHONE_SQL),
        ).fetchone()[0]
        if remaining:
            raise RuntimeError("crm_synthetic_lead_cleanup_incomplete")
        db.commit()


def main():
    config = Config.from_env()
    if os.environ.get("JARVIS_TRANSPORT", "fake") != "fake":
        raise RuntimeError("only_fake_primary_transport_is_allowed")
    crm_database_url = os.environ.get("CRM_E2E_DATABASE_URL", "")
    plan = _crm_plan(crm_database_url)
    repo = QueueRepository(config)
    source_ids = set(plan["source_ids"])
    with repo.connection() as conn:
        jarvis_rows = conn.execute(
            """SELECT source_id FROM jarvis_crm_intake_requests
               WHERE payload->>'name' ~ %s AND payload->>'phone' ~ %s""",
            (_NAME_SQL, _PHONE_SQL),
        ).fetchall()
        source_ids.update(row["source_id"] for row in jarvis_rows)
        jarvis_count = conn.execute(
            """SELECT count(*) AS n FROM jarvis_crm_intake_requests
               WHERE payload->>'name' ~ %s AND payload->>'phone' ~ %s""",
            (_NAME_SQL, _PHONE_SQL),
        ).fetchone()["n"]
    apply = os.environ.get("ISSUE77_CLEANUP_APPLY") == "true"
    summary = {
        "mode": "apply" if apply else "dry_run",
        "crm_leads": len(plan["lead_ids"]),
        "crm_bookings": len(plan["booking_ids"]),
        "crm_diagnostic_cases": len(plan["diagnostic_ids"]),
        "crm_customers": len(plan["customer_ids"]),
        "crm_vehicles": len(plan["vehicle_ids"]),
        "crm_fixture_staff": len(plan["staff_ids"]),
        "crm_fixture_services": len(plan["service_ids"]),
        "crm_fixture_bays": len(plan["bay_ids"]),
        "jarvis_intake_rows": jarvis_count,
        "source_ids": len(source_ids),
    }
    print("ISSUE77_SYNTHETIC_CLEANUP_PLAN " +
          json.dumps(summary, sort_keys=True, separators=(",", ":")))
    if not apply:
        print("ISSUE77_SYNTHETIC_CLEANUP_DRY_RUN_PASS")
        return
    _apply_crm_cleanup(crm_database_url, plan)
    for source_id in sorted(source_ids):
        cleanup_jarvis(repo, source_id)
    with repo.connection() as conn:
        remaining = conn.execute(
            """SELECT count(*) AS n FROM jarvis_crm_intake_requests
               WHERE payload->>'name' ~ %s AND payload->>'phone' ~ %s""",
            (_NAME_SQL, _PHONE_SQL),
        ).fetchone()["n"]
    if remaining:
        raise RuntimeError("jarvis_synthetic_cleanup_incomplete")
    print("ISSUE77_SYNTHETIC_CLEANUP_PASS crm=verified jarvis=verified")


if __name__ == "__main__":
    main()
