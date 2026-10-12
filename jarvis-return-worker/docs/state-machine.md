# Jarvis Delivery State Machine

~~~mermaid
stateDiagram-v2
    [*] --> READY
    READY --> APPROVED: operator approval
    APPROVED --> PROCESSING: fenced claim
    PROCESSING --> DONE: receipt committed
    PROCESSING --> FAILED: definitive permanent rejection
    PROCESSING --> APPROVED: definitive retryable rejection
    PROCESSING --> RECONCILIATION_REQUIRED: ambiguous outcome
    PROCESSING --> APPROVED: lease expired before send marker
    PROCESSING --> RECONCILIATION_REQUIRED: lease expired after marker
    RECONCILIATION_REQUIRED --> DELIVERED_CONFIRMED: operator confirms delivery
    RECONCILIATION_REQUIRED --> UNDELIVERED_CONFIRMED: operator confirms non-delivery
    DELIVERED_CONFIRMED --> [*]
    UNDELIVERED_CONFIRMED --> READY: request fresh approval
    READY --> CANCELLED: operator cancellation
    FAILED --> CANCELLED: operator cancellation
~~~

DONE remains the worker's receipt-complete state. DELIVERED_CONFIRMED and UNDELIVERED_CONFIRMED are explicit operator outcomes. Every mutating reconciliation operation requires expected_version; row locking and the version comparison reject stale and concurrent writers. RECONCILIATION_REQUIRED and both confirmed states are excluded from worker claims. An undelivered confirmation alone never authorizes a resend; it must be followed by a fresh approval of the reset READY action.

Delivery fields track provider evidence independently of queue state. Reconciliation status progresses through REQUIRED, IN_REVIEW, and a confirmed status, or APPROVAL_REQUESTED. Unknown provider outcomes are never retried automatically.
