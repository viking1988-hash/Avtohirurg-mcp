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

def health():
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
    return {"status": "ok", "checked_at": datetime.now(timezone.utc).isoformat()}
