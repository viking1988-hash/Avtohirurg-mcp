# Jarvis Return Worker — Railway Sandbox only

The Orchestrator is the single application boundary for queue lifecycle and providers. Lifecycle events use a PostgreSQL transactional outbox and multi-instance replay worker; see the [outbox and recovery guide](docs/transactional-outbox.md) and [Orchestrator architecture](docs/orchestrator.md).

This worker is isolated to the Railway Sandbox project and staging environment. Startup checks the exact Railway project/environment and private PostgreSQL database identity. It does not deploy to Production or CRM Booking.

## Telegram Transport

JARVIS_TRANSPORT=telegram enables Bot API sendMessage and requires a Sandbox-only bot token plus one allowlisted test chat. Destinations must match that test chat. Never copy Production credentials into Sandbox. Telegram has no idempotency key: the worker commits a send marker before one bounded request and never automatically retries an ambiguous result. A timeout, HTTP 5xx, or missing receipt enters RECONCILIATION_REQUIRED. A definitive provider rejection may follow an explicit provider retry policy.

On success, the provider name, message ID, receipt, chat ID and minimized response are durably stored before completion. Message text, user profile and sender data are discarded; secrets and request URLs are excluded from logs.

## Delivery and reconciliation states

~~~mermaid
stateDiagram-v2
    [*] --> READY
    READY --> APPROVED: fresh human approval
    APPROVED --> PROCESSING: fenced claim
    PROCESSING --> DONE: receipt saved
    PROCESSING --> FAILED: definitive permanent rejection
    PROCESSING --> APPROVED: definitive retryable rejection
    PROCESSING --> RECONCILIATION_REQUIRED: ambiguous outcome
    PROCESSING --> APPROVED: lease expires before send marker
    PROCESSING --> RECONCILIATION_REQUIRED: lease expires after marker
    RECONCILIATION_REQUIRED --> DELIVERED_CONFIRMED: operator confirms delivery
    RECONCILIATION_REQUIRED --> UNDELIVERED_CONFIRMED: operator confirms non-delivery
    UNDELIVERED_CONFIRMED --> READY: request fresh approval
    READY --> CANCELLED: operator cancellation
~~~

DONE represents a receipt-backed worker completion. Operator-confirmed outcomes are separately represented by DELIVERED_CONFIRMED and UNDELIVERED_CONFIRMED. All three states are excluded from claims. See the complete [State Machine](docs/state-machine.md).

## Reconciliation API

Read-only operations use JARVIS_READ_TOKEN. Operator decisions use the separate JARVIS_APPROVAL_TOKEN; actor identity comes from JARVIS_OPERATOR_ID, never from caller JSON. Each write requires the observed expected_version, reason and optional comment. The row lock plus optimistic version check prevents stale writes, double confirmation and concurrent operator races. The legacy unversioned POST /reconcile returns 410 and is audited.

- GET /queue/{task_id}
- GET /queue/{task_id}/events
- GET /queue/{task_id}/delivery
- POST /queue/{task_id}/reconcile
- POST /queue/{task_id}/confirm-delivered
- POST /queue/{task_id}/confirm-undelivered
- POST /queue/{task_id}/request-new-approval

Every accepted and rejected valid action is persisted in the same transaction as its audit event; rejected optimistic-lock attempts are then written in a separate audit transaction. Provider IDs and receipts have database uniqueness constraints. See the [API Reference](docs/api-reference.md) and [Manual Reconciliation Guide](docs/reconciliation-guide.md).

## Recovery

The send marker is committed before a transport call; provider receipt and IDs are committed before DONE. Recovery sends no second request after an ambiguous outcome. After confirmed non-delivery, a separate request clears the old approval and marker, records prior provider evidence in the audit history, and returns the task to READY. A fresh human approval is then required. See the [Recovery Guide](docs/recovery-guide.md).

## Durable events, audit and metrics

Queue and reconciliation transitions insert their outbox event atomically with the business row and audit journal. Replay workers use PostgreSQL row locks and a processed-event primary key. Subscriber database effects and processed markers commit together; external side effects still require provider idempotency. See the [Transactional Outbox guide](docs/transactional-outbox.md).

Authenticated /metrics includes outbox_events_total, outbox_pending_total, outbox_replayed_total, outbox_failed_total, subscriber_errors_total, replay_latency_seconds and replay_queue_size. Durable totals are reconstructed from PostgreSQL after restart.

## CRM Booking integration (Sandbox)

`CRMAdapter` maps version-1 `BookingCreated`, `BookingUpdated`, `BookingCancelled`,
`BookingConfirmed` and `BookingCompleted` outbox events into deduplicated rows in
`jarvis_crm_operations`. It runs as a PostgreSQL outbox subscriber and makes no
network calls. `CRMProvider` is registered with the same provider registry as other
transports only when both `JARVIS_CRM_SANDBOX_URL` and
`JARVIS_CRM_SANDBOX_TOKEN` are set. The endpoint must use HTTPS and identify itself
as a Railway Sandbox hostname. Staging and Production endpoints are rejected. Never
configure Production credentials here.

The provider uses stable idempotency keys, minimizes stored responses to booking
IDs/status/receipt, and classifies ambiguous network/5xx responses as UNKNOWN;
Orchestrator policy sends UNKNOWN outcomes to reconciliation and never retries them.
Metrics include `crm_requests_total`, `crm_success_total`, `crm_failed_total`,
`crm_unknown_total`, `crm_reconciliation_total`,
`crm_duplicate_prevented_total`, and `crm_latency_seconds`. See
[CRM transport and recovery](docs/crm-transport.md).

When a dedicated Railway Sandbox CRM endpoint is configured, a separate CRM
operation worker sends outbox-created intents through the Orchestrator and Policy
Engine. Inspect an operation with `GET /crm/operations/{event_id}`; an authorized
operator can trigger a read-only lookup with
`POST /crm/operations/{event_id}/reconcile`. This Sandbox has no CRM endpoint or
token configured by this change, so no CRM request is sent.

## Audit and metrics

The append-only event journal includes task ID, actor, provider, action, previous and new state, reason, event time, correlation ID, and action metadata. It supports a replay of state changes and operator decisions.

Authenticated /metrics includes queue gauges, the existing Telegram-specific counters/histogram, and durable provider-neutral counters: reconciliation_total, reconciliation_success_total, reconciliation_failed_total, delivery_confirmed_total, delivery_rejected_total, reconciliation_required_total, manual_review_total, provider_unknown_total. Values are reloaded from the event journal after process restart. See the metrics definitions in [API Reference](docs/api-reference.md).

## Testing and rollback

PostgreSQL integration tests use only the disposable CI database configured via JARVIS_WORKER_CI_DATABASE_URL. They cover delivered and undelivered confirmation, stale versions, concurrent operators, restart during review, missing provider identifiers/receipt and post-reconciliation recovery. Telegram integration may use only a dedicated test bot and test chat.

Rollback only the Sandbox worker. Keep the additive schema and audit history; never delete delivery evidence or restore an older database snapshot.


## Email Provider (isolated test sink only)

`EmailRequested` is recorded through the Orchestrator and transactional outbox.
Replay materializes one email intent; the Email Operation Worker obtains a
Policy Engine decision and sends only through the generic Dispatcher. The Email
Provider is registered only when `JARVIS_EMAIL_SANDBOX_SMTP_HOST` points to a
private test sink. Recipients and sender are restricted to `.test`/`.invalid`.
The Railway Sandbox uses a private Mailpit SMTP sink for synthetic `@example.test`
checks only; it is not a public or real mail service. No external recipients or
working SMTP services are configured. SMTP acceptance is stored as `QUEUED`; it is not proof of final
mailbox delivery. A timeout after DATA becomes `RECONCILIATION_REQUIRED` and is
never automatically resent. See the [Email Provider Guide](docs/email-provider-guide.md),
[Adapter Guide](docs/email-adapter-guide.md), [Recovery Guide](docs/email-recovery-guide.md),
and [Email metrics](docs/email-metrics.md).

~~~mermaid
sequenceDiagram
    participant API as Orchestrator API
    participant Policy as Policy Engine
    participant DB as PostgreSQL Outbox
    participant Replay as Replay Worker
    participant Disp as Dispatcher
    participant SMTP as Sandbox SMTP sink
    API->>Policy: authorize EmailRequested
    Policy->>DB: transaction: request and outbox event
    Replay->>DB: claim event and materialize intent once
    Replay->>Policy: evaluate send policy
    Policy->>Disp: authorize provider route
    Disp->>SMTP: one bounded SMTP submission
    SMTP-->>Disp: accepted or ambiguous outcome
    Disp->>DB: persist receipt or reconciliation state
~~~

Provider integration example (never wire a provider directly to Queue):

~~~python
provider = SandboxProvider(config)
dispatcher.register("sandbox-mail", provider, enabled=True)
adapter = SandboxMailAdapter()
adapter.register(event_bus)
# The operation worker calls Orchestrator.process_email_operation(...).
~~~

## Generic HTTP Provider (Sandbox only)

`HTTPProvider` is optional and is registered through the existing Dispatcher
when both `JARVIS_HTTP_SANDBOX_URL` and `JARVIS_HTTP_SANDBOX_TOKEN` are present.
The URL must identify an HTTPS Railway Sandbox/Staging service. It supports
send, update, cancel, status, reconciliation, and receipt lookup, while keeping
business decisions in the Orchestrator and Policy Engine. Mutations forward the
stable idempotency key; ambiguous network/5xx/incomplete-receipt outcomes enter
`RECONCILIATION_REQUIRED` and are never resent automatically. Redirects are
blocked and only minimal provider IDs/receipt/status fields are retained. Tasks
addressed to the `sandbox:http:` destination namespace are routed by the
Orchestrator and remain covered by the human approval fingerprint.

The Sandbox currently has no generic HTTP endpoint or token configured, so the
provider is disabled there and no external HTTP request has been made. Contract
tests use an injected synthetic response handler. See the
[HTTP Provider Guide](docs/http-provider-guide.md).

Metrics are `http_provider_requests_total`, `http_provider_success_total`,
`http_provider_failed_total`, `http_provider_unknown_total`, and
`http_provider_latency_seconds`.
