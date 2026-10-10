import unittest
from datetime import datetime, timedelta, timezone
from jarvis_return_queue_safety import action_fingerprint, validate_transition

class ReturnQueueSafetyTests(unittest.TestCase):
    def test_no_shortcut_to_done(self):
        self.assertFalse(validate_transition("OPEN", "DONE", delivery_receipt="r"))
        self.assertFalse(validate_transition("READY", "DONE", delivery_receipt="r"))

    def test_approval_required(self):
        self.assertFalse(validate_transition("READY", "APPROVED"))
        self.assertFalse(validate_transition("APPROVED", "PROCESSING"))

    def test_fingerprint_stable_and_payload_bound(self):
        a = action_fingerprint("1", "customer", {"a": 1, "b": 2})
        self.assertEqual(a, action_fingerprint("1", "customer", {"b": 2, "a": 1}))
        self.assertNotEqual(a, action_fingerprint("1", "customer", {"a": 2, "b": 2}))

    def test_valid_approval_and_expiration(self):
        now = datetime(2026, 10, 10, tzinfo=timezone.utc)
        kwargs = dict(approved_by="employee", approval_fingerprint="f", current_fingerprint="f", approval_expires_at=now+timedelta(minutes=5), now=now)
        self.assertTrue(validate_transition("READY", "APPROVED", **kwargs))
        self.assertTrue(validate_transition("APPROVED", "PROCESSING", **kwargs))
        self.assertFalse(validate_transition("READY", "APPROVED", **{**kwargs, "current_fingerprint": "changed"}))
        self.assertFalse(validate_transition("READY", "APPROVED", **{**kwargs, "now": now+timedelta(minutes=6)}))

    def test_done_needs_receipt(self):
        self.assertFalse(validate_transition("PROCESSING", "DONE"))
        self.assertTrue(validate_transition("PROCESSING", "DONE", delivery_receipt="provider:123"))

    def test_terminal_states(self):
        self.assertFalse(validate_transition("DONE", "OPEN"))
        self.assertFalse(validate_transition("CANCELLED", "PROCESSING"))

if __name__ == "__main__":
    unittest.main()
