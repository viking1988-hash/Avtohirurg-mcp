"""PostgreSQL transaction-lock regression for webhook response-ID fencing.

Uses only the isolated GitHub Actions PostgreSQL database.
"""
import os
import threading
import unittest
from types import SimpleNamespace

from jarvis_return_worker.queue import QueueRepository


class DuplicateResponseIdDatabaseTests(unittest.TestCase):
    def test_second_connection_cannot_acquire_response_lock_before_commit(self):
        url = os.environ.get("JARVIS_WORKER_CI_DATABASE_URL")
        if not url:
            self.skipTest("isolated CI PostgreSQL URL not configured")
        repo = QueueRepository(SimpleNamespace(database_url=url, worker_id="response-lock-ci",
                                                lease_seconds=30, heartbeat_seconds=5,
                                                max_attempts=3))
        repo.migrate()
        key = "jarvis-webhook-response:ci-concurrent-response-id"
        first_acquired = threading.Event()
        allow_commit = threading.Event()
        results = []
        errors = []

        def first():
            try:
                with repo.connection() as conn:
                    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (key,))
                    first_acquired.set()
                    if not allow_commit.wait(timeout=8):
                        raise TimeoutError("second connection did not check lock")
            except Exception as exc:
                errors.append(exc)

        thread = threading.Thread(target=first)
        thread.start()
        try:
            self.assertTrue(first_acquired.wait(timeout=5), "first lock not acquired")
            with repo.connection() as conn:
                blocked = conn.execute(
                    "SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 0)) AS acquired",
                    (key,)).fetchone()
                results.append(bool(blocked["acquired"]))
            self.assertEqual(results, [False], "duplicate response lock was not exclusive")
        finally:
            allow_commit.set()
            thread.join(timeout=10)
        self.assertFalse(thread.is_alive(), "first transaction did not finish")
        self.assertFalse(errors, errors)
        with repo.connection() as conn:
            acquired = conn.execute(
                "SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 0)) AS acquired",
                (key,)).fetchone()
            self.assertTrue(acquired["acquired"], "lock was not released on commit")


if __name__ == "__main__":
    unittest.main()
