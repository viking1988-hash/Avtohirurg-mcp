-- STAGING ONLY. Execute each claim in a short transaction; never hold DB lock during HTTP.
-- Expired leases are ambiguous: a Telegram send may have succeeded before crash.
-- Move them to human review instead of blindly retrying and creating duplicates.
BEGIN;
UPDATE crm_telegram_outbox
SET status='review', lease_until=NULL, lease_token=NULL,
    last_error_code='LEASE_EXPIRED_DELIVERY_UNCERTAIN', updated_at=now()
WHERE status='leased' AND lease_until < now();

-- Claim at most 20 eligible jobs; concurrent workers skip locked rows.
WITH candidate AS (
  SELECT id FROM crm_telegram_outbox
  WHERE status IN ('pending','retryable_error')
    AND next_attempt_at <= now() AND attempts < 5
  ORDER BY next_attempt_at, id
  LIMIT 20
  FOR UPDATE SKIP LOCKED
)
UPDATE crm_telegram_outbox o
SET status='leased', lease_until=now()+interval '2 minutes',
    lease_token=gen_random_uuid(), attempts=o.attempts+1, updated_at=now()
FROM candidate
WHERE o.id=candidate.id
RETURNING o.id,o.lead_id,o.alert_type,o.lease_token,o.attempts;
COMMIT;

-- After Telegram confirms success, update with BOTH id and lease_token:
-- UPDATE crm_telegram_outbox SET status='sent',sent_at=now(),
--   telegram_message_id=$3,lease_until=NULL,lease_token=NULL,updated_at=now()
-- WHERE id=$1 AND lease_token=$2 AND status='leased';
--
-- On explicit Telegram rejection where delivery is CERTAINLY absent:
-- UPDATE crm_telegram_outbox SET status='retryable_error',
--   next_attempt_at=now()+interval '5 minutes',lease_until=NULL,
--   lease_token=NULL,last_error_code='TELEGRAM_REJECTED',updated_at=now()
-- WHERE id=$1 AND lease_token=$2 AND status='leased';
--
-- On timeout or uncertain network failure: mark status='review' rather
-- than automatically retry. A manual reconciliation step is required.
