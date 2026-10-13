-- Staging-only proposal. Do NOT run in production without backup and approval.
-- Independent from crm_telegram_alert_log; no destructive migrations.
BEGIN;
CREATE TABLE IF NOT EXISTS crm_telegram_outbox (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  lead_id bigint NOT NULL,
  alert_type text NOT NULL CHECK (alert_type IN ('new','overdue')),
  status text NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending','leased','sent','retryable_error','review')),
  attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  next_attempt_at timestamptz NOT NULL DEFAULT now(),
  lease_until timestamptz,
  lease_token uuid,
  sent_at timestamptz,
  telegram_message_id bigint,
  last_error_code text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (lead_id, alert_type),
  CHECK ((status = 'leased') = (lease_until IS NOT NULL AND lease_token IS NOT NULL)),
  CHECK (status <> 'sent' OR sent_at IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS crm_telegram_outbox_ready_idx
  ON crm_telegram_outbox (next_attempt_at, id)
  WHERE status IN ('pending','retryable_error');
CREATE INDEX IF NOT EXISTS crm_telegram_outbox_expired_lease_idx
  ON crm_telegram_outbox (lease_until)
  WHERE status = 'leased';
COMMIT;
