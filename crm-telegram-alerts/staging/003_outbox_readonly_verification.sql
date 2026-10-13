-- STAGING ONLY: read-only checks; safe to execute before any migration.
-- Expected: one row for each catalogued table/column after migration.
SELECT table_name,column_name,data_type,is_nullable
FROM information_schema.columns
WHERE table_schema=current_schema()
  AND table_name='crm_telegram_outbox'
ORDER BY ordinal_position;

SELECT indexname,indexdef
FROM pg_indexes
WHERE schemaname=current_schema()
  AND tablename='crm_telegram_outbox'
ORDER BY indexname;

SELECT conname,contype,pg_get_constraintdef(oid) AS definition
FROM pg_constraint
WHERE conrelid=to_regclass('crm_telegram_outbox')
ORDER BY conname;

-- Check no duplicate delivery keys.
SELECT lead_id,alert_type,count(*) AS duplicate_count
FROM crm_telegram_outbox
GROUP BY lead_id,alert_type HAVING count(*)>1;

-- State inventory; no customer data or secrets returned.
SELECT status,count(*) AS total,min(created_at) AS oldest_created
FROM crm_telegram_outbox GROUP BY status ORDER BY status;

-- Inspect stalled leases without changing state.
SELECT count(*) AS expired_lease_count
FROM crm_telegram_outbox
WHERE status='leased' AND lease_until<now();

-- Inspect exhausted retries without changing state.
SELECT count(*) AS exhausted_retry_count
FROM crm_telegram_outbox
WHERE status='retryable_error' AND attempts>=5;
