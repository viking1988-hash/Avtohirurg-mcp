"""PostgreSQL concurrency regression for duplicate webhook response IDs.

Requires the isolated CI PostgreSQL database; never connects to production.
"""
import os
import threading
import unittest
from types import SimpleNamespace

from jarvis_return_worker.queue import QueueRepository


class DuplicateResponseIdDatabaseTests(unittest.TestCase):
    def test_two_transactions_serialize_duplicate_provider_response_id(self):
        url = os.environ.get("JARVIS_WORKER_CI_DATABASE_URL")
        if not url:
            self.skipTest("isolated CI PostgreSQL URL not configured")
        repo = QueueRepository(SimpleNamespace(database_url=url, worker_id="response-lock-ci",
                                                lease_seconds=30, heartbeat_seconds=5,
                                                max_attempts=3))
        repo.migrate()
        response_id = "ci-concurrent-response-id"
        barrier = threading.Barrier(2)
        results = []
        errors = []

        def competitor():
            try:
                with repo.connection() as conn:
                    barrier.wait(timeout=5)
                    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                                 ("jarvis-webhook-response:" + response_id,))
                    # Once the lock is held, the other transaction cannot enter
                    # the duplicate check until this transaction commits.
                    row = conn.execute("SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 0)) AS acquired",
                                       ("jarvis-webhook-response:" + response_id,)).fetchone()
                    results.append(bool(row["acquired"]))
            except Exception as exc:
                errors.append(exc)

        a = threading.Thread(target=competitor)
        b = threading.Thread(target=competitor)
        a.start()
        b.start()
        a.join(timeout=12)
        b.join(timeout=12)
        self.assertFalse(a.is_alive() or b.is_alive(), "concurrent lock attempt hung")
        self.assertFalse(errors, errors)
        self.assertEqual(results, [True, True])


if __name__ == "__main__":
    unittest.main()
