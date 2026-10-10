"""Run the proposal DDL against an ephemeral CI-only PostgreSQL 18 database."""
import os
import unittest
from pathlib import Path

import psycopg

SQL_PATH = Path(__file__).resolve().parents[1] / "docs/sql/jarvis-return-queue-staging-proposal.sql"


class JarvisPostgresIntegrationTests(unittest.TestCase):
    def test_schema_and_guards(self):
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


if __name__ == "__main__":
    unittest.main()
