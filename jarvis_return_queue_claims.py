"""SQLite-backed atomic task claim prototype for staging.

Not wired to production. In production, use the existing durable task store
and a database transaction/unique constraint, not an isolated SQLite file.
"""
import sqlite3
from datetime import datetime, timedelta, timezone

def initialize(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS jarvis_claims (
        task_id TEXT PRIMARY KEY,
        action_fingerprint TEXT NOT NULL,
        lease_owner TEXT NOT NULL,
        lease_until TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('PROCESSING','DONE'))
    )""")

def claim(conn: sqlite3.Connection, task_id: str, fingerprint: str,
          owner: str, now: datetime, lease_seconds: int = 120) -> bool:
    """One winner per task. Expired claims can be reclaimed only after
    upstream delivery status reconciliation; this prototype does not reclaim.
    """
    if not task_id or not fingerprint or not owner or now.tzinfo is None or lease_seconds <= 0:
        raise ValueError("invalid claim parameters")
    expires = (now + timedelta(seconds=lease_seconds)).astimezone(timezone.utc).isoformat()
    with conn:
        cur = conn.execute(
            """INSERT OR IGNORE INTO jarvis_claims
            (task_id,action_fingerprint,lease_owner,lease_until,state)
            VALUES (?,?,?,?,'PROCESSING')""",
            (task_id, fingerprint, owner, expires))
    return cur.rowcount == 1

def complete(conn: sqlite3.Connection, task_id: str, fingerprint: str,
             owner: str, receipt: str) -> bool:
    if not receipt:
        return False
    with conn:
        cur = conn.execute(
            """UPDATE jarvis_claims SET state='DONE'
            WHERE task_id=? AND action_fingerprint=? AND lease_owner=?
            AND state='PROCESSING'""",
            (task_id, fingerprint, owner))
    return cur.rowcount == 1
