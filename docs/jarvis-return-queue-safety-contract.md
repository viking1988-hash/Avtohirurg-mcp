# Jarvis Return Queue — safety contract

This document is a review-only specification. It does not activate any workflow or change production.

## Current template risks
- Polling every five minutes can reprocess the same OPEN or READY task.
- Preparing an approval payload is **not** evidence that approval was granted.
- A disabled Mark DONE node is not a substitute for verifying an actual external action.
- n8n expressions using `$env` require checking whether environment-variable access is enabled in the running n8n instance.

## Required state transitions
- OPEN → READY: a task is validated and an approval draft is prepared. No customer-facing action.
- READY → APPROVED: only an authenticated human approval, with approver, timestamp, action fingerprint, and expiration.
- APPROVED → PROCESSING: atomically claim a task with a lease; reject concurrent claims.
- PROCESSING → DONE: only after a confirmed, idempotent external action and recorded receipt.
- PROCESSING → RETRY / FAILED: bounded retries with backoff, explicit error category, and manual escalation.
- Any state → CANCELLED: explicit authorized cancellation; no outbound action.

Never transition OPEN/READY directly to DONE. Approval must bind to the exact payload and destination; editing either invalidates approval.

## Idempotency and audit
- Stable task_id and action_id/idempotency_key, unique per task+action+destination.
- Atomic compare-and-swap on state/version or unique DB constraint before execution.
- Persist attempt count, lease expiration, request/response outcome, actor, timestamp, and correlation ID.
- Never log credentials or unredacted personal data.
- Failed or timed-out sends must not be blindly retried without a deduplication check.

## Acceptance tests (staging only)
1. Empty queue: zero outbound actions.
2. OPEN task: one draft, zero outbound actions, zero DONE transitions.
3. READY task without approval: zero outbound actions.
4. Two concurrent polls: one claimed task at most.
5. Expired approval or changed payload: action rejected.
6. Valid approval: exactly one outbound action with receipt.
7. Timeout/retry: no duplicate external action.
8. Explicit failure: FAILED/RETRY with audit trail, not DONE.
9. Invalid MCP token: fail closed, no secret in logs.
10. CRM Booking and its databases remain untouched.

## Rollout
Keep n8n workflow inactive until Work verifies actual imported workflow ID, endpoint authentication, runtime n8n expression compatibility, and staging acceptance tests. Leave Mark DONE disabled until an end-to-end approval and delivery receipt mechanism is implemented. Do not edit the same production workflow concurrently with Work.
