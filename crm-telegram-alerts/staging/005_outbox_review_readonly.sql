-- STAGING ONLY. Operator review inventory; SELECT only, no PII and no state changes.
-- Use only after verifying current_database() is outbox_ci or outbox_sandbox.
BEGIN READ ONLY;
DO $audit_guard$
BEGIN
  IF current_database() NOT IN ('outbox_ci', 'outbox_sandbox') THEN
    RAISE EXCEPTION 'Refusing outbox audit on non-disposable database: %', current_database();
  END IF;
END $audit_guard$;
SELECT current_database() AS database_name, current_user AS database_user;
SELECT status, last_error_code, count(*)::integer AS jobs,
       min(created_at) AS oldest_job,
       min(updated_at) AS oldest_update
FROM crm_telegram_outbox
WHERE status IN ('review','retryable_error','leased')
GROUP BY status,last_error_code ORDER BY status,last_error_code;
SELECT count(*)::integer AS ambiguous_expired_leases
FROM crm_telegram_outbox
WHERE status='leased' AND lease_until<now();
SELECT count(*)::integer AS review_missing_error_code
FROM crm_telegram_outbox
WHERE status='review' AND last_error_code IS NULL;
SELECT count(*)::integer AS sent_missing_receipt
FROM crm_telegram_outbox
WHERE status='sent' AND (telegram_message_id IS NULL OR sent_at IS NULL);
SELECT count(*)::integer AS retry_exhausted
FROM crm_telegram_outbox
WHERE status='retryable_error' AND attempts>=5;
COMMIT;
