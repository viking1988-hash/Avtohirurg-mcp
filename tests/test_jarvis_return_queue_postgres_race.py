"""Independent-connection single-winner claim acceptance test.

Run only against ephemeral PostgreSQL CI database, never Railway or production.
"""
import os
import uuid
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from jarvis_return_queue_postgres_sandbox import guard, enqueue, prepare, approve, claim


@unittest.skipUnless(os.getenv("JARVIS_CI_DATABASE_URL"), "CI sandbox DB unavailable")
class ConcurrentClaim(unittest.TestCase):
    def test_two_connections_only_one_winner(self):
        import psycopg
        url = os.environ["JARVIS_CI_DATABASE_URL"]
        task_id = "ci-race-" + uuid.uuid4().hex
        with psycopg.connect(url, autocommit=True) as conn:
            guard(conn)
            now = datetime.now(timezone.utc)
            fingerprint = enqueue(conn, task_id, "sandbox:never-deliver", "race")
            self.assertTrue(prepare(conn, task_id))
            self.assertTrue(approve(conn, task_id, "ci-human", fingerprint,
                                    now + timedelta(minutes=5), now))
        try:
            def contender(worker):
                with psycopg.connect(url, autocommit=True) as conn:
                    guard(conn)
                    return claim(conn, task_id, worker, fingerprint)
            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(contender, ("worker-1", "worker-2")))
            self.assertEqual(outcomes.count(True), 1)
            self.assertEqual(outcomes.count(False), 1)
            with psycopg.connect(url, autocommit=True) as conn:
                guard(conn)
                state, attempts = conn.execute(
                    "SELECT state,attempt_count FROM jarvis_return_actions WHERE task_id=%s",
                    (task_id,)).fetchone()
                self.assertEqual((state, attempts), ("PROCESSING", 1))
                self.assertEqual(conn.execute(
                    "SELECT count(*) FROM jarvis_return_action_events WHERE task_id=%s AND to_state='PROCESSING'",
                    (task_id,)).fetchone()[0], 1)
        finally:
            with psycopg.connect(url, autocommit=True) as conn:
                guard(conn)
                with conn.transaction():
                    conn.execute("DELETE FROM jarvis_return_action_events WHERE task_id=%s", (task_id,))
                    conn.execute("DELETE FROM jarvis_return_actions WHERE task_id=%s", (task_id,))


if __name__ == "__main__":
    unittest.main()
