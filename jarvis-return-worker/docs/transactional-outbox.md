# Transactional outbox and replay

## Write path

Queue and reconciliation mutations already share a PostgreSQL transaction with their
append-only action-journal row. The same transaction now inserts a row into
`jarvis_outbox_events`. A rollback removes both the business change and event; a
process crash after commit leaves the event durable for a later worker.

Each event has a UUID `event_id`, aggregate identity/type, event type and schema
version, minimized JSON payload, correlation and causation IDs, creation/publication
times, retry count, and status. The payload contains state transition metadata, not
the original task payload or user message.

## Replay and idempotency

Every application instance runs a replay loop. PostgreSQL row locks with
`FOR UPDATE SKIP LOCKED` let several instances compete safely. For one
(event, subscriber, version), the handler, `jarvis_processed_events` marker,
delivery status, and replay audit are committed in one transaction. If the
process or database disappears before commit, PostgreSQL rolls back all those writes
and another worker can try again after restart.

A committed processed marker and unique key prevent that internal subscriber from
running again for the same event. This is exactly-once for effects performed through
the handler's supplied PostgreSQL connection. Subscriber handlers must keep their
effects inside that transaction and should be short. Exactly-once cannot be
guaranteed for arbitrary email/HTTP/other external side effects; such integrations
must use their provider's idempotency key and an explicit delivery/reconciliation
contract.

Temporary exceptions are audited and retried with capped exponential backoff.
Raise `PermanentSubscriberError` for an unsupported or permanently invalid event;
the delivery is retained as FAILED and is not retried automatically. A paused or
not-yet-registered subscriber does not delete an event; it stays pending until a
matching active subscriber is registered.

## Subscriber registration

Register handlers on the shared `EventBus` registry (the compatibility name now
refers to a durable subscriber registry), and pass that registry to
`OutboxReplay`. Each entry carries a name, event type (or `*`), event schema
version, handler, and ACTIVE/PAUSED status. Registry rows, error counts, and the
last successful execution time live in `jarvis_event_subscribers`.

The Sandbox application installs `jarvis.lifecycle-projector` for all version-1
events. It writes a durable projection row in the same handler transaction. No
external transport is enabled by this subscriber.

## Storage tables

| Table | Purpose |
| --- | --- |
| `jarvis_outbox_events` | Immutable event envelope and publication status |
| `jarvis_event_subscribers` | Subscriber name, type/version, status, error count, last success |
| `jarvis_outbox_deliveries` | Per-subscriber attempts, lease, retry schedule, and final status |
| `jarvis_processed_events` | Unique exactly-once marker per event/subscriber/version |
| `jarvis_outbox_audit` | Success, retry, and permanent-failure history |
| `jarvis_event_projection` | Durable built-in lifecycle projection |
| `jarvis_crm_operations` | Idempotent CRM intents, lease, receipt, external ID, and reconciliation state |
| `jarvis_crm_operation_events` | Append-only CRM operation delivery/reconciliation audit |

The schema is additive and is applied by the worker's existing PostgreSQL migration
under its advisory migration lock.

## Recovery

1. Keep the Sandbox worker online; every instance runs the same replay loop.
2. Check `/ready` and authenticated `/metrics`; then inspect pending outbox rows,
   subscriber status, delivery attempts, and audit rows in Sandbox PostgreSQL.
3. A PENDING delivery is retried after `next_attempt_at`. Do not manually delete
   events, processed markers, or delivery history.
4. For a permanently failed delivery, fix or version the subscriber, then perform an
   explicit operator-approved replay procedure. Do not reset the marker unless the
   handler's side effects have been checked.
5. If PostgreSQL restarts, rely on the committed event and processed marker. Uncommitted
   handler effects roll back; committed effects are not run again.

Example inspection queries (Sandbox only):

```sql
SELECT event_id,event_type,status,retry_count,created_at,published_at
FROM jarvis_outbox_events ORDER BY created_at DESC LIMIT 25;

SELECT subscriber_name,status,attempt_count,next_attempt_at,last_error
FROM jarvis_outbox_deliveries ORDER BY started_at DESC LIMIT 25;

SELECT event_id,subscriber_name,action,created_at
FROM jarvis_outbox_audit ORDER BY audit_id DESC LIMIT 25;
```

## Metrics

`/metrics` exposes durable counts reloaded from PostgreSQL:

- `outbox_events_total`: total persisted outbox event rows.
- `outbox_pending_total`: events with pending work or waiting for a matching subscriber.
- `outbox_replayed_total`: committed subscriber deliveries.
- `outbox_failed_total`: audited retry and permanent-failure attempts.
- `subscriber_errors_total`: cumulative error count in the registry.
- `replay_latency_seconds`: replay handler latency histogram.
- `replay_queue_size`: current durable pending event count.

The two counters ending in `_total` that reflect durable state are reconstructed from
PostgreSQL after process restart; replay latency is process-local.

