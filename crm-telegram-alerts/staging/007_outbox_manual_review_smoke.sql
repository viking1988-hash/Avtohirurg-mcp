-- Disposable CI database only. All sample rows rolled back.
BEGIN;
DO $guard$ BEGIN
 IF current_database() <> 'outbox_ci' THEN RAISE EXCEPTION 'CI database only'; END IF;
END $guard$;
INSERT INTO crm_telegram_outbox(lead_id,alert_type,status,attempts,last_error_code)
VALUES(99200001,'new','review',1,'TELEGRAM_DELIVERY_UNCERTAIN');
DO $test$
DECLARE k bigint; n integer;
BEGIN
 SELECT id INTO k FROM crm_telegram_outbox WHERE lead_id=99200001;
 BEGIN
   PERFORM crm_telegram_outbox_review_decide(k,'mark_sent','qa-operator','Evidence is insufficient',NULL);
   RAISE EXCEPTION 'Missing receipt accepted';
 EXCEPTION WHEN raise_exception THEN
   IF SQLERRM='Missing receipt accepted' THEN RAISE; END IF;
 END;
 IF (SELECT status FROM crm_telegram_outbox WHERE id=k)<>'review' THEN
   RAISE EXCEPTION 'Invalid decision changed state';
 END IF;
 PERFORM crm_telegram_outbox_review_decide(k,'mark_sent','qa-operator',
   'Verified external receipt against provider logs',123456);
 IF NOT EXISTS (SELECT 1 FROM crm_telegram_outbox WHERE id=k AND status='sent'
     AND telegram_message_id=123456) THEN RAISE EXCEPTION 'Mark sent failed'; END IF;
 SELECT count(*) INTO n FROM crm_telegram_outbox_review_audit
    WHERE outbox_id=k AND action='mark_sent' AND actor='qa-operator';
 IF n<>1 THEN RAISE EXCEPTION 'Audit missing'; END IF;
 BEGIN
   PERFORM crm_telegram_outbox_review_decide(k,'authorize_retry','qa-operator',
     'Attempted second decision on completed job');
   RAISE EXCEPTION 'Duplicate decision accepted';
 EXCEPTION WHEN raise_exception THEN
   IF SQLERRM='Duplicate decision accepted' THEN RAISE; END IF;
 END;
END $test$;
INSERT INTO crm_telegram_outbox(lead_id,alert_type,status,attempts,last_error_code)
VALUES(99200002,'new','review',2,'TELEGRAM_DELIVERY_UNCERTAIN'),
      (99200003,'new','review',5,'TELEGRAM_DELIVERY_UNCERTAIN');
DO $test$
DECLARE k bigint;
BEGIN
 SELECT id INTO k FROM crm_telegram_outbox WHERE lead_id=99200002;
 PERFORM crm_telegram_outbox_review_decide(k,'authorize_retry','qa-operator',
   'Manual investigation confirmed no delivery');
 IF NOT EXISTS (SELECT 1 FROM crm_telegram_outbox WHERE id=k
   AND status='retryable_error' AND next_attempt_at>now()) THEN
   RAISE EXCEPTION 'Authorized retry failed'; END IF;
 SELECT id INTO k FROM crm_telegram_outbox WHERE lead_id=99200003;
 BEGIN
   PERFORM crm_telegram_outbox_review_decide(k,'authorize_retry','qa-operator',
     'Manual investigation confirmed no delivery');
   RAISE EXCEPTION 'Exhausted retry accepted';
 EXCEPTION WHEN raise_exception THEN
   IF SQLERRM='Exhausted retry accepted' THEN RAISE; END IF;
 END;
 IF (SELECT status FROM crm_telegram_outbox WHERE id=k)<>'review' THEN
   RAISE EXCEPTION 'Exhausted retry changed state'; END IF;
END $test$;
ROLLBACK;
