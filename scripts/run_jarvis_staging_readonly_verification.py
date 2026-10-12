"""Read-only acceptance evidence for Jarvis Return Queue staging.
No DDL/DML, no external calls, no customer data. Executes reviewed SQL file.
"""
import os
import json
from pathlib import Path
from urllib.parse import quote
import psycopg

PROJECT = "73400534-5ebc-4619-86d9-f036e1028db9"
ENV = "96dc8ace-8884-47b1-b529-ee2c317c7904"
DB = "jarvis_return_queue_test"
USER = "jarvis_sandbox"
HOST = "jarvis-sandbox-postgres.railway.internal"
SQL_PATH = Path(__file__).resolve().parents[1] / "docs/sql/jarvis-return-queue-staging-readonly-verification.sql"

def main():
    if (os.getenv("RAILWAY_PROJECT_ID"), os.getenv("RAILWAY_ENVIRONMENT_ID"),
        os.getenv("RAILWAY_SERVICE_NAME")) != (
        PROJECT, ENV, "jarvis-return-queue-readonly-verifier"):
        raise RuntimeError("FAIL: incorrect project, environment or service")
    pw = os.getenv("JARVIS_SANDBOX_PASSWORD", "")
    if not pw:
        raise RuntimeError("FAIL: sandbox credential missing")
    url = "postgresql://" + USER + ":" + quote(pw, safe="") + "@" + HOST + ":5432/" + DB
    sql = SQL_PATH.read_text(encoding="utf-8")
    if not sql.startswith("-- Jarvis Return Queue: read-only Railway staging verification."):
        raise RuntimeError("FAIL: unreviewed SQL")
    # Parse only statements from the reviewed SQL; comments do not execute.
    cleaned = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    statements = [x.strip() for x in cleaned.split(";") if x.strip()]
    if not statements or statements[0] != "BEGIN READ ONLY" or statements[-1] != "COMMIT":
        raise RuntimeError("FAIL: SQL is not a read-only transaction")
    if any(not x.lstrip().upper().startswith(("BEGIN READ ONLY", "SELECT ", "COMMIT"))
           for x in statements):
        raise RuntimeError("FAIL: non-read-only statement")
    with psycopg.connect(url, connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database(), current_user, current_setting('server_version_num')::int")
            db, role, ver = cur.fetchone()
            if (db, role) != (DB, USER) or not (180000 <= ver < 190000):
                raise RuntimeError("FAIL: server identity or PostgreSQL 18 mismatch")
            print("PASS: private PostgreSQL 18, expected DB and role", flush=True)
            results = []
            for i, statement in enumerate(statements):
                cur.execute(statement)
                if cur.description:
                    rows = cur.fetchall()
                    cols = [c.name for c in cur.description]
                    # Avoid logging unrelated data; the reviewed file only selects schema metadata.
                    results.append((cols, rows))
                    print("SQL_RESULT " + str(i) + " " + json.dumps(
                        {"columns":cols, "rows":rows}, default=str, ensure_ascii=False), flush=True)
            assert len(results) == 4, "Unexpected SQL result count"
            tables = results[1][1][0]
            assert tables == ("jarvis_return_actions", "jarvis_return_action_events"), tables
            indexes = results[2][1]
            assert len(indexes) == 1 and indexes[0][2] == "jarvis_return_action_events_task_time_idx"
            constraints = results[3][1]
            assert any("approved_by" in str(row[3]) and "approved_fingerprint" in str(row[3]) for row in constraints), "approval constraint absent"
            assert any("delivery_receipt" in str(row[3]) for row in constraints), "receipt constraint absent"
            print("PASS: 2 tables, index, APPROVAL and DONE receipt constraints", flush=True)
            # Supplemental queries are read-only and restricted to synthetic smoke IDs.
            cur.execute("BEGIN READ ONLY")
            cur.execute("""SELECT task_id,state,version,attempt_count,lease_owner,approved_by
                FROM public.jarvis_return_actions
                WHERE task_id LIKE 'sandbox_return_queue_smoke_20261010%' ORDER BY task_id""")
            actions = cur.fetchall()
            print("SMOKE_ACTIONS " + json.dumps(actions, default=str), flush=True)
            cur.execute("""SELECT task_id,from_state,to_state,actor,correlation_id,event_time
                FROM public.jarvis_return_action_events
                WHERE task_id LIKE 'sandbox_return_queue_smoke_20261010%'
                ORDER BY event_id""")
            events = cur.fetchall()
            print("SMOKE_EVENTS " + json.dumps(events, default=str), flush=True)
            cur.execute("COMMIT")
            assert len(actions) == 1 and actions[0][1] == "PROCESSING" and actions[0][3] == 1, "smoke action mismatch"
            assert len(events) == 2 and [r[2] for r in events] == ["APPROVED", "PROCESSING"], "event journal mismatch"
            print("PASS: smoke action persists, one claim, two audit events", flush=True)
    print("PASS: read-only SQL verification complete", flush=True)

if __name__ == "__main__":
    main()
