-- Experimental migration for an ISOLATED staging database only.
-- Never execute against production without an approved rollout and backfill.
BEGIN;
CREATE TABLE IF NOT EXISTS crm_telegram_delivery_queue (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  lead_id bigint NOT NULL,
  alert_type text NOT NULL CHECK (alert_type IN ('new','overdue')),
  state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','sending','retryable','sent','uncertain','rejected')),
  attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0 AND attempts <= 5),
  lease_owner text,
  lease_until timestamptz,
  telegram_message_id bigint,
  last_error text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  sent_at timestamptz,
  CONSTRAINT crm_telegram_delivery_unique UNIQUE(lead_id,alert_type),
  CONSTRAINT crm_telegram_sent_requires_receipt CHECK (state <> 'sent' OR (telegram_message_id IS NOT NULL AND sent_at IS NOT NULL)),
  CONSTRAINT crm_telegram_sending_requires_lease CHECK (state <> 'sending' OR (lease_owner IS NOT NULL AND lease_until IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS crm_telegram_delivery_queue_ready_idx
  ON crm_telegram_delivery_queue(state,created_at)
  WHERE state IN ('pending','retryable');
COMMIT;

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
