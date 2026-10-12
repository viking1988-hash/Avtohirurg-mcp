# CRM Booking transport (Sandbox)

## Event adapter

`CRMAdapter` is registered with the same durable `SubscriberRegistry` used by the
Sandbox lifecycle projection. It recognizes `BookingCreated`, `BookingUpdated`,
`BookingCancelled`, `BookingConfirmed`, and `BookingCompleted`. Each source event
maps to an operation and a stable `jarvis-crm:<event_id>` idempotency key. The
adapter inserts a row into `jarvis_crm_operations` in the supplied PostgreSQL
transaction. `event_id` and `idempotency_key` are unique, so a replay cannot create
a second operation intent. The row retains `booking_id`, CRM status, a minimized
event payload, external booking ID, provider receipt, and reconciliation reason.

Replay handlers are database-only. The adapter has no provider reference and makes
no external request. CRM Booking cannot call the queue or provider directly.

## Provider

`CRMProvider` implements send, update, cancel, reconcile, and checkStatus. It
requires an HTTPS Railway `*.up.railway.app` Sandbox hostname containing the
`sandbox` label, a bounded request timeout, and an explicit token. Staging and
Production endpoints are rejected. Mutations send the event-derived
idempotency key in `Idempotency-Key`. Responses are reduced to booking identifiers,
status, and receipt. Tokens, request headers, response bodies, and customer fields
are not included in errors or logs.

HTTP 429 is RETRYABLE; definitive 4xx is PERMANENT; 5xx, timeout, connection loss,
and unreadable responses are UNKNOWN. Existing Orchestrator policy never retries an
UNKNOWN delivery and routes it to `RECONCILIATION_REQUIRED`.

## Current rollout boundary

The Sandbox app registers the provider only when both
`JARVIS_CRM_SANDBOX_URL` and `JARVIS_CRM_SANDBOX_TOKEN` are configured. A URL
without an explicit Railway Sandbox hostname is rejected. No CRM credentials or
endpoint are currently enabled by this code change. Adapter-created operations
remain PENDING; the CRM operation claim, provider call, receipt persistence, and
reconciliation executor are a follow-up step. Do not mark this integration as
end-to-end until that executor is implemented and verified against a dedicated
CRM Sandbox with synthetic bookings.

## Metrics

| Metric | Meaning |
|---|---|
| `crm_requests_total` | CRM provider requests |
| `crm_success_total` | Successful CRM responses |
| `crm_failed_total` | Definitive request failures |
| `crm_unknown_total` | Ambiguous request outcomes |
| `crm_reconciliation_total` | Unknown outcomes requiring reconciliation |
| `crm_duplicate_prevented_total` | Outbox event intents rejected by unique-key deduplication |
| `crm_latency_seconds` | CRM request latency histogram |

These metrics are available at authenticated `/metrics`. Provider counters are
process-local; the operation and receipt journal is PostgreSQL-backed.

## Recovery

Keep ambiguous operations out of the automatic retry path. Inspect the Sandbox
operation row and the provider using `booking_id`/`external_booking_id`, then record
the reconciliation result and reason through the Orchestrator-owned recovery path.
Never delete an idempotency key or receipt to force a resend. A definitive
non-delivery may be retried only after Policy Engine authorization and with the
same stable operation key. Production and CRM Booking environments are outside
this runbook.
