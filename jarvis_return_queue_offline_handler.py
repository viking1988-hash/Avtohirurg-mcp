"""Isolated SQLite Jarvis Return Queue handler; no network or production integration.

Only sandbox: destinations are accepted. Human approval is explicit and
fingerprint-bound. A fake transport returns deterministic receipts.
"""
import sqlite3
from datetime import datetime, timezone
from jarvis_return_queue_safety import action_fingerprint, validate_transition


class FakeTransport:
    def __init__(self):
        self.receipts = {}
        self.calls = 0

    def deliver(self, key, payload):
        self.calls += 1
        return self.receipts.setdefault(key, "sandbox:receipt:" + key)


def initialize(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS queue (
        task_id TEXT PRIMARY KEY, destination TEXT NOT NULL, payload TEXT NOT NULL,
        fingerprint TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'OPEN',
        approved_by TEXT, approved_fingerprint TEXT, approval_expires_at TEXT,
        owner TEXT, receipt TEXT, version INTEGER NOT NULL DEFAULT 0)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS events (
        event_id INTEGER PRIMARY KEY, task_id TEXT NOT NULL,
        from_state TEXT, to_state TEXT NOT NULL, actor TEXT NOT NULL,
        FOREIGN KEY(task_id) REFERENCES queue(task_id))""")


def enqueue(conn, task_id, destination, payload):
    if not destination.startswith("sandbox:"):
        raise ValueError("only sandbox destinations allowed")
    fingerprint = action_fingerprint(task_id, destination, payload)
    with conn:
        conn.execute("INSERT INTO queue(task_id,destination,payload,fingerprint) VALUES (?,?,?,?)",
                     (task_id, destination, payload, fingerprint))
        conn.execute("INSERT INTO events(task_id,from_state,to_state,actor) VALUES (?,'','OPEN','sandbox')", (task_id,))
    return fingerprint


def prepare(conn, task_id):
    with conn:
        row = conn.execute("UPDATE queue SET state='READY',version=version+1 WHERE task_id=? AND state='OPEN' RETURNING task_id", (task_id,)).fetchone()
        if row:
            conn.execute("INSERT INTO events(task_id,from_state,to_state,actor) VALUES (?,'OPEN','READY','sandbox')", (task_id,))
    return bool(row)


def approve(conn, task_id, human, fingerprint, expires_at, now):
    if not human or not expires_at or expires_at.tzinfo is None or now.tzinfo is None:
        return False
    with conn:
        row = conn.execute("SELECT fingerprint FROM queue WHERE task_id=? AND state='READY'", (task_id,)).fetchone()
        if not row or not validate_transition("READY", "APPROVED", approved_by=human,
                approval_fingerprint=fingerprint, current_fingerprint=row[0],
                approval_expires_at=expires_at, now=now):
            return False
        result = conn.execute("""UPDATE queue SET state='APPROVED',approved_by=?,
            approved_fingerprint=?,approval_expires_at=?,version=version+1
            WHERE task_id=? AND state='READY' AND fingerprint=?""",
            (human, fingerprint, expires_at.isoformat(), task_id, fingerprint))
        if result.rowcount != 1:
            return False
        conn.execute("INSERT INTO events(task_id,from_state,to_state,actor) VALUES (?,'READY','APPROVED',?)", (task_id,human))
    return True


def process(conn, task_id, owner, transport, now):
    """Single-winner claim. Never automatically reclaims PROCESSING tasks."""
    if not isinstance(transport, FakeTransport) or not owner or now.tzinfo is None:
        raise ValueError("sandbox fake transport and timezone-aware time required")
    with conn:
        row = conn.execute("""UPDATE queue SET state='PROCESSING',owner=?,version=version+1
            WHERE task_id=? AND state='APPROVED'
              AND approved_by IS NOT NULL AND approved_fingerprint=fingerprint
              AND approval_expires_at>? AND destination LIKE 'sandbox:%'
            RETURNING fingerprint,payload""", (owner,task_id,now.isoformat())).fetchone()
        if not row:
            return False
        conn.execute("INSERT INTO events(task_id,from_state,to_state,actor) VALUES (?,'APPROVED','PROCESSING',?)", (task_id,owner))
    # Never retry automatically if fake delivery raises: manual reconciliation.
    receipt = transport.deliver(task_id + ":" + row[0], row[1])
    if not receipt:
        return False
    with conn:
        result = conn.execute("""UPDATE queue SET state='DONE',receipt=?,version=version+1
            WHERE task_id=? AND state='PROCESSING' AND owner=? AND receipt IS NULL""",
            (receipt,task_id,owner))
        if result.rowcount != 1:
            return False
        conn.execute("INSERT INTO events(task_id,from_state,to_state,actor) VALUES (?,'PROCESSING','DONE',?)", (task_id,owner))
    return True
