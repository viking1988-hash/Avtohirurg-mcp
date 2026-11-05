from time import monotonic
from psycopg.types.json import Jsonb

SNAPSHOT_FIELDS = (
    "version", "state", "provider", "provider_message_id", "provider_receipt",
    "delivery_status", "reconciliation_status", "reconciliation_reason",
    "reconciliation_by", "reconciliation_at", "reconciliation_comment",
    "delivery_started_at", "chat_id", "delivery_response",
)

def _json_value(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value

EVENT_TYPES = {
    "enqueue": "TaskCreated", "prepare": "TaskReady", "approve": "TaskApproved",
    "delivery_started": "DeliveryStarted", "delivery_receipt_saved": "DeliverySucceeded",
    "finish": "TaskFinished", "delivery_uncertain": "DeliveryUnknown",
    "fail": "DeliveryFailed", "telegram_permanent_rejection": "DeliveryFailed",
    "telegram_retry_after_rejection": "DeliveryRetryScheduled",
    "cancel": "TaskCancelled", "requeue_requires_fresh_approval": "TaskReady",
    "recovery": "TaskRecovered", "receipt_recovered": "TaskFinished",
    "reconcile_delivered": "ReconciliationCompleted",
    "reconcile_not_delivered": "ReconciliationCompleted",
    "reconcile_started": "ReconciliationStarted",
    "confirm-delivered": "ReconciliationCompleted",
    "confirm-undelivered": "ReconciliationCompleted",
    "request-new-approval": "ApprovalRequested",
}


def append_audit_outbox(conn, audit_event_id, before, after, action, provider=None, reason=None):
    event_type = EVENT_TYPES.get(action)
    if not event_type:
        return None
    from .outbox import append_event
    payload = {
        "action": action,
        "previous_state": before.get("state") if before else None,
        "new_state": after.get("state"),
        "aggregate_version": after.get("version"),
        "provider": provider or after.get("provider") or (
            "telegram" if str(after.get("destination_key", "")).startswith("sandbox:telegram:") else
            "http" if str(after.get("destination_key", "")).startswith("sandbox:http:") else "fake"
        ),
        "reason": reason,
    }
    if action == "approve":
        # Bind the durable dispatch intent to this exact, human-approved version.
        payload["approved_at"] = _json_value(after.get("approved_at"))
        payload["approved_fingerprint"] = after.get("approved_fingerprint")
    return append_event(conn, aggregate_id=after["task_id"], aggregate_type="queue_task",
        event_type=event_type, event_version=1, payload=payload,
        correlation_id=after.get("correlation_id"), causation_id=str(audit_event_id))


def record(conn, before, after, action, worker_id, started=None, metadata=None):
    details={
        "action":action,
        "worker_id":worker_id,
        "lease_owner":(before or after).get("lease_owner"),
        "duration":max(0,monotonic()-started) if started else 0,
        "snapshot":_json_value({key:after.get(key) for key in SNAPSHOT_FIELDS}),
        **(metadata or {}),
    }
    reason=details.get("reason")
    audit_row = conn.execute("""INSERT INTO jarvis_return_action_events
        (task_id,from_state,to_state,actor,correlation_id,metadata,
         action,provider,reason,previous_state,new_state)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING event_id""",
        (after["task_id"],before["state"] if before else None,after["state"],worker_id,
         after["correlation_id"],Jsonb(_json_value(details)),action,after.get("provider"),reason,
         before["state"] if before else None,after["state"])).fetchone()
    append_audit_outbox(conn, audit_row["event_id"], before, after, action,
                        provider=after.get("provider"), reason=reason)
