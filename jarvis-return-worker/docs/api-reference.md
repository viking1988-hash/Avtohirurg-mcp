# Reconciliation API Reference

All endpoints require HTTPS and a bearer token. Reads use JARVIS_READ_TOKEN; reconciliation writes require the separate JARVIS_APPROVAL_TOKEN. Actor identity comes from the configured JARVIS_OPERATOR_ID, never from request JSON. Responses omit secrets. The legacy unversioned POST /reconcile returns 410 Gone.

| Method and path | Access | Purpose |
|---|---|---|
| GET /queue/{task_id} | read | Task snapshot, including current version |
| GET /queue/{task_id}/events | read | Ordered audit history; read itself is audited |
| GET /queue/{task_id}/delivery | read | Provider and reconciliation projection, without payload |
| POST /queue/{task_id}/reconcile | operator | Start a manual review |
| POST /queue/{task_id}/confirm-delivered | operator | Record confirmed delivery evidence |
| POST /queue/{task_id}/confirm-undelivered | operator | Record confirmed non-delivery |
| POST /queue/{task_id}/request-new-approval | operator | Reset a confirmed undelivered task to READY |

All POST bodies require expected_version from the last GET and a reason. Optional comment is limited to 2,000 characters.

Start review:

~~~http
POST /queue/return-123/reconcile
Authorization: Bearer <JARVIS_APPROVAL_TOKEN>
Content-Type: application/json

{"expected_version":7,"reason":"Checking the allowlisted test chat","comment":"No automatic retry"}
~~~

Confirm delivered:

~~~json
{"expected_version":8,"reason":"Message visible in test chat","provider":"telegram","provider_message_id":"101","provider_receipt":"telegram:chat:101","comment":"Verified by operator"}
~~~

Confirm undelivered:

~~~json
{"expected_version":8,"reason":"Test chat and provider history checked","comment":"No message found"}
~~~

Request a new approval after confirmed non-delivery:

~~~json
{"expected_version":9,"reason":"Non-delivery confirmed; new approval required"}
~~~

The POST response contains the updated task and incremented version. Stale versions, duplicate confirmations, invalid transitions and concurrent conflicts return 409; callers must GET the current state before deciding what to do. Missing tasks return 404; repository outages return 503. Every accepted or rejected valid operation is written to the audit journal.


## Outbox metrics

The authenticated `/metrics` endpoint also exports:

| Metric | Meaning |
| --- | --- |
| `outbox_events_total` | Persisted events |
| `outbox_pending_total` | Pending events, including events waiting for an active subscriber |
| `outbox_replayed_total` | Committed event/subscriber deliveries |
| `outbox_failed_total` | Audited retry and permanent failure attempts |
| `subscriber_errors_total` | Cumulative subscriber registry error count |
| `replay_latency_seconds` | Replay handler latency histogram |
| `replay_queue_size` | Current pending event count |

The durable totals are reconstructed from PostgreSQL after restart. Replay latency
is process-local. See the [Transactional Outbox and Replay guide](transactional-outbox.md).
