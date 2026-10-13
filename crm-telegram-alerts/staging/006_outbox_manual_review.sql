-- STAGING ONLY. Additive operator decision log. Do not apply to production.
BEGIN;
CREATE TABLE IF NOT EXISTS crm_telegram_outbox_review_audit (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  outbox_id bigint NOT NULL REFERENCES crm_telegram_outbox(id),
  action text NOT NULL CHECK (action IN ('mark_sent','authorize_retry')),
  actor text NOT NULL CHECK (length(btrim(actor)) BETWEEN 3 AND 128),
  reason text NOT NULL CHECK (length(btrim(reason)) BETWEEN 10 AND 1000),
  previous_status text NOT NULL DEFAULT 'review',
  decided_at timestamptz NOT NULL DEFAULT now(),
  receipt_message_id bigint,
  CHECK ((action = 'mark_sent' AND receipt_message_id > 0)
      OR (action = 'authorize_retry' AND receipt_message_id IS NULL))
);
-- No public execution grant. A privileged operator must call explicitly.
CREATE OR REPLACE FUNCTION crm_telegram_outbox_review_decide(
  p_outbox_id bigint, p_action text, p_actor text, p_reason text,
  p_receipt_message_id bigint DEFAULT NULL
) RETURNS void LANGUAGE plpgsql AS $review$
DECLARE v_attempts integer;
BEGIN
  IF current_database() NOT IN ('outbox_ci','outbox_sandbox') THEN
    RAISE EXCEPTION 'Disposable database only';
  END IF;
  IF p_action NOT IN ('mark_sent','authorize_retry')
     OR p_action IS NULL OR length(btrim(coalesce(p_actor,''))) NOT BETWEEN 3 AND 128
     OR length(btrim(coalesce(p_reason,''))) NOT BETWEEN 10 AND 1000 THEN
    RAISE EXCEPTION 'Explicit action, actor and reason required';
  END IF;
  IF (p_action='mark_sent' AND (p_receipt_message_id IS NULL OR p_receipt_message_id<=0))
     OR (p_action='authorize_retry' AND p_receipt_message_id IS NOT NULL) THEN
    RAISE EXCEPTION 'Invalid receipt for review decision';
  END IF;
  SELECT attempts INTO v_attempts FROM crm_telegram_outbox
    WHERE id=p_outbox_id AND status='review' FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Outbox job is not in review'; END IF;
  IF p_action='authorize_retry' AND v_attempts>=5 THEN
    RAISE EXCEPTION 'Retry ceiling reached';
  END IF;
  IF p_action='mark_sent' THEN
    UPDATE crm_telegram_outbox SET status='sent',sent_at=now(),
      telegram_message_id=p_receipt_message_id,last_error_code=NULL,
      lease_until=NULL,lease_token=NULL,updated_at=now() WHERE id=p_outbox_id;
  ELSE
    UPDATE crm_telegram_outbox SET status='retryable_error',
      next_attempt_at=now()+interval '5 minutes',
      last_error_code='MANUAL_RETRY_AUTHORIZED',lease_until=NULL,
      lease_token=NULL,updated_at=now() WHERE id=p_outbox_id;
  END IF;
  INSERT INTO crm_telegram_outbox_review_audit
    (outbox_id,action,actor,reason,receipt_message_id)
  VALUES (p_outbox_id,p_action,p_actor,p_reason,p_receipt_message_id);
END $review$;
REVOKE ALL ON FUNCTION crm_telegram_outbox_review_decide(bigint,text,text,text,bigint) FROM PUBLIC;
COMMIT;
