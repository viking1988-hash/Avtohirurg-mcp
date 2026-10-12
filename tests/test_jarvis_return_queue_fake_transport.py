"""Offline fake transport acceptance test. Never calls real customer channels."""
import unittest
from datetime import datetime, timedelta, timezone
from jarvis_return_queue_safety import action_fingerprint, validate_transition


class FakeTransport:
    def __init__(self):
        self.receipts = {}
        self.attempts = 0

    def send(self, idempotency_key, payload):
        self.attempts += 1
        if idempotency_key not in self.receipts:
            self.receipts[idempotency_key] = "sandbox:receipt:" + idempotency_key
        return self.receipts[idempotency_key]


class OfflineReturnQueueAcceptance(unittest.TestCase):
    def test_approved_task_single_claim_and_mock_receipt(self):
        now = datetime(2026, 10, 10, tzinfo=timezone.utc)
        fingerprint = action_fingerprint("sandbox-1", "sandbox:never-deliver", {"message": "test"})
        approval = dict(approved_by="sandbox-human", approval_fingerprint=fingerprint,
                        current_fingerprint=fingerprint,
                        approval_expires_at=now + timedelta(minutes=5), now=now)
        self.assertTrue(validate_transition("OPEN", "READY", now=now))
        self.assertFalse(validate_transition("READY", "PROCESSING", **approval))
        self.assertTrue(validate_transition("READY", "APPROVED", **approval))
        self.assertTrue(validate_transition("APPROVED", "PROCESSING", **approval))
        # Fake atomic state gate: second worker sees PROCESSING and cannot claim.
        self.assertFalse(validate_transition("PROCESSING", "PROCESSING", **approval))
        transport = FakeTransport()
        receipt = transport.send("sandbox-1:" + fingerprint, {"message": "test"})
        self.assertEqual(receipt, transport.send("sandbox-1:" + fingerprint, {"message": "test"}))
        self.assertEqual(len(transport.receipts), 1)
        self.assertTrue(validate_transition("PROCESSING", "DONE", delivery_receipt=receipt, now=now))
        self.assertFalse(validate_transition("DONE", "PROCESSING", **approval))

    def test_no_approval_no_transport_call(self):
        transport = FakeTransport()
        self.assertFalse(validate_transition("READY", "APPROVED"))
        self.assertEqual(transport.attempts, 0)

    def test_no_receipt_cannot_finish(self):
        self.assertFalse(validate_transition("PROCESSING", "DONE"))


if __name__ == "__main__":
    unittest.main()
