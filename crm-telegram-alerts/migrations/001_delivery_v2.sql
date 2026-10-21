-- Delivery v2 schema candidate. APPLY ONLY to disposable/staging PostgreSQL after review.
-- Production migration is NOT authorized by this file.
BEGIN;
CREATE TABLE IF NOT EXISTS crm_telegram_delivery_v2 (
 lead_id bigint NOT NULL,
 alert_type text NOT NULL,
 state text NOT NULL CHECK (state IN ('pending','sending','sent','rejected','uncertain')),
 attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
 claimed_at timestamptz,
 last_attempt_at timestamptz,
 next_attempt_at timestamptz,
 sent_at timestamptz,
 telegram_message_id bigint,
 last_error_class text,
 updated_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY (lead_id,alert_type)
);
CREATE INDEX IF NOT EXISTS crm_telegram_delivery_v2_due_idx
 ON crm_telegram_delivery_v2 (next_attempt_at, updated_at)
 WHERE state IN ('pending','rejected');
CREATE INDEX IF NOT EXISTS crm_telegram_delivery_v2_review_idx
 ON crm_telegram_delivery_v2 (updated_at)
 WHERE state='uncertain';
COMMIT;
