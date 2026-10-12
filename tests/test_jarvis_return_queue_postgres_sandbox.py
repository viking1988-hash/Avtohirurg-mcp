"""Integration tests for the guarded PostgreSQL sandbox repository.

Run only with JARVIS_CI_DATABASE_URL pointing to ephemeral CI PostgreSQL 18.
Never connect to production or Railway through these tests.
"""
import os
import uuid
import unittest
from datetime import datetime, timedelta, timezone

from jarvis_return_queue_postgres_sandbox import enqueue, prepare, approve, claim, finish_fake, guard


@unittest.skipUnless(os.getenv("JARVIS_CI_DATABASE_URL"), "ephemeral CI PostgreSQL not configured")
class PostgresSandboxHandlerIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.psycopg = psycopg
        cls.url = os.environ["JARVIS_CI_DATABASE_URL"]
        with psycopg.connect(cls.url, autocommit=True) as conn:
            guard(conn)
            from pathlib import Path
            sql = (Path(__file__).resolve().parents[1] /
                   "docs/sql/jarvis-return-queue-staging-proposal.sql").read_text()
            conn.execute(sql.split("-- Example single-winner claim")[0])

    def setUp(self):
        self.conn = self.psycopg.connect(self.url, autocommit=True)
        guard(self.conn)
        self.task_id = "ci-sandbox-" + uuid.uuid4().hex
        self.now = datetime.now(timezone.utc)
        self.fp = enqueue(self.conn, self.task_id, "sandbox:never-deliver", "test")
        self.assertTrue(prepare(self.conn, self.task_id))

    def tearDown(self):
        with self.conn.transaction():
            self.conn.execute("DELETE FROM jarvis_return_action_events WHERE task_id=%s", (self.task_id,))
            self.conn.execute("DELETE FROM jarvis_return_actions WHERE task_id=%s", (self.task_id,))
        self.conn.close()

    def test_requires_approval(self):
        self.assertFalse(claim(self.conn, self.task_id, "worker", self.fp))

    def test_approval_claim_fake_receipt_and_events(self):
        self.assertTrue(approve(self.conn, self.task_id, "human", self.fp,
                                self.now + timedelta(minutes=5), self.now))
        self.assertTrue(claim(self.conn, self.task_id, "worker1", self.fp))
        self.assertFalse(claim(self.conn, self.task_id, "worker2", self.fp))
        self.assertFalse(finish_fake(self.conn, self.task_id, "worker1", "not-a-sandbox-receipt"))
        self.assertTrue(finish_fake(self.conn, self.task_id, "worker1",
                                    "sandbox:receipt:" + self.task_id))
        self.assertFalse(finish_fake(self.conn, self.task_id, "worker1",
                                     "sandbox:receipt:" + self.task_id))
        state, receipt = self.conn.execute(
            "SELECT state,delivery_receipt FROM jarvis_return_actions WHERE task_id=%s",
            (self.task_id,)).fetchone()
        self.assertEqual(state, "DONE")
        self.assertTrue(receipt.startswith("sandbox:receipt:"))
        events = [r[0] for r in self.conn.execute(
            "SELECT to_state FROM jarvis_return_action_events WHERE task_id=%s ORDER BY event_id",
            (self.task_id,))]
        self.assertEqual(events, ["OPEN", "READY", "APPROVED", "PROCESSING", "DONE"])

    def test_expired_approval_and_wrong_fingerprint(self):
        self.assertFalse(approve(self.conn, self.task_id, "human", "wrong",
                                 self.now + timedelta(minutes=5), self.now))
        self.assertTrue(approve(self.conn, self.task_id, "human", self.fp,
                                self.now + timedelta(minutes=5), self.now))
        self.assertFalse(claim(self.conn, self.task_id, "worker", "wrong"))
        self.assertFalse(claim(self.conn, self.task_id, "worker", self.fp, lease_seconds=0))

    def test_rejects_real_destination(self):
        with self.assertRaises(ValueError):
            enqueue(self.conn, "unsafe-" + uuid.uuid4().hex, "telegram:real", "test")


if __name__ == "__main__":
    unittest.main()
