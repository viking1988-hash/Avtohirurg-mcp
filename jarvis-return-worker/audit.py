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
    conn.execute("""INSERT INTO jarvis_return_action_events
        (task_id,from_state,to_state,actor,correlation_id,metadata,
         action,provider,reason,previous_state,new_state)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (after["task_id"],before["state"] if before else None,after["state"],worker_id,
         after["correlation_id"],Jsonb(_json_value(details)),action,after.get("provider"),reason,
         before["state"] if before else None,after["state"]))
