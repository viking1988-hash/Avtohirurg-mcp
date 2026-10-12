"""End-to-end acceptance tests for isolated handler (no external services)."""
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from jarvis_return_queue_offline_handler import FakeTransport, initialize, enqueue, prepare, approve, process


class HandlerE2E(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.execute("PRAGMA foreign_keys=ON")
        initialize(self.db)
        self.now = datetime(2026, 10, 10, tzinfo=timezone.utc)
        self.transport = FakeTransport()
        self.fp = enqueue(self.db, "task-1", "sandbox:never-deliver", "example")
        self.assertTrue(prepare(self.db, "task-1"))

    def tearDown(self):
        self.db.close()

    def test_no_approval_no_delivery(self):
        self.assertFalse(process(self.db, "task-1", "worker-1", self.transport, self.now))
        self.assertEqual(self.transport.calls, 0)

    def test_expired_or_changed_approval_rejected(self):
        self.assertFalse(approve(self.db, "task-1", "human", self.fp, self.now, self.now))
        self.assertFalse(approve(self.db, "task-1", "human", "bad", self.now + timedelta(minutes=5), self.now))
        self.assertEqual(self.transport.calls, 0)

    def test_single_delivery_receipt_and_audit(self):
        self.assertTrue(approve(self.db, "task-1", "human", self.fp, self.now + timedelta(minutes=5), self.now))
        self.assertTrue(process(self.db, "task-1", "worker-1", self.transport, self.now))
        self.assertFalse(process(self.db, "task-1", "worker-2", self.transport, self.now))
        self.assertEqual(self.transport.calls, 1)
        state,receipt = self.db.execute("SELECT state,receipt FROM queue WHERE task_id='task-1'").fetchone()
        self.assertEqual(state, "DONE")
        self.assertTrue(receipt.startswith("sandbox:receipt:"))
        states = [r[0] for r in self.db.execute("SELECT to_state FROM events WHERE task_id='task-1' ORDER BY event_id")]
        self.assertEqual(states, ["OPEN", "READY", "APPROVED", "PROCESSING", "DONE"])

    def test_no_real_destination(self):
        with self.assertRaises(ValueError):
            enqueue(self.db, "unsafe", "telegram:real", "test")

    def test_duplicate_task_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            enqueue(self.db, "task-1", "sandbox:never-deliver", "example")

    def test_unconfirmed_delivery_not_done(self):
        class BrokenTransport(FakeTransport):
            def deliver(self, key, payload):
                self.calls += 1
                return None
        transport = BrokenTransport()
        self.assertTrue(approve(self.db, "task-1", "human", self.fp, self.now + timedelta(minutes=5), self.now))
        self.assertFalse(process(self.db, "task-1", "worker", transport, self.now))
        self.assertEqual(self.db.execute("SELECT state FROM queue WHERE task_id='task-1'").fetchone()[0], "PROCESSING")
        self.assertFalse(process(self.db, "task-1", "worker2", transport, self.now))
        self.assertEqual(transport.calls, 1)


if __name__ == "__main__":
    unittest.main()
