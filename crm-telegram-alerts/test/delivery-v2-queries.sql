-- Disposable PostgreSQL only: never run against CRM Production.
-- Explicit worker claim and crash-reconciliation primitives for delivery v2.
-- Caller must execute inside a transaction and bind :lead_id / :alert_type.
-- Claim is conditional and atomic: a second worker cannot claim the same pending row.
UPDATE crm_telegram_delivery_v2
SET state='sending',
    attempt_count=attempt_count+1,
    claimed_at=clock_timestamp(),
    last_attempt_at=clock_timestamp(),
    updated_at=clock_timestamp()
WHERE lead_id=:lead_id
  AND alert_type=:alert_type
  AND state='pending'
RETURNING lead_id, alert_type, attempt_count;

-- Run as a separate maintenance operation, NEVER as an automatic resend.
-- Do not silently turn stale attempts back into pending.
UPDATE crm_telegram_delivery_v2
SET state='uncertain',
    last_error_class='stalled_sending_requires_review',
    updated_at=clock_timestamp()
WHERE state='sending'
  AND claimed_at < clock_timestamp() - interval '15 minutes'
RETURNING lead_id, alert_type, attempt_count, claimed_at;
