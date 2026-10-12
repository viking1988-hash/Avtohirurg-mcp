"""Pure, side-effect-free Jarvis return-queue safety checks.

This module does not send messages or modify task state. It is not wired to
production. The authoritative state transition must still be atomic in storage.
"""
from datetime import datetime, timezone
from typing import Any
import hashlib
import json

ALLOWED = {
    "OPEN": {"READY", "CANCELLED"},
    "READY": {"APPROVED", "CANCELLED"},
    "APPROVED": {"PROCESSING", "CANCELLED"},
    "PROCESSING": {"DONE", "RETRY", "FAILED"},
    "RETRY": {"PROCESSING", "FAILED", "CANCELLED"},
    "FAILED": set(),
    "DONE": set(),
    "CANCELLED": set(),
}

def action_fingerprint(task_id: str, destination: str, payload: Any) -> str:
    if not task_id or not destination:
        raise ValueError("task_id and destination are required")
    canonical = json.dumps(
        {"task_id": task_id, "destination": destination, "payload": payload},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

def validate_transition(
    previous: str, next_state: str, *,
    approved_by: str | None = None,
    approval_fingerprint: str | None = None,
    current_fingerprint: str | None = None,
    approval_expires_at: datetime | None = None,
    delivery_receipt: str | None = None,
    now: datetime | None = None,
) -> bool:
    if next_state not in ALLOWED.get(previous, set()):
        return False
    if next_state in {"APPROVED", "PROCESSING"}:
        if not approved_by or not approval_fingerprint or not current_fingerprint:
            return False
        if approval_fingerprint != current_fingerprint:
            return False
        if approval_expires_at is None or approval_expires_at.tzinfo is None:
            return False
        current_time = now if now is not None else datetime.now(timezone.utc)
        if current_time.tzinfo is None or current_time >= approval_expires_at:
            return False
    if next_state == "DONE" and not delivery_receipt:
        return False
    return True
