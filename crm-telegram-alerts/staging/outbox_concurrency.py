#!/usr/bin/env python3
"""Disposable CI-only PostgreSQL concurrency check. Never use customer data."""
import threading
import psycopg

dsn = "host=localhost port=5432 dbname=outbox_ci user=outbox_test password=disposable_ci_password"
with psycopg.connect(dsn) as db:
    with db.cursor() as cur:
        cur.execute("INSERT INTO crm_telegram_outbox(lead_id,alert_type) VALUES (90000101,'new'),(90000102,'new')")
    db.commit()

barrier = threading.Barrier(2)
results = []
errors = []
lock = threading.Lock()

def worker():
    try:
        with psycopg.connect(dsn) as db:
            barrier.wait(timeout=10)
            with db.transaction():
                with db.cursor() as cur:
                    cur.execute("""
                      WITH candidate AS (
                        SELECT id FROM crm_telegram_outbox
                        WHERE status='pending' AND attempts<5
                        ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED
                      )
                      UPDATE crm_telegram_outbox o
                      SET status='leased',attempts=attempts+1,
                          lease_until=now()+interval '2 minutes',
                          lease_token=gen_random_uuid()
                      FROM candidate WHERE o.id=candidate.id
                      RETURNING o.id
                    """)
                    row = cur.fetchone()
                    with lock:
                        results.append(row[0] if row else None)
                    # Keep row locked long enough to overlap the other worker.
                    cur.execute("SELECT pg_sleep(0.4)")
    except Exception as exc:
        with lock:
            errors.append(str(exc))

threads = [threading.Thread(target=worker) for _ in range(2)]
for thread in threads:
    thread.start()
for thread in threads:
    thread.join(timeout=15)
assert all(not t.is_alive() for t in threads), "worker deadlock"
assert not errors, errors
assert len(results)==2 and None not in results and len(set(results))==2, results

with psycopg.connect(dsn) as db:
    with db.cursor() as cur:
        cur.execute("SELECT count(*) FROM crm_telegram_outbox WHERE status='leased' AND attempts=1")
        assert cur.fetchone()[0]==2
        cur.execute("DELETE FROM crm_telegram_outbox WHERE lead_id IN (90000101,90000102)")
    db.commit()
print("PASS: two simultaneous workers claimed different rows, no duplicate lease")

# One available job, two contenders: exactly one claim must succeed.
with psycopg.connect(dsn) as db:
    with db.cursor() as cur:
        cur.execute("INSERT INTO crm_telegram_outbox(lead_id,alert_type) VALUES (90000103,'new')")
    db.commit()
barrier = threading.Barrier(2)
results.clear()
errors.clear()
threads = [threading.Thread(target=worker) for _ in range(2)]
for thread in threads:
    thread.start()
for thread in threads:
    thread.join(timeout=15)
assert all(not t.is_alive() for t in threads), "worker deadlock on single job"
assert not errors, errors
assert len(results)==2 and sum(x is not None for x in results)==1, results
with psycopg.connect(dsn) as db:
    with db.cursor() as cur:
        cur.execute("SELECT count(*) FROM crm_telegram_outbox WHERE lead_id=90000103 AND status='leased' AND attempts=1")
        assert cur.fetchone()[0]==1
        cur.execute("DELETE FROM crm_telegram_outbox WHERE lead_id=90000103")
    db.commit()
print("PASS: single job claimed by only one of two simultaneous workers")

# A dropped connection before commit must roll back its claim, allowing recovery.
with psycopg.connect(dsn) as setup:
    with setup.cursor() as cur:
        cur.execute("INSERT INTO crm_telegram_outbox(lead_id,alert_type) VALUES (90000104,'new')")
    setup.commit()
crashed = psycopg.connect(dsn)
crashed.autocommit = False
with crashed.cursor() as cur:
    cur.execute("""
      WITH candidate AS (
        SELECT id FROM crm_telegram_outbox
        WHERE lead_id=90000104 AND status='pending'
        FOR UPDATE SKIP LOCKED
      )
      UPDATE crm_telegram_outbox o SET status='leased',attempts=attempts+1,
        lease_until=now()+interval '2 minutes',lease_token=gen_random_uuid()
      FROM candidate WHERE o.id=candidate.id RETURNING o.id
    """)
    claimed = cur.fetchone()
    assert claimed is not None
# Simulate abrupt transport failure: close backend without COMMIT.
with psycopg.connect(dsn, autocommit=True) as admin:
    with admin.cursor() as cur:
        cur.execute("SELECT pg_terminate_backend(%s)", (crashed.info.backend_pid,))
        assert cur.fetchone()[0] is True
try:
    crashed.close()
except psycopg.Error:
    pass
with psycopg.connect(dsn) as recovered:
    with recovered.cursor() as cur:
        cur.execute("SELECT status,attempts,lease_token FROM crm_telegram_outbox WHERE lead_id=90000104")
        status, attempts, token = cur.fetchone()
        assert (status,attempts,token)==('pending',0,None), (status,attempts,token)
        cur.execute("""
          UPDATE crm_telegram_outbox SET status='leased',attempts=attempts+1,
            lease_until=now()+interval '2 minutes',lease_token=gen_random_uuid()
          WHERE lead_id=90000104 AND status='pending'
          RETURNING id
        """)
        assert cur.fetchone() is not None
        cur.execute("DELETE FROM crm_telegram_outbox WHERE lead_id=90000104")
    recovered.commit()
print("PASS: terminated DB connection rolled back uncommitted lease and job recovered")

# Uncommitted lease must remain invisible to a second transaction.
with psycopg.connect(dsn) as setup:
    with setup.cursor() as cur:
        cur.execute("INSERT INTO crm_telegram_outbox(lead_id,alert_type) VALUES (90000105,'new')")
    setup.commit()
owner = psycopg.connect(dsn)
owner.autocommit = False
with owner.cursor() as cur:
    cur.execute("""
      WITH candidate AS (
        SELECT id FROM crm_telegram_outbox
        WHERE lead_id=90000105 AND status='pending'
        FOR UPDATE SKIP LOCKED
      )
      UPDATE crm_telegram_outbox o SET status='leased',attempts=attempts+1,
        lease_until=now()+interval '2 minutes',lease_token=gen_random_uuid()
      FROM candidate WHERE o.id=candidate.id RETURNING o.id
    """)
    assert cur.fetchone() is not None
with psycopg.connect(dsn) as rival:
    with rival.cursor() as cur:
        cur.execute("""
          SELECT id FROM crm_telegram_outbox
          WHERE lead_id=90000105 AND status='pending'
          FOR UPDATE SKIP LOCKED
        """)
        assert cur.fetchone() is None, "locked job was visible to rival"
owner.rollback()
owner.close()
with psycopg.connect(dsn) as cleanup:
    with cleanup.cursor() as cur:
        cur.execute("SELECT status,attempts FROM crm_telegram_outbox WHERE lead_id=90000105")
        assert cur.fetchone()==('pending',0)
        cur.execute("DELETE FROM crm_telegram_outbox WHERE lead_id=90000105")
    cleanup.commit()
print("PASS: uncommitted lease invisible to rival, rollback restores job")
