# Telegram delivery crash-window — safety decision (2026-10-12)

## Verified code behavior
- The worker inserts (lead_id, alert_type) into crm_telegram_alert_log **before** calling Telegram.
- On a normal HTTP/API failure it deletes the claim so the next run can retry.
- On process termination, host restart, SIGKILL, or database disconnect after the insert, that delete may never run. Future runs see ON CONFLICT and skip the alert permanently.
- A network timeout may occur **after Telegram delivered** a message, so retrying can duplicate it. Telegram sendMessage has no application-level idempotency key.

## Proposed remediation (not yet deployed)
1. Use a dedicated transactional outbox table with states pending, leased, sent, retryable_error; include a unique (lead_id, alert_type), attempt counter, next_attempt_at, lease_until, sent_at, telegram_message_id, and error code.
2. Enqueue alerts transactionally; use a short lease and SKIP LOCKED for concurrent workers; recover expired leases on next scheduled run.
3. After Telegram confirms success, mark sent with message_id. On retryable failure, schedule bounded exponential backoff. Avoid retry storms and keep a dead-letter/manual-review state.
4. Treat ambiguous timeouts as uncertain, not proven undelivered; alert a human for reconciliation if duplicate messages are unacceptable.
5. Add isolated tests for crash before fetch, crash after Telegram success before DB commit, concurrent worker claims, expired lease recovery, rate limits, and retry caps.
6. Back up the production database, verify migration and rollback in staging, then roll out with an explicit approval gate. Do not rewrite the existing log table in place without a migration.

## Operational limits
The current PR #31 only adds failure exit, Telegram timeout and tests; it does not close the crash window. Existing production behavior remains unchanged.
