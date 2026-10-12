"""Adversarial regression tests for sandbox-only queue."""
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from jarvis_return_queue_offline_handler import FakeTransport, initialize, enqueue, prepare, approve, process


class Adversarial(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.execute("PRAGMA foreign_keys=ON")
        initialize(self.db)
        self.now = datetime(2026, 10, 10, tzinfo=timezone.utc)
        self.fp = enqueue(self.db, "sandbox-test", "sandbox:never-deliver", "draft")
        self.assertTrue(prepare(self.db, "sandbox-test"))

    def tearDown(self):
        self.db.close()

    def authorize(self):
        self.assertTrue(approve(self.db, "sandbox-test", "human", self.fp,
                                self.now + timedelta(minutes=5), self.now))

    def test_payload_tampering_blocks_delivery(self):
        self.authorize()
        self.db.execute("UPDATE queue SET payload='modified' WHERE task_id='sandbox-test'")
        fake = FakeTransport()
        self.assertFalse(process(self.db, "sandbox-test", "worker", fake, self.now))
        self.assertEqual(fake.calls, 0)

    def test_destination_tampering_blocks_delivery(self):
        self.authorize()
        self.db.execute("UPDATE queue SET destination='sandbox:other' WHERE task_id='sandbox-test'")
        fake = FakeTransport()
        self.assertFalse(process(self.db, "sandbox-test", "worker", fake, self.now))
        self.assertEqual(fake.calls, 0)

    def test_expired_approval_blocks_claim(self):
        self.authorize()
        fake = FakeTransport()
        self.assertFalse(process(self.db, "sandbox-test", "worker", fake,
                                 self.now + timedelta(minutes=6)))
        self.assertEqual(fake.calls, 0)

    def test_wrong_fingerprint_blocks_claim(self):
        self.authorize()
        self.db.execute("UPDATE queue SET approved_fingerprint='wrong' WHERE task_id='sandbox-test'")
        fake = FakeTransport()
        self.assertFalse(process(self.db, "sandbox-test", "worker", fake, self.now))
        self.assertEqual(fake.calls, 0)

    def test_transport_failure_never_auto_retries(self):
        self.authorize()
        class BrokenTransport(FakeTransport):
            def deliver(self, key, payload):
                self.calls += 1
                raise RuntimeError("simulated fake delivery failure")
        fake = BrokenTransport()
        with self.assertRaises(RuntimeError):
            process(self.db, "sandbox-test", "worker", fake, self.now)
        self.assertEqual(self.db.execute("SELECT state FROM queue WHERE task_id='sandbox-test'").fetchone()[0], "PROCESSING")
        self.assertFalse(process(self.db, "sandbox-test", "other", fake, self.now))
        self.assertEqual(fake.calls, 1)


if __name__ == "__main__":
    unittest.main()
