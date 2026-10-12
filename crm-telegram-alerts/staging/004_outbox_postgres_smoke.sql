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
-- Verify retry policy, state constraints and success acknowledgement.
DO $outbox$
DECLARE k bigint; t uuid; n integer;
BEGIN
  INSERT INTO crm_telegram_outbox(lead_id,alert_type,status,attempts,next_attempt_at)
  VALUES (90000002,'overdue','retryable_error',4,now()-interval '1 minute')
  RETURNING id INTO k;
  UPDATE crm_telegram_outbox SET status='leased',attempts=attempts+1,
    lease_until=now()+interval '2 minutes',lease_token=gen_random_uuid()
  WHERE id=k AND status='retryable_error' AND attempts<5
  RETURNING lease_token,attempts INTO t,n;
  IF t IS NULL OR n<>5 THEN RAISE EXCEPTION 'final retry claim failed'; END IF;
  UPDATE crm_telegram_outbox SET status='sent',sent_at=now(),
    telegram_message_id=987654321,lease_until=NULL,lease_token=NULL
  WHERE id=k AND status='leased' AND lease_token=t;
  IF NOT FOUND THEN RAISE EXCEPTION 'acknowledgement failed'; END IF;
  IF NOT EXISTS(SELECT 1 FROM crm_telegram_outbox WHERE id=k AND
    status='sent' AND telegram_message_id=987654321 AND sent_at IS NOT NULL)
  THEN RAISE EXCEPTION 'sent receipt missing'; END IF;
  BEGIN
    INSERT INTO crm_telegram_outbox(lead_id,alert_type,status)
    VALUES(90000003,'new','leased');
    RAISE EXCEPTION 'invalid lease state was accepted';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
  INSERT INTO crm_telegram_outbox(lead_id,alert_type,status,attempts,next_attempt_at)
  VALUES(90000004,'new','retryable_error',5,now()-interval '1 minute')
  RETURNING id INTO k;
  UPDATE crm_telegram_outbox SET status='leased',lease_until=now()+interval '2 minutes',
    lease_token=gen_random_uuid(),attempts=attempts+1
  WHERE id=k AND status='retryable_error' AND attempts<5;
  IF FOUND THEN RAISE EXCEPTION 'exhausted retry was claimed'; END IF;
END $outbox$;
-- Expired lease must be quarantined, not automatically retried.
DO $expiry$
DECLARE k bigint; affected integer;
BEGIN
  INSERT INTO crm_telegram_outbox
    (lead_id,alert_type,status,attempts,lease_token,lease_until)
  VALUES
    (90000005,'new','leased',1,gen_random_uuid(),now()-interval '1 minute')
  RETURNING id INTO k;
  UPDATE crm_telegram_outbox
  SET status='review',lease_until=NULL,lease_token=NULL,
    last_error_code='LEASE_EXPIRED_DELIVERY_UNCERTAIN'
  WHERE status='leased' AND lease_until<now();
  GET DIAGNOSTICS affected = ROW_COUNT;
  IF affected<1 THEN RAISE EXCEPTION 'expired lease not quarantined'; END IF;
  IF NOT EXISTS(SELECT 1 FROM crm_telegram_outbox WHERE id=k
      AND status='review' AND lease_token IS NULL)
  THEN RAISE EXCEPTION 'expired lease remains eligible'; END IF;
  UPDATE crm_telegram_outbox SET status='leased',attempts=attempts+1,
    lease_token=gen_random_uuid(),lease_until=now()+interval '2 minutes'
  WHERE id=k AND status IN ('pending','retryable_error') AND attempts<5;
  IF FOUND THEN RAISE EXCEPTION 'uncertain delivery was automatically retried'; END IF;
END $expiry$;
-- Explicit rejection can be retried; uncertain delivery must be reviewed.
DO $delivery$
DECLARE k bigint; t uuid;
BEGIN
  INSERT INTO crm_telegram_outbox(lead_id,alert_type,status,attempts,
      lease_until,lease_token)
  VALUES(90000006,'new','leased',1,now()+interval '2 minutes',gen_random_uuid())
  RETURNING id,lease_token INTO k,t;
  UPDATE crm_telegram_outbox SET status='retryable_error',
      next_attempt_at=now()+interval '5 minutes',lease_until=NULL,
      lease_token=NULL,last_error_code='TELEGRAM_REJECTED'
  WHERE id=k AND status='leased' AND lease_token=t;
  IF NOT FOUND THEN RAISE EXCEPTION 'explicit rejection not recorded'; END IF;
  IF NOT EXISTS(SELECT 1 FROM crm_telegram_outbox WHERE id=k
      AND status='retryable_error' AND next_attempt_at>now()
      AND lease_token IS NULL)
  THEN RAISE EXCEPTION 'retry backoff missing'; END IF;
  UPDATE crm_telegram_outbox SET status='leased',attempts=attempts+1,
      lease_until=now()+interval '2 minutes',lease_token=gen_random_uuid()
  WHERE id=k AND status='retryable_error' AND next_attempt_at<=now();
  IF FOUND THEN RAISE EXCEPTION 'backoff ignored'; END IF;
  INSERT INTO crm_telegram_outbox(lead_id,alert_type,status,attempts,
      lease_until,lease_token)
  VALUES(90000007,'new','leased',1,now()+interval '2 minutes',gen_random_uuid())
  RETURNING id,lease_token INTO k,t;
  UPDATE crm_telegram_outbox SET status='review',lease_until=NULL,
      lease_token=NULL,last_error_code='TELEGRAM_DELIVERY_UNCERTAIN'
  WHERE id=k AND status='leased' AND lease_token=t;
  IF NOT FOUND THEN RAISE EXCEPTION 'uncertain delivery review failed'; END IF;
  IF NOT EXISTS(SELECT 1 FROM crm_telegram_outbox WHERE id=k AND status='review')
  THEN RAISE EXCEPTION 'uncertain delivery was lost'; END IF;
END $delivery$;
-- Receipt acknowledgement must be idempotent and reject stale tokens.
DO $ack$
DECLARE k bigint; t uuid; count_changed integer;
BEGIN
  INSERT INTO crm_telegram_outbox(lead_id,alert_type,status,attempts,
    lease_until,lease_token)
  VALUES(90000008,'new','leased',1,now()+interval '2 minutes',gen_random_uuid())
  RETURNING id,lease_token INTO k,t;
  UPDATE crm_telegram_outbox SET status='sent',sent_at=now(),
    telegram_message_id=12345,lease_until=NULL,lease_token=NULL
  WHERE id=k AND status='leased' AND lease_token=t;
  GET DIAGNOSTICS count_changed=ROW_COUNT;
  IF count_changed<>1 THEN RAISE EXCEPTION 'initial receipt failed'; END IF;
  UPDATE crm_telegram_outbox SET status='sent',telegram_message_id=99999
  WHERE id=k AND status='leased' AND lease_token=t;
  GET DIAGNOSTICS count_changed=ROW_COUNT;
  IF count_changed<>0 THEN RAISE EXCEPTION 'duplicate receipt changed row'; END IF;
  IF NOT EXISTS(SELECT 1 FROM crm_telegram_outbox WHERE id=k
    AND status='sent' AND telegram_message_id=12345)
  THEN RAISE EXCEPTION 'original receipt was overwritten'; END IF;
END $ack$;
ROLLBACK;
