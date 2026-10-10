"""Run the proposal DDL against an ephemeral CI-only PostgreSQL 18 database."""
import os
import unittest
from pathlib import Path

SQL_PATH = Path(__file__).resolve().parents[1] / "docs/sql/jarvis-return-queue-staging-proposal.sql"


class JarvisPostgresIntegrationTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("JARVIS_CI_DATABASE_URL"), "CI PostgreSQL service not configured")
    def test_schema_and_guards(self):
        import psycopg
        url = os.environ["JARVIS_CI_DATABASE_URL"]
        ddl = SQL_PATH.read_text(encoding="utf-8").split("-- Example single-winner claim")[0]
        with psycopg.connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT current_database(), current_user")
                self.assertEqual(cur.fetchone(), ("jarvis_return_queue_test", "jarvis_sandbox"))
                cur.execute(ddl)
                cur.execute(ddl)  # idempotent
                cur.execute("""SELECT tablename FROM pg_tables WHERE schemaname = 'public'
                               AND tablename IN ('jarvis_return_actions', 'jarvis_return_action_events')""")
                self.assertEqual({r[0] for r in cur.fetchall()},
                                 {"jarvis_return_actions", "jarvis_return_action_events"})
                cur.execute("""SELECT indexname FROM pg_indexes WHERE schemaname = 'public'
                               AND indexname = 'jarvis_return_action_events_task_time_idx'""")
                self.assertIsNotNone(cur.fetchone())
                fingerprint = "a" * 64
                cur.execute("""INSERT INTO jarvis_return_actions
                               (task_id, action_fingerprint, destination_key, state)
                               VALUES (%s, %s, %s, 'OPEN')""",
                            ("ci-test-1", fingerprint, "ci-only",))
                with self.assertRaises(psycopg.errors.CheckViolation):
                    with conn.transaction():
                        cur.execute("""UPDATE jarvis_return_actions SET state='DONE'
                                       WHERE task_id='ci-test-1'""")
                cur.execute("SELECT state FROM jarvis_return_actions WHERE task_id='ci-test-1'")
                self.assertEqual(cur.fetchone()[0], "OPEN")



    @unittest.skipUnless(os.environ.get("JARVIS_CI_DATABASE_URL"), "CI PostgreSQL service not configured")
    def test_approval_and_single_winner_claim(self):
        import psycopg
        url = os.environ["JARVIS_CI_DATABASE_URL"]
        ddl = SQL_PATH.read_text(encoding="utf-8").split("-- Example single-winner claim")[0]
        with psycopg.connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute(ddl)
                fingerprint = "a" * 64
                cur.execute("INSERT INTO jarvis_return_actions (task_id, action_fingerprint, destination_key, state) VALUES (%s,%s,%s,'OPEN')", ("ci-claim", fingerprint, "ci-only"))
                with self.assertRaises(psycopg.errors.CheckViolation):
                    with conn.transaction():
                        cur.execute("UPDATE jarvis_return_actions SET state='APPROVED' WHERE task_id='ci-claim'")
                with self.assertRaises(psycopg.errors.ForeignKeyViolation):
                    with conn.transaction():
                        cur.execute("INSERT INTO jarvis_return_action_events (task_id,to_state,actor,correlation_id) VALUES ('nonexistent','OPEN','ci','ci-test')")
                cur.execute("UPDATE jarvis_return_actions SET state='APPROVED', approved_by='human-ci', approved_at=now(), approved_fingerprint=%s, approval_expires_at=now()+interval '10 minutes' WHERE task_id='ci-claim'", (fingerprint,))
                claim = """UPDATE jarvis_return_actions SET state='PROCESSING', lease_owner=%s, lease_until=now()+interval '60 seconds', attempt_count=attempt_count+1, version=version+1 WHERE task_id='ci-claim' AND state IN ('APPROVED','RETRY') AND approved_fingerprint=%s AND action_fingerprint=%s AND approval_expires_at>now() RETURNING task_id"""
                cur.execute(claim, ("worker-1", fingerprint, fingerprint))
                self.assertEqual(len(cur.fetchall()), 1)
                cur.execute(claim, ("worker-2", fingerprint, fingerprint))
                self.assertEqual(len(cur.fetchall()), 0)
                cur.execute("SELECT lease_owner,attempt_count,version FROM jarvis_return_actions WHERE task_id='ci-claim'")
                self.assertEqual(cur.fetchone(), ("worker-1", 1, 1))

if __name__ == "__main__":
    unittest.main()
