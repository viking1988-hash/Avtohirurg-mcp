-- Additive Delivery v2 schema. Only apply to a disposable/staging database during tests.
-- Never apply to Production without a separately reviewed migration and backup.
-- Historical crm_telegram_alert_log rows are deliberately not copied or reclassified.
BEGIN;
CREATE TABLE IF NOT EXISTS crm_telegram_delivery_v2 (
 lead_id bigint NOT NULL,
 alert_type text NOT NULL,
 state text NOT NULL CHECK (state IN ('pending','sending','sent','rejected','uncertain')),
 attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
 manual_resend_authorized boolean NOT NULL DEFAULT false,
 claimed_at timestamptz,
 last_attempt_at timestamptz,
 next_attempt_at timestamptz,
 sent_at timestamptz,
 telegram_message_id bigint,
 last_error_class text,
 updated_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY (lead_id, alert_type)
);
CREATE INDEX IF NOT EXISTS crm_telegram_delivery_v2_due_idx
 ON crm_telegram_delivery_v2 (next_attempt_at, updated_at)
 WHERE state IN ('pending','rejected');
CREATE INDEX IF NOT EXISTS crm_telegram_delivery_v2_review_idx
 ON crm_telegram_delivery_v2 (updated_at)
 WHERE state='uncertain';
CREATE TABLE IF NOT EXISTS crm_telegram_delivery_operator_audit (
 audit_id bigserial PRIMARY KEY,
 lead_id bigint NOT NULL,
 alert_type text NOT NULL,
 action text NOT NULL CHECK (action IN ('confirmed_delivered','authorized_resend')),
 operator_id text NOT NULL CHECK (length(operator_id) BETWEEN 1 AND 120),
 reason text NOT NULL CHECK (length(reason) BETWEEN 1 AND 1000),
 duplicate_risk_accepted boolean NOT NULL DEFAULT false,
 created_at timestamptz NOT NULL DEFAULT now()
);
COMMIT;
