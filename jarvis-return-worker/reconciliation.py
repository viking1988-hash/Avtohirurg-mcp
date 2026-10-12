"""Durable, provider-neutral reconciliation service.

All state changes and their audit rows share one PostgreSQL transaction. Operators
must supply the version they observed; row locks serialize contenders and stale
versions are rejected.
"""
from datetime import datetime
import psycopg
from psycopg.types.json import Jsonb
from .queue import Conflict

SNAPSHOT_FIELDS = ('version','state','provider','provider_message_id','provider_receipt',
    'delivery_status','reconciliation_status','reconciliation_reason','reconciliation_by',
    'reconciliation_at','reconciliation_comment','delivery_started_at','chat_id','delivery_response')


def _json_value(value):
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value

DELIVERY_FIELDS = (
    "provider", "provider_message_id", "provider_receipt", "delivery_status",
    "reconciliation_status", "reconciliation_reason", "reconciliation_by",
    "reconciliation_at", "reconciliation_comment",
)

def _public(row):
    return dict(row) if row is not None else None

def _event(conn, before, after, actor, action, reason=None, provider=None, metadata=None):
    conn.execute(
        """INSERT INTO jarvis_return_action_events
           (task_id,from_state,to_state,actor,correlation_id,event_time,metadata,
            action,provider,reason,previous_state,new_state)
           VALUES (%s,%s,%s,%s,%s,clock_timestamp(),%s,%s,%s,%s,%s,%s)""",
        (after["task_id"], before["state"] if before else None, after["state"],
         actor, after["correlation_id"], Jsonb(_json_value({**(metadata or {}),
             "snapshot": {key: after.get(key) for key in SNAPSHOT_FIELDS}})), action, provider,
         reason, before["state"] if before else None, after["state"]),
    )

def _task(conn, task_id, lock=False):
    suffix = " FOR UPDATE" if lock else ""
    return conn.execute("SELECT * FROM jarvis_return_actions WHERE task_id=%s"+suffix,
                        (task_id,)).fetchone()

def _version(row, expected_version):
    if row["version"] != expected_version:
        raise Conflict("stale version; reload task and retry with current version")

def _require(row, *states):
    if row["state"] not in states:
        raise Conflict("task state does not allow this reconciliation action")

def read_task(repo, task_id, actor):
    with repo.connection() as conn:
        row = _task(conn, task_id, True)
        if not row:
            return None
        _event(conn, row, row, actor, "task_read", provider=row.get("provider"),
               metadata={"version": row["version"]})
        return _public(row)

def read_events(repo, task_id, actor):
    with repo.connection() as conn:
        row = _task(conn, task_id, True)
        if not row:
            return None
        events = conn.execute(
            "SELECT event_id,task_id,actor,provider,action,previous_state,new_state,"
            "reason,correlation_id,event_time,metadata FROM jarvis_return_action_events "
            "WHERE task_id=%s ORDER BY event_id", (task_id,)).fetchall()
        _event(conn, row, row, actor, "events_read", provider=row.get("provider"),
               metadata={"returned_event_count": len(events), "version": row["version"]})
        return {"task_id": task_id, "version": row["version"], "events": events}

def read_delivery(repo, task_id, actor):
    with repo.connection() as conn:
        row = _task(conn, task_id, True)
        if not row:
            return None
        _event(conn, row, row, actor, "delivery_read", provider=row.get("provider"),
               metadata={"version": row["version"]})
        return {key: row.get(key) for key in
                ("task_id", "version", "state", *DELIVERY_FIELDS, "updated_at")}

def _failed(repo, task_id, actor, action, reason, expected_version=None):
    # Rejected attempts are auditable too. This is intentionally a separate
    # transaction after the rejected mutation rolls back.
    with repo.connection() as conn:
        row = _task(conn, task_id, True)
        if row:
            _event(conn, row, row, actor, "reconcile_failed", reason,
                   provider=row.get("provider"),
                   metadata={"requested_action": action,
                             "expected_version": expected_version,
                             "actual_version": row["version"]})

def mutate(repo, task_id, actor, action, expected_version, *, reason, comment=None,
           provider=None, provider_message_id=None, provider_receipt=None):
    try:
        with repo.connection() as conn:
            before = _task(conn, task_id, True)
            if not before:
                raise Conflict("task not found")
            _version(before, expected_version)
            after = None
            if action == "reconcile":
                _require(before, "RECONCILIATION_REQUIRED")
                if before.get("reconciliation_status") == "IN_REVIEW":
                    raise Conflict("reconciliation already in progress")
                after = conn.execute(
                    """UPDATE jarvis_return_actions SET reconciliation_status='IN_REVIEW',
                       reconciliation_reason=%s,reconciliation_by=%s,
                       reconciliation_at=clock_timestamp(),reconciliation_comment=%s,
                       version=version+1,updated_at=clock_timestamp()
                       WHERE task_id=%s RETURNING *""",
                    (reason, actor, comment, task_id)).fetchone()
                _event(conn, before, after, actor, "reconcile_started", reason,
                       provider or before.get("provider"),
                       {"comment": comment, "expected_version": expected_version})
            elif action == "confirm-delivered":
                _require(before, "RECONCILIATION_REQUIRED")
                if before.get("reconciliation_status") not in ("REQUIRED", "IN_REVIEW"):
                    raise Conflict("delivery is not awaiting confirmation")
                effective_provider = provider or before.get("provider")
                effective_receipt = provider_receipt or before.get("provider_receipt")
                effective_message = provider_message_id or before.get("provider_message_id")
                if not effective_provider or not (effective_receipt or effective_message or comment):
                    raise Conflict("provider and delivery evidence are required")
                after = conn.execute(
                    """UPDATE jarvis_return_actions SET state='DELIVERED_CONFIRMED',
                       provider=%s,provider_receipt=%s,provider_message_id=%s,
                       delivery_receipt=%s,external_message_id=%s,delivery_status='DELIVERED',
                       reconciliation_status='DELIVERED_CONFIRMED',
                       reconciliation_reason=%s,reconciliation_by=%s,
                       reconciliation_at=clock_timestamp(),reconciliation_comment=%s,
                       last_error=NULL,lease_owner=NULL,lease_until=NULL,
                       version=version+1,updated_at=clock_timestamp()
                       WHERE task_id=%s RETURNING *""",
                    (effective_provider, effective_receipt, effective_message,
                     effective_receipt, effective_message, reason, actor, comment, task_id)).fetchone()
                _event(conn, before, after, actor, action, reason, effective_provider,
                       {"comment": comment, "provider_receipt": effective_receipt,
                        "provider_message_id": effective_message,
                        "expected_version": expected_version})
            elif action == "confirm-undelivered":
                _require(before, "RECONCILIATION_REQUIRED")
                if before.get("reconciliation_status") not in ("REQUIRED", "IN_REVIEW"):
                    raise Conflict("delivery is not awaiting confirmation")
                after = conn.execute(
                    """UPDATE jarvis_return_actions SET state='UNDELIVERED_CONFIRMED',
                       delivery_status='UNDELIVERED',reconciliation_status='UNDELIVERED_CONFIRMED',
                       reconciliation_reason=%s,reconciliation_by=%s,
                       reconciliation_at=clock_timestamp(),reconciliation_comment=%s,
                       version=version+1,updated_at=clock_timestamp()
                       WHERE task_id=%s RETURNING *""",
                    (reason, actor, comment, task_id)).fetchone()
                _event(conn, before, after, actor, action, reason, before.get("provider"),
                       {"comment": comment, "provider_receipt": before.get("provider_receipt"),
                        "provider_message_id": before.get("provider_message_id"),
                        "expected_version": expected_version})
            elif action == "request-new-approval":
                _require(before, "UNDELIVERED_CONFIRMED")
                after = conn.execute(
                    """UPDATE jarvis_return_actions SET state='READY',approved_by=NULL,
                       approved_at=NULL,approval_expires_at=NULL,approved_fingerprint=NULL,
                       delivery_started_at=NULL,delivery_receipt=NULL,external_message_id=NULL,
                       provider=NULL,provider_message_id=NULL,provider_receipt=NULL,
                       delivery_status='PENDING',reconciliation_status='APPROVAL_REQUESTED',
                       reconciliation_reason=%s,reconciliation_by=%s,
                       reconciliation_at=clock_timestamp(),reconciliation_comment=%s,
                       lease_owner=NULL,lease_until=NULL,heartbeat_at=NULL,next_attempt_at=now(),
                       version=version+1,updated_at=clock_timestamp()
                       WHERE task_id=%s RETURNING *""",
                    (reason, actor, comment, task_id)).fetchone()
                _event(conn, before, after, actor, action, reason, before.get("provider"),
                       {"comment": comment, "prior_delivery": {
                           "provider": before.get("provider"),
                           "provider_receipt": before.get("provider_receipt"),
                           "provider_message_id": before.get("provider_message_id"),
                           "delivery_status": before.get("delivery_status")},
                        "expected_version": expected_version})
            else:
                raise Conflict("unsupported reconciliation action")
            return _public(after)
    except Conflict as exc:
        _failed(repo, task_id, actor, action, str(exc), expected_version)
        raise
    except psycopg.errors.UniqueViolation:
        reason = 'provider identifier is already bound to another delivery'
        _failed(repo, task_id, actor, action, reason, expected_version)
        raise Conflict(reason) from None


def log_rejected(repo, task_id, actor, action, reason):
    _failed(repo, task_id, actor, action, reason)
