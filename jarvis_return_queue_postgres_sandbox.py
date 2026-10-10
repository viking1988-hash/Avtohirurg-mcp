"""PostgreSQL 18 sandbox-only queue repository, no network or delivery code.

The caller provides an existing psycopg connection to the isolated test DB.
Every mutation checks the database name and role before issuing writes.
"""
from jarvis_return_queue_safety import action_fingerprint, validate_transition


def guard(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT current_database(), current_user")
        if cur.fetchone() != ("jarvis_return_queue_test", "jarvis_sandbox"):
            raise RuntimeError("refusing non-sandbox database or role")


def enqueue(conn, task_id, destination, payload):
    guard(conn)
    if not destination.startswith("sandbox:"):
        raise ValueError("sandbox destination required")
    fp = action_fingerprint(task_id, destination, payload)
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO jarvis_return_actions
                (task_id,action_fingerprint,destination_key,state)
                VALUES (%s,%s,%s,'OPEN')""", (task_id,fp,destination))
            cur.execute("""INSERT INTO jarvis_return_action_events
                (task_id,from_state,to_state,actor,correlation_id)
                VALUES (%s,NULL,'OPEN','sandbox',%s)""", (task_id,task_id))
    return fp


def prepare(conn, task_id):
    guard(conn)
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("""UPDATE jarvis_return_actions
                SET state='READY',version=version+1,updated_at=now()
                WHERE task_id=%s AND state='OPEN' RETURNING task_id""", (task_id,))
            if cur.fetchone() is None:
                return False
            cur.execute("""INSERT INTO jarvis_return_action_events
                (task_id,from_state,to_state,actor,correlation_id)
                VALUES (%s,'OPEN','READY','sandbox',%s)""", (task_id,task_id))
    return True


def approve(conn, task_id, human, fingerprint, expires_at, now):
    guard(conn)
    if not human or not expires_at or expires_at.tzinfo is None or now.tzinfo is None:
        return False
    if not validate_transition("READY","APPROVED",approved_by=human,
            approval_fingerprint=fingerprint,current_fingerprint=fingerprint,
            approval_expires_at=expires_at,now=now):
        return False
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("""UPDATE jarvis_return_actions
                SET state='APPROVED',approved_by=%s,approved_at=%s,
                    approved_fingerprint=%s,approval_expires_at=%s,
                    version=version+1,updated_at=now()
                WHERE task_id=%s AND state='READY'
                  AND action_fingerprint=%s AND destination_key LIKE 'sandbox:%%'
                RETURNING task_id""",
                (human,now,fingerprint,expires_at,task_id,fingerprint))
            if cur.fetchone() is None:
                return False
            cur.execute("""INSERT INTO jarvis_return_action_events
                (task_id,from_state,to_state,actor,correlation_id)
                VALUES (%s,'READY','APPROVED',%s,%s)""", (task_id,human,task_id))
    return True


def claim(conn, task_id, owner, fingerprint, lease_seconds=60):
    """Atomic single-winner claim; no auto-reclaim, retry or real delivery."""
    guard(conn)
    if not owner or not fingerprint or not 1 <= lease_seconds <= 300:
        return False
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("""UPDATE jarvis_return_actions
                SET state='PROCESSING',lease_owner=%s,
                    lease_until=now()+(%s * interval '1 second'),
                    attempt_count=attempt_count+1,version=version+1,updated_at=now()
                WHERE task_id=%s AND state='APPROVED'
                  AND action_fingerprint=%s AND approved_fingerprint=%s
                  AND approval_expires_at>now()
                  AND destination_key LIKE 'sandbox:%%'
                RETURNING task_id""", (owner,lease_seconds,task_id,fingerprint,fingerprint))
            if cur.fetchone() is None:
                return False
            cur.execute("""INSERT INTO jarvis_return_action_events
                (task_id,from_state,to_state,actor,correlation_id)
                VALUES (%s,'APPROVED','PROCESSING',%s,%s)""", (task_id,owner,task_id))
    return True


def finish_fake(conn, task_id, owner, receipt):
    """Only sandbox fake receipts may mark DONE."""
    guard(conn)
    if not receipt or not receipt.startswith("sandbox:receipt:"):
        return False
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("""UPDATE jarvis_return_actions
                SET state='DONE',delivery_receipt=%s,version=version+1,
                    updated_at=now()
                WHERE task_id=%s AND state='PROCESSING' AND lease_owner=%s
                  AND delivery_receipt IS NULL
                RETURNING task_id""", (receipt,task_id,owner))
            if cur.fetchone() is None:
                return False
            cur.execute("""INSERT INTO jarvis_return_action_events
                (task_id,from_state,to_state,actor,correlation_id)
                VALUES (%s,'PROCESSING','DONE',%s,%s)""", (task_id,owner,task_id))
    return True
