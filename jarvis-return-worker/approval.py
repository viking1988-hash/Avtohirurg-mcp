# Same canonical fingerprint contract as the accepted queue safety module.
import hashlib
import json
from datetime import datetime, timezone

def action_fingerprint(task_id, destination, payload):
    canonical = json.dumps({'task_id': task_id, 'destination': destination, 'payload': payload},
        sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(canonical.encode()).hexdigest()

def valid_approval(task, now=None):
    now = now or datetime.now(timezone.utc)
    return bool(task['approved_by'] and task['approved_at'] and task['approval_expires_at']
        and task['approval_expires_at'] > now and task['destination_key'].startswith('sandbox:')
        and task['approved_fingerprint'] == task['action_fingerprint']
        == action_fingerprint(task['task_id'], task['destination_key'], task['payload']))
