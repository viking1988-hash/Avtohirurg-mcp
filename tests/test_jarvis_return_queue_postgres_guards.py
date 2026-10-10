"""Additional PostgreSQL sandbox guards; only ephemeral CI PostgreSQL.

No real delivery, Railway access, or customer messaging.
"""
import os
import uuid
import unittest
from datetime import datetime, timedelta, timezone
from jarvis_return_queue_postgres_sandbox import guard, enqueue, prepare, approve, claim, finish_fake


@unittest.skipUnless(os.environ.get("JARVIS_CI_DATABASE_URL"), "ephemeral PostgreSQL CI only")
class PostgresSandboxGuardTests(unittest.TestCase):
    def setUp(self):
        import psycopg
        self.conn = psycopg.connect(os.environ["JARVIS_CI_DATABASE_URL"], autocommit=True)
        guard(self.conn)
        self.task_id = "ci-guard-" + uuid.uuid4().hex
        self.fp = enqueue(self.conn, self.task_id, "sandbox:never-deliver", "payload")
        self.assertTrue(prepare(self.conn, self.task_id))
        self.now = datetime.now(timezone.utc)

    def tearDown(self):
        with self.conn.transaction():
            self.conn.execute("DELETE FROM jarvis_return_action_events WHERE task_id=%s", (self.task_id,))
            self.conn.execute("DELETE FROM jarvis_return_actions WHERE task_id=%s", (self.task_id,))
        self.conn.close()

    def test_non_autocommit_rejected(self):
        import psycopg
        with psycopg.connect(os.environ["JARVIS_CI_DATABASE_URL"], autocommit=False) as conn:
            with self.assertRaises(RuntimeError):
                guard(conn)

    def test_expired_approval_rejected_by_database_clock(self):
        past = self.now - timedelta(minutes=1)
        self.assertFalse(approve(self.conn, self.task_id, "human", self.fp, past,
                                 past - timedelta(minutes=2)))
        self.assertFalse(claim(self.conn, self.task_id, "worker", self.fp))

    def test_wrong_task_receipt_rejected(self):
        self.assertTrue(approve(self.conn, self.task_id, "human", self.fp,
                                self.now + timedelta(minutes=5), self.now))
        self.assertTrue(claim(self.conn, self.task_id, "worker", self.fp))
        self.assertFalse(finish_fake(self.conn, self.task_id, "worker",
                                     "sandbox:receipt:another-task"))
        self.assertFalse(finish_fake(self.conn, self.task_id, "wrong-worker",
                                     "sandbox:receipt:" + self.task_id))
        self.assertTrue(finish_fake(self.conn, self.task_id, "worker",
                                    "sandbox:receipt:" + self.task_id))

    def test_changed_destination_blocks_claim(self):
        self.assertTrue(approve(self.conn, self.task_id, "human", self.fp,
                                self.now + timedelta(minutes=5), self.now))
        self.conn.execute("UPDATE jarvis_return_actions SET destination_key='telegram:real' WHERE task_id=%s",
                          (self.task_id,))
        self.assertFalse(claim(self.conn, self.task_id, "worker", self.fp))


if __name__ == "__main__":
    unittest.main()
