-- CI disposable PostgreSQL only. Transaction rollback ensures no persistent test rows.
BEGIN;
INSERT INTO crm_telegram_outbox (lead_id,alert_type) VALUES (90000001,'new');
DO $$
BEGIN
  BEGIN
    INSERT INTO crm_telegram_outbox (lead_id,alert_type) VALUES (90000001,'new');
    RAISE EXCEPTION 'unique key was not enforced';
  EXCEPTION WHEN unique_violation THEN NULL;
  END;
END $$;
DO $$
DECLARE job_id bigint; token uuid; attempts_now integer; leased_count integer;
BEGIN
  WITH candidate AS (
    SELECT id FROM crm_telegram_outbox
    WHERE status='pending' AND next_attempt_at<=now()
    ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED
  )
  UPDATE crm_telegram_outbox o
  SET status='leased',lease_until=now()+interval '2 minutes',
      lease_token=gen_random_uuid(),attempts=attempts+1
  FROM candidate WHERE o.id=candidate.id
  RETURNING o.id,o.lease_token,o.attempts INTO job_id,token,attempts_now;
  IF job_id IS NULL OR token IS NULL OR attempts_now<>1 THEN
    RAISE EXCEPTION 'claim failed';
  END IF;
  SELECT count(*) INTO leased_count FROM crm_telegram_outbox
  WHERE id=job_id AND status='leased';
  IF leased_count<>1 THEN RAISE EXCEPTION 'leased state missing'; END IF;
  UPDATE crm_telegram_outbox SET status='review',lease_until=NULL,
      lease_token=NULL,last_error_code='LEASE_EXPIRED_DELIVERY_UNCERTAIN'
  WHERE id=job_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'review transition failed'; END IF;
  UPDATE crm_telegram_outbox SET status='sent',sent_at=now()
  WHERE id=job_id AND status='leased' AND lease_token=token;
  IF FOUND THEN RAISE EXCEPTION 'stale lease token was accepted'; END IF;
END $$;
ROLLBACK;
