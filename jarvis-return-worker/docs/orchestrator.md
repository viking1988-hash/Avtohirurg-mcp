# Jarvis Orchestrator

The Orchestrator is the only application boundary between workers/APIs and delivery providers. It coordinates claim, approval, send marker, provider call, receipt persistence, terminal transition, recovery, reconciliation, cancellation and audit-facing reads. Providers receive a task and idempotency key; they never receive a Queue repository.

## Component architecture

~~~mermaid
flowchart TD
    API["Protected API"] --> ORCH["Orchestrator"]
    WORKER["Worker"] --> ORCH
    ORCH --> QUEUE["Queue Repository"]
    ORCH --> POLICY["Policy Engine"]
    ORCH --> DISPATCH["Transport Dispatcher"]
    DISPATCH --> PROVIDER["Provider Adapter"]
    ORCH --> BUS["Event Bus"]
    QUEUE --> AUDIT["PostgreSQL audit journal"]
~~~

The journal and task state are authoritative. Event Bus observers receive in-process notifications; observer failure does not affect the delivery transition or trigger a send retry. Consumers can inspect the durable audit journal after a restart.

## Delivery pipeline

~~~mermaid
stateDiagram-v2
    [*] --> READY
    READY --> APPROVED: valid human approval
    APPROVED --> PROCESSING: fenced claim
    PROCESSING --> PROCESSING: durable send marker
    PROCESSING --> DONE: provider receipt saved
    PROCESSING --> APPROVED: confirmed provider rejection and retry budget
    PROCESSING --> FAILED: definitive failure
    PROCESSING --> RECONCILIATION_REQUIRED: outcome is ambiguous
    RECONCILIATION_REQUIRED --> DONE: operator confirms delivery
    RECONCILIATION_REQUIRED --> READY: operator confirms non-delivery
    READY --> CANCELLED: operator cancellation
~~~

Queue persistence validates approval and lease fencing. The Orchestrator commits the send marker before calling a provider and persists its receipt/message ID before DONE. An unknown result never returns to the retry path. Confirmed non-delivery requires a new approval before sending again.

## Dispatcher and providers

TransportDispatcher.register(name, provider, enabled=...) adds a provider without editing the Orchestrator. Unknown or disabled providers fail closed. The current Sandbox boot registers only the configured provider; its deployment remains on fake.

The provider contract is:

- send(task, idempotency_key) -> receipt
- get_receipt(provider_message_id)
- check_status(provider_message_id)
- reconcile(task)
- cancel(provider_message_id)

ProviderAdapter allows legacy protocol clients to be wrapped while the interfaces migrate. It provides conservative defaults for unavailable status, reconcile, and cancel operations. It does not claim a delivery outcome based on absence of support.

### Add a transport

1. Implement the provider methods above in a protocol client. Keep provider-specific request/response parsing there; do not access Queue, approve, retry, or decide whether to resend.
2. Return a receipt and provider message identifier only when the provider confirms them. Raise DeliveryError(UNKNOWN, ...) on timeouts or unclear results; only use RETRYABLE when a provider definitively rejected the request.
3. Register the provider by name with the Dispatcher. Keep it disabled until credentials and an isolated test destination are configured.
4. Add tests for send, receipt lookup, status, cancel, definitive rejection, unknown result, and restart/recovery. Verify no ambiguous result can create a second send.
5. Deploy to Railway Sandbox only after CI passes. Production and CRM Booking remain separate.

## Policy Engine

PolicyEngine is the only place that selects fail, retry, reconciliation, or safe resend after a provider result. Resend is eligible only for an explicit RETRYABLE classification and no durable receipt. Permanent rejection fails the task. Timeout, transport loss, missing receipt, and unclassified failure after the send marker require reconciliation. Approval integrity remains checked before send.

## Event Bus

Lifecycle events are appended to PostgreSQL's transactional outbox in the same
transaction as the queue/reconciliation state and audit journal. The process-local
publish/deduplication path has been removed. Event names and payloads are versioned;
replay workers use per-subscriber delivery rows, PostgreSQL row locks, and a unique
processed-event marker. See the [Transactional Outbox guide](transactional-outbox.md)
for crash recovery and the exact-once transaction boundary.

## Metrics

The authenticated /metrics endpoint exports:

| Metric | Type | Meaning |
|---|---|---|
| orchestrator_tasks_total | Counter by outcome | Completed pipeline attempts |
| orchestrator_pipeline_seconds | Histogram | End-to-end processing duration |
| transport_dispatch_total | Counter by provider | Provider dispatch attempts |
| policy_retry_total | Counter | Centrally approved retries |
| policy_reconcile_total | Counter | Ambiguous outcomes sent to reconciliation |
| event_bus_events_total | Counter by event | Event notifications published |
| dispatcher_failures_total | Counter by reason | Unknown or disabled provider dispatch failures |
| provider_calls_total | Counter by provider and method | Calls through provider abstraction |

Provider-neutral reconciliation counters from PR #35 remain available alongside these metrics.


The durable outbox metrics are documented in the [API Reference](api-reference.md).
