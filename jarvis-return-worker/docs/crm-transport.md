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

## Orchestrator execution and reconciliation

The Sandbox app registers the provider only when both
`JARVIS_CRM_SANDBOX_URL` and `JARVIS_CRM_SANDBOX_TOKEN` are configured. A URL
without an explicit Railway Sandbox hostname is rejected. `CRMOperationWorker`
claims intents with PostgreSQL `SKIP LOCKED`, then delegates provider calls to the
Orchestrator and Policy Engine. It saves external booking ID and provider receipt
before marking an operation DONE. The provider call always carries the original
outbox event idempotency key. An expired PROCESSING lease is moved to
`RECONCILIATION_REQUIRED`; the operation is never claimed for a blind resend.

Read an operation with authenticated `GET /crm/operations/{event_id}`. An
authorized operator can request a read-only provider lookup with
`POST /crm/operations/{event_id}/reconcile`, passing the observed `expected_version`.
The row lock and optimistic version check reject stale operator submissions. The operation becomes DONE only when
the provider returns status, external booking ID, and receipt; inconclusive lookup
keeps it in `RECONCILIATION_REQUIRED` with a reason. No CRM credentials or endpoint
are enabled by this code change. Do not mark the transport live until verified
against a dedicated Railway Sandbox CRM endpoint with synthetic bookings.

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
process-local; operation states, receipts, external IDs and reconciliation reasons
are PostgreSQL-backed.

## Recovery

Keep ambiguous operations out of the automatic retry path. Inspect the Sandbox
operation through the authenticated endpoint and use its `booking_id` or
`external_booking_id` for a read-only provider lookup. Never delete an idempotency
key or receipt to force a resend. Only an explicit provider 429 rejection can
return to PENDING, and it keeps the same stable operation key. Production and the
independent CRM Booking environment are outside this runbook.

The current Railway Sandbox has no dedicated CRM Sandbox endpoint or credentials.
The CRM worker therefore remains disabled there. PostgreSQL adapter/replay tests
exercise durable intent creation and failure/reconciliation transitions; mocked
provider contract tests do not prove external CRM delivery and must not be reported
as a live integration check.
