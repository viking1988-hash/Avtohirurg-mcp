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
