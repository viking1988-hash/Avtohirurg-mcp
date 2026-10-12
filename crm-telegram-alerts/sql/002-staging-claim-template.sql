-- Claim one row atomically; execute with parameters $1=worker ID, $2=lease seconds.
-- Do not auto-claim expired sending rows: ambiguous Telegram outcome requires reconciliation.
WITH candidate AS (
  SELECT id FROM crm_telegram_delivery_queue
  WHERE state IN ('pending','retryable') AND attempts < 5
  ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT 1
)
UPDATE crm_telegram_delivery_queue AS q
SET state='sending',attempts=q.attempts+1,lease_owner=$1,
    lease_until=now()+make_interval(secs => $2::int),updated_at=now()
FROM candidate WHERE q.id=candidate.id
RETURNING q.*;

-- On confirmed Telegram success, record the returned message_id:
-- UPDATE crm_telegram_delivery_queue
-- SET state='sent',telegram_message_id=$2,sent_at=now(),lease_owner=NULL,lease_until=NULL,updated_at=now()
-- WHERE id=$1 AND state='sending' AND lease_owner=$3;
--
-- On uncertain network outcome, mark 'uncertain', NOT 'retryable'.
-- A separate operator reconciliation is required before another attempt.
