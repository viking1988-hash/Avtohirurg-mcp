# Manual Reconciliation Guide

1. Read GET /queue/{task_id} and GET /queue/{task_id}/delivery; note version, provider, delivery status, and any message or receipt identifiers.
2. Inspect the provider's authoritative history for the isolated test destination. A timeout, worker crash, HTTP 5xx, or missing local receipt is not proof of non-delivery.
3. Start review with POST /queue/{task_id}/reconcile, the observed expected_version, a concise reason and optional comment.
4. If provider evidence proves delivery, call POST /confirm-delivered with provider and any known provider identifiers. If an ID or receipt was lost, document the evidence in comment; never invent an identifier.
5. If evidence proves non-delivery, call POST /confirm-undelivered. The task enters UNDELIVERED_CONFIRMED and remains non-sendable.
6. Only when a new send is still wanted, call POST /request-new-approval. The old approval and send marker are cleared, the previous provider evidence remains in the audit journal, and a new READY task must receive a fresh human approval.
7. Read task and events again. Confirm the incremented version, final state, actor, provider, reason, correlation ID and audit ordering.

If any POST returns 409, do not repeat the old request blindly. Fetch the current task and events; a competing operator may have completed the action. Duplicate provider IDs and receipts are rejected by database uniqueness constraints.
