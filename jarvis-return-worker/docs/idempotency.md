# Idempotency Guide

Each outbox event maps to one durable webhook operation and deterministic key `jarvis-webhook:<event_id>`. The key is sent as `Idempotency-Key`; receivers should deduplicate it across their reconciliation window.

Replay uses event/subscriber uniqueness; webhook operations have a unique event ID. Fenced writes require the current owner, attempt number, and version. Duplicate provider response IDs become `RECONCILIATION_REQUIRED`.

Jarvis does not resend ambiguous writes. Receivers should expose a status endpoint and stable response ID. Idempotency headers cannot provide exactly-once behavior if the receiver ignores them.
