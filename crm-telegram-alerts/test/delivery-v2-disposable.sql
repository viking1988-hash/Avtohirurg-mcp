-- Disposable PostgreSQL only. Do not run against Production.
BEGIN;
CREATE TEMP TABLE crm_telegram_delivery_v2 (
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
 PRIMARY KEY (lead_id, alert_type)
);
INSERT INTO crm_telegram_delivery_v2 (lead_id,alert_type,state) VALUES (900001,'new','pending');
DO $$
DECLARE n integer;
BEGIN
 UPDATE crm_telegram_delivery_v2 SET state='sending',attempt_count=attempt_count+1,claimed_at=now(),last_attempt_at=now() WHERE lead_id=900001 AND alert_type='new' AND state='pending';
 GET DIAGNOSTICS n = ROW_COUNT;
 IF n<>1 THEN RAISE EXCEPTION 'claim failed'; END IF;
 UPDATE crm_telegram_delivery_v2 SET state='uncertain',last_error_class='timeout' WHERE lead_id=900001 AND state='sending';
 IF NOT EXISTS (SELECT 1 FROM crm_telegram_delivery_v2 WHERE lead_id=900001 AND state='uncertain' AND attempt_count=1) THEN RAISE EXCEPTION 'uncertain transition failed'; END IF;
 UPDATE crm_telegram_delivery_v2 SET state='sending' WHERE lead_id=900001 AND state='pending';
 GET DIAGNOSTICS n = ROW_COUNT;
 IF n<>0 THEN RAISE EXCEPTION 'unsafe automatic retry'; END IF;
END $$;
ROLLBACK;
