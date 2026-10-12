"""Read-only, one-shot private Railway staging SQL verification."""
import json
import os
import sys
from pathlib import Path
from urllib.parse import quote
import psycopg

EXPECTED_PROJECT = "73400534-5ebc-4619-86d9-f036e1028db9"
EXPECTED_ENV = "96dc8ace-8884-47b1-b529-ee2c317c7904"
EXPECTED_DB = "jarvis_return_queue_test"
EXPECTED_USER = "jarvis_sandbox"
EXPECTED_HOST = "jarvis-sandbox-postgres.railway.internal"
SQL_PATH = Path(__file__).resolve().parents[1] / "docs/sql/jarvis-return-queue-staging-readonly-verification.sql"

def main():
    if os.environ.get("RAILWAY_PROJECT_ID") != EXPECTED_PROJECT:
        raise RuntimeError("REFUSED: wrong Railway project")
    if os.environ.get("RAILWAY_ENVIRONMENT_ID") != EXPECTED_ENV:
        raise RuntimeError("REFUSED: wrong Railway environment")
    if os.environ.get("RAILWAY_SERVICE_NAME") != "jarvis-queue-sql-readonly-check":
        raise RuntimeError("REFUSED: wrong runner")
    password = os.environ.get("JARVIS_SANDBOX_PASSWORD", "")
    if not password:
        raise RuntimeError("REFUSED: sandbox secret not available")
    url = "postgresql://" + EXPECTED_USER + ":" + quote(password, safe="") + "@" + EXPECTED_HOST + ":5432/" + EXPECTED_DB
    sql = SQL_PATH.read_text(encoding="utf-8")
    if not sql.startswith("-- Jarvis Return Queue: read-only Railway staging verification."):
        raise RuntimeError("REFUSED: unexpected SQL file")
    if "BEGIN TRANSACTION READ ONLY;" not in sql or "DO $$" not in sql or "COMMIT;" not in sql:
        raise RuntimeError("REFUSED: SQL safety markers absent")
    with psycopg.connect(url, connect_timeout=12, options="-c default_transaction_read_only=on") as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database(), current_user, current_setting('server_version_num')::int")
            db, role, pg_version = cur.fetchone()
            if (db, role) != (EXPECTED_DB, EXPECTED_USER) or not 180000 <= pg_version < 190000:
                raise RuntimeError("REFUSED: server-side identity/version mismatch")
            conn.commit()
            print("PASS identity: " + json.dumps({"db": db, "role": role, "postgres_major": 18}), flush=True)
            cur.execute(sql)
            outputs = []
            while True:
                if cur.description:
                    columns = [col.name for col in cur.description]
                    rows = [dict(zip(columns, [str(v) if v is not None else None for v in row])) for row in cur.fetchall()]
                    outputs.append({"columns": columns, "rows": rows})
                if not cur.nextset():
                    break
    for i, result in enumerate(outputs):
        print("SQL_RESULT_" + str(i+1) + " " + json.dumps(result, ensure_ascii=False), flush=True)
    if len(outputs) != 6:
        raise RuntimeError("FAIL: expected six SELECT result sets, got " + str(len(outputs)))
    identity, tables, indexes, constraints, actions, events = outputs
    if len(identity["rows"]) != 1 or identity["rows"][0]["db"] != EXPECTED_DB or identity["rows"][0]["db_role"] != EXPECTED_USER:
        raise RuntimeError("FAIL: SQL identity")
    if len(tables["rows"]) != 1 or not all(tables["rows"][0].values()):
        raise RuntimeError("FAIL: missing queue tables")
    if len(indexes["rows"]) != 1 or indexes["rows"][0]["indexname"] != "jarvis_return_action_events_task_time_idx":
        raise RuntimeError("FAIL: missing event index")
    defs = [r["definition"] for r in constraints["rows"]]
    if not any("approved_by" in d and "approved_fingerprint" in d for d in defs):
        raise RuntimeError("FAIL: APPROVAL constraint not found")
    if not any("delivery_receipt" in d for d in defs):
        raise RuntimeError("FAIL: DONE receipt constraint not found")
    if not actions["rows"]:
        raise RuntimeError("FAIL: smoke task missing")
    if not events["rows"] or not any(int(r["event_count"]) >= 2 for r in events["rows"]):
        raise RuntimeError("FAIL: smoke events missing")
    print("PASS: two tables, index, approval/receipt constraints, smoke records, event journal", flush=True)
    print("PASS: exact PR #27 SQL executed read-only on private Railway staging", flush=True)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("FAIL: " + str(e).split("postgresql://")[0], flush=True)
        sys.exit(1)
