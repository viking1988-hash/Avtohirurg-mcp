#!/usr/bin/env python3
"""Kill a real PostgreSQL backend after a committed lease, before any mock delivery.

This deliberately models a crash *before* an external send. A crash after sending
is ambiguous and MUST go to manual review; this test does not prove exactly-once Telegram.
Only disposable CI PostgreSQL. No Telegram network calls or credentials.
"""
import os
import psycopg

assert os.getenv("OUTBOX_STAGING_MOCK") == "true", "Staging guard required"
assert not os.getenv("TELEGRAM_BOT_TOKEN") and not os.getenv("TELEGRAM_CHAT_ID")
dsn = os.environ["DATABASE_URL"]
with psycopg.connect(dsn) as setup:
    with setup.cursor() as cur:
        cur.execute("SELECT current_database()")
        assert cur.fetchone()[0] == "outbox_ci", "CI database only"
        cur.execute("INSERT INTO crm_telegram_outbox(lead_id,alert_type) VALUES (90000191,'new')")
    setup.commit()

try:
    crashed = psycopg.connect(dsn)
    with crashed.cursor() as cur:
        cur.execute("""
          UPDATE crm_telegram_outbox SET status='leased',attempts=1,
          lease_token=gen_random_uuid(),lease_until=now()+interval '2 minutes'
          WHERE lead_id=90000191 AND status='pending'
          RETURNING lease_token
        """)
        old_token = cur.fetchone()[0]
    crashed.commit()
    pid = crashed.info.backend_pid

    # Kill the worker's actual DB backend after lease COMMIT, before fake send.
    with psycopg.connect(dsn, autocommit=True) as killer:
        with killer.cursor() as cur:
            cur.execute("SELECT pg_terminate_backend(%s)", (pid,))
            assert cur.fetchone()[0] is True
    try:
        crashed.close()
    except psycopg.Error:
        pass

    with psycopg.connect(dsn) as verifier:
        with verifier.cursor() as cur:
            cur.execute("SELECT status,attempts,lease_token FROM crm_telegram_outbox WHERE lead_id=90000191")
            status,attempts,token = cur.fetchone()
            assert (status,attempts,token)==('leased',1,old_token)
            # Simulate time passing and the conservative lease reaper.
            cur.execute("""
              UPDATE crm_telegram_outbox SET lease_until=now()-interval '1 second'
              WHERE lead_id=90000191 AND lease_token=%s
            """, (old_token,))
            cur.execute("""
              UPDATE crm_telegram_outbox SET status='review',lease_until=NULL,lease_token=NULL,
              last_error_code='LEASE_EXPIRED_DELIVERY_UNCERTAIN'
              WHERE lead_id=90000191 AND status='leased' AND lease_until<now()
              RETURNING id
            """)
            assert cur.fetchone() is not None
            cur.execute("""
              UPDATE crm_telegram_outbox SET status='leased',attempts=attempts+1,
              lease_until=now()+interval '2 minutes',lease_token=gen_random_uuid()
              WHERE lead_id=90000191 AND status IN ('pending','retryable_error')
              RETURNING id
            """)
            assert cur.fetchone() is None, "uncertain delivery must not auto-retry"
            cur.execute("""
              UPDATE crm_telegram_outbox SET status='sent',sent_at=now(),telegram_message_id=191,
              lease_until=NULL,lease_token=NULL
              WHERE lead_id=90000191 AND status='leased' AND lease_token=%s
              RETURNING id
            """, (old_token,))
            assert cur.fetchone() is None, "crashed worker's stale token must not acknowledge"
            cur.execute("SELECT status,attempts,last_error_code FROM crm_telegram_outbox WHERE lead_id=90000191")
            assert cur.fetchone()==('review',1,'LEASE_EXPIRED_DELIVERY_UNCERTAIN')
        verifier.commit()
    print("PASS: committed lease survives killed backend; expired lease goes to review; no automatic resend or stale ack")
finally:
    with psycopg.connect(dsn) as cleanup:
        with cleanup.cursor() as cur:
            cur.execute("DELETE FROM crm_telegram_outbox WHERE lead_id=90000191")
        cleanup.commit()
