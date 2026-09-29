import hashlib
import json
import os
from datetime import datetime, timezone

try:
    import psycopg
except ImportError:
    psycopg = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS avtohirurg_clients (
    client_key TEXT PRIMARY KEY,
    name TEXT,
    phone TEXT,
    current_state TEXT NOT NULL DEFAULT 'NEW',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS avtohirurg_visits (
    id BIGSERIAL PRIMARY KEY,
    client_key TEXT NOT NULL REFERENCES avtohirurg_clients(client_key),
    car TEXT,
    year TEXT,
    mileage TEXT,
    state TEXT NOT NULL,
    card JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_avtohirurg_visits_client
    ON avtohirurg_visits(client_key, created_at DESC);
"""

def _dsn():
    return (
        os.environ.get("DATABASE_URL")
        or os.environ.get("POSTGRES_URL")
        or os.environ.get("DATABASE_PUBLIC_URL")
        or ""
    ).strip()

def _client_key(phone):
    value = str(phone or "").strip()
    if not value:
        raise ValueError("phone is required for client history")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

def _connect():
    dsn = _dsn()
    if not dsn:
        raise RuntimeError("DATABASE_URL is not configured")
    if psycopg is None:
        raise RuntimeError("psycopg is not installed")
    return psycopg.connect(dsn)

def init_store():
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA)
        conn.commit()

def upsert_client(name, phone, state):
    key = _client_key(phone)
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO avtohirurg_clients(client_key,name,phone,current_state)
                   VALUES (%s,%s,%s,%s)
                   ON CONFLICT(client_key) DO UPDATE SET
                     name=COALESCE(EXCLUDED.name,avtohirurg_clients.name),
                     phone=COALESCE(EXCLUDED.phone,avtohirurg_clients.phone),
                     current_state=EXCLUDED.current_state,
                     updated_at=NOW()""",
                (key, name or None, phone, state),
            )
        conn.commit()
    return key

def save_visit(name, phone, state, card):
    key = upsert_client(name, phone, state)
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO avtohirurg_visits(client_key,car,year,mileage,state,card)
                   VALUES (%s,%s,%s,%s,%s,%s::jsonb)
                   RETURNING id""",
                (
                    key, card.get("car"), card.get("year"), card.get("mileage"),
                    state, json.dumps(card, ensure_ascii=False),
                ),
            )
            visit_id = cur.fetchone()[0]
        conn.commit()
    return {"client_key": key, "visit_id": visit_id}

def set_state(phone, state, name=""):
    key = upsert_client(name, phone, state)
    return {"client_key": key, "state": state}

def get_history(phone, limit=10):
    key = _client_key(phone)
    limit = max(1, min(int(limit), 50))
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id,car,year,mileage,state,card,created_at
                   FROM avtohirurg_visits
                   WHERE client_key=%s
                   ORDER BY created_at DESC
                   LIMIT %s""",
                (key, limit),
            )
            rows = cur.fetchall()
    return [
        {
            "visit_id": row[0],
            "car": row[1],
            "year": row[2],
            "mileage": row[3],
            "state": row[4],
            "card": row[5],
            "created_at": row[6].isoformat() if row[6] else None,
        }
        for row in rows
    ]

def create_return_task(phone, task_type, due_date="", payload=None):
    key = _client_key(phone)
    task_type = str(task_type or "").strip()
    if not task_type:
        raise ValueError("task_type is required")
    payload = payload or {}
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS avtohirurg_return_tasks (
                id BIGSERIAL PRIMARY KEY,
                client_key TEXT NOT NULL REFERENCES avtohirurg_clients(client_key),
                task_type TEXT NOT NULL,
                due_date TEXT,
                status TEXT NOT NULL DEFAULT 'OPEN',
                payload JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )""")
            cur.execute("""INSERT INTO avtohirurg_return_tasks(client_key,task_type,due_date,payload)
                VALUES (%s,%s,%s,%s::jsonb) RETURNING id""",
                (key, task_type, str(due_date or "").strip() or None, json.dumps(payload, ensure_ascii=False)))
            task_id = cur.fetchone()[0]
        conn.commit()
    return {"task_id": task_id, "client_key": key, "status": "OPEN"}

def get_return_tasks(phone, limit=20):
    key = _client_key(phone)
    limit = max(1, min(int(limit), 50))
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT id,task_type,due_date,status,payload,created_at
                FROM avtohir_return_tasks WHERE client_key=%s
                ORDER BY created_at DESC LIMIT %s""", (key, limit))
            rows = cur.fetchall()
    return [{"task_id":r[0],"task_type":r[1],"due_date":r[2],"status":r[3],"payload":r[4],"created_at":r[5].isoformat() if r[5] else None} for r in rows]

def list_open_return_tasks(limit=100):
    limit = max(1, min(int(limit), 200))
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT id,client_key,task_type,due_date,status,payload,created_at
                FROM avtohirurg_return_tasks WHERE status='OPEN'
                ORDER BY created_at ASC LIMIT %s""", (limit,))
            rows = cur.fetchall()
    return [{"task_id":r[0],"client_key":r[1],"task_type":r[2],"due_date":r[3],"status":r[4],"payload":r[5],"created_at":r[6].isoformat() if r[6] else None} for r in rows]

def update_return_task_status(task_id, status):
    status = str(status or "").strip().upper()
    if status not in {"OPEN", "READY", "DONE", "CANCELLED"}:
        raise ValueError("invalid task status")
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE avtohirurg_return_tasks SET status=%s WHERE id=%s RETURNING id,status", (status, task_id))
            row = cur.fetchone()
        conn.commit()
    if not row:
        raise ValueError("return task not found")
    return {"task_id": row[0], "status": row[1]}

def health():
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
    return {"status": "ok", "checked_at": datetime.now(timezone.utc).isoformat()}
