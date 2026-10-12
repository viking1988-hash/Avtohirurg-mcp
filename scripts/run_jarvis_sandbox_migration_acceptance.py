"""Private staging-only PostgreSQL schema acceptance runner. No external sends."""
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

import psycopg

DB = "jarvis_return_queue_test"
USER = "jarvis_sandbox"
HOST = "jarvis-sandbox-postgres.railway.internal"
TASK = "sandbox_return_queue_smoke_20261010"
FINGERPRINT = "a" * 64
ROOT = Path(__file__).resolve().parents[1]


def checked_url():
    if os.environ.get("RAILWAY_PROJECT_ID") != "73400534-5ebc-4619-86d9-f036e1028db9":
        raise RuntimeError("Wrong Railway project")
    if os.environ.get("RAILWAY_ENVIRONMENT_ID") != "96dc8ace-8884-47b1-b529-ee2c317c7904":
        raise RuntimeError("Wrong Railway environment")
    if os.environ.get("RAILWAY_SERVICE_NAME") != "jarvis-return-queue-migration-runner":
        raise RuntimeError("Wrong runner service")
    password = os.environ.get("JARVIS_SANDBOX_PASSWORD", "")
    if not password:
        raise RuntimeError("Sandbox password not configured")
    url = "postgresql://" + USER + ":" + quote(password, safe="") + "@" + HOST + ":5432/" + DB
    os.environ["JARVIS_SANDBOX_DATABASE_URL"] = url
    os.environ["JARVIS_SANDBOX_DATABASE_NAME"] = DB
    return url


def connect(url):
    conn = psycopg.connect(url, connect_timeout=10)
    with conn.cursor() as cur:
        cur.execute("SELECT current_database(), current_user, current_setting('server_version_num')::int")
        db, user, version = cur.fetchone()
        if (db, user) != (DB, USER) or version < 180000 or version >= 190000:
            conn.close()
            raise RuntimeError("Server-side identity/version mismatch")
    return conn


def verify_schema(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT tablename FROM pg_tables WHERE schemaname=current_schema() AND tablename IN ('jarvis_return_actions','jarvis_return_action_events')")
        assert {r[0] for r in cur.fetchall()} == {"jarvis_return_actions", "jarvis_return_action_events"}
        cur.execute("SELECT indexname FROM pg_indexes WHERE schemaname=current_schema() AND indexname='jarvis_return_action_events_task_time_idx'")
        assert cur.fetchone() is not None
    print("PASS: PostgreSQL 18, sandbox identity, 2 tables, event index", flush=True)


def must_fail(conn, sql, params, label):
    with conn.cursor() as cur:
        cur.execute("SAVEPOINT negative_test")
        try:
            cur.execute(sql, params)
        except psycopg.errors.CheckViolation if label.startswith("approval") or label.startswith("receipt") else psycopg.errors.UniqueViolation:
            cur.execute("ROLLBACK TO SAVEPOINT negative_test")
            cur.execute("RELEASE SAVEPOINT negative_test")
            print("PASS: " + label, flush=True)
            return
        except Exception:
            cur.execute("ROLLBACK TO SAVEPOINT negative_test")
            cur.execute("RELEASE SAVEPOINT negative_test")
            raise
        cur.execute("ROLLBACK TO SAVEPOINT negative_test")
        cur.execute("RELEASE SAVEPOINT negative_test")
        raise AssertionError("Constraint did not reject: " + label)


def claim(url, owner):
    with connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute("""UPDATE jarvis_return_actions
                SET state='PROCESSING', lease_owner=%s,
                    lease_until=now()+interval '60 seconds',
                    attempt_count=attempt_count+1, version=version+1,
                    updated_at=now()
                WHERE task_id=%s AND state='APPROVED'
                  AND approved_fingerprint=%s
                  AND action_fingerprint=%s
                  AND approval_expires_at>now()
                RETURNING task_id""", (owner, TASK, FINGERPRINT, FINGERPRINT))
            return len(cur.fetchall())


def first_run(url):
    subprocess.run([sys.executable, str(ROOT / "scripts/apply_jarvis_sandbox_schema.py"), "--apply"],
                   check=True, cwd=str(ROOT), timeout=40)
    with connect(url) as conn:
        verify_schema(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM jarvis_return_actions WHERE task_id=%s", (TASK,))
            if cur.fetchone():
                raise RuntimeError("Unexpected existing smoke task; refusing to overwrite")
            cur.execute("""INSERT INTO jarvis_return_actions
                (task_id, action_fingerprint, destination_key, state)
                VALUES (%s,%s,%s,'OPEN')""", (TASK, FINGERPRINT, "sandbox:never-deliver"))
            must_fail(conn, """INSERT INTO jarvis_return_actions
                (task_id,action_fingerprint,destination_key,state)
                VALUES (%s,%s,%s,'APPROVED')""",
                (TASK+"_bad_approval", FINGERPRINT, "sandbox:never-deliver"), "approval requires human")
            must_fail(conn, """INSERT INTO jarvis_return_actions
                (task_id,action_fingerprint,destination_key,state,
                 approved_by,approved_at,approved_fingerprint)
                VALUES (%s,%s,%s,'DONE',%s,now(),%s)""",
                (TASK+"_bad_receipt", FINGERPRINT, "sandbox:never-deliver",
                 "sandbox-reviewer", FINGERPRINT), "receipt required for DONE")
            must_fail(conn, """INSERT INTO jarvis_return_actions
                (task_id,action_fingerprint,destination_key,state)
                VALUES (%s,%s,%s,'OPEN')""",
                (TASK, FINGERPRINT, "sandbox:never-deliver"), "duplicate task id rejected")
            cur.execute("""UPDATE jarvis_return_actions SET
                state='APPROVED',approved_by='sandbox-reviewer',
                approved_at=now(),approval_expires_at=now()+interval '1 hour',
                approved_fingerprint=%s WHERE task_id=%s""", (FINGERPRINT, TASK))
            cur.execute("""INSERT INTO jarvis_return_action_events
                (task_id,from_state,to_state,actor,correlation_id,metadata)
                VALUES (%s,'OPEN','APPROVED','sandbox-reviewer',
                'sandbox-smoke-20261010','{"test":true}'::jsonb)""", (TASK,))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda o: claim(url, o), ("sandbox-worker-1","sandbox-worker-2")))
    assert sorted(results) == [0, 1], "Claim must have exactly one winner"
    print("PASS: atomic claim / idempotency: exactly one winner", flush=True)
    with connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO jarvis_return_action_events
                (task_id,from_state,to_state,actor,correlation_id,metadata)
                VALUES (%s,'APPROVED','PROCESSING','sandbox-test',
                'sandbox-smoke-20261010','{"test":true}'::jsonb)""", (TASK,))
    print("PASS: approval constraint, receipt constraint, duplicate key, event journal", flush=True)
    print("PASS: smoke marker committed for runner restart verification", flush=True)


def verify_restart(url):
    with connect(url) as conn:
        verify_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""SELECT state,attempt_count,approved_by,lease_owner
                FROM jarvis_return_actions WHERE task_id=%s""", (TASK,))
            row = cur.fetchone()
            assert row is not None and row[0] == "PROCESSING" and row[1] == 1
            assert row[2] == "sandbox-reviewer" and row[3] in ("sandbox-worker-1","sandbox-worker-2")
            cur.execute("""SELECT count(*), count(*) FILTER (WHERE to_state='APPROVED'),
                count(*) FILTER (WHERE to_state='PROCESSING')
                FROM jarvis_return_action_events WHERE task_id=%s""", (TASK,))
            assert cur.fetchone() == (2, 1, 1)
    print("PASS: persisted task and 2 audit events after runner restart", flush=True)


def main():
    url = checked_url()
    with connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('jarvis_return_actions')")
            exists = cur.fetchone()[0] is not None
    if not exists:
        first_run(url)
    else:
        verify_restart(url)
    print("PASS: private sandbox migration runner ready for cleanup", flush=True)
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
