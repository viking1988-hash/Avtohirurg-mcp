"""Offline, no-network handler acceptance checks for Jarvis Return Queue.

No Railway connection, CRM, n8n, provider API, or customer messages.
"""
import unittest
from datetime import datetime, timedelta, timezone
from jarvis_return_queue_safety import action_fingerprint, validate_transition


class OfflineHandlerSafetyTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 10, tzinfo=timezone.utc)
        self.fingerprint = action_fingerprint("sandbox-task", "sandbox:never-deliver", {"text": "test"})
        self.approval = dict(
            approved_by="sandbox-reviewer",
            approval_fingerprint=self.fingerprint,
            current_fingerprint=self.fingerprint,
            approval_expires_at=self.now + timedelta(minutes=5),
            now=self.now,
        )

    def test_open_cannot_skip_approval_or_finish(self):
        for state in ("APPROVED", "PROCESSING", "DONE"):
            self.assertFalse(validate_transition("OPEN", state, **self.approval))

    def test_ready_without_human_approval_fails_closed(self):
        self.assertFalse(validate_transition("READY", "APPROVED", now=self.now))
        self.assertTrue(validate_transition("READY", "APPROVED", **self.approval))

    def test_payload_or_destination_change_invalidates_approval(self):
        altered = action_fingerprint("sandbox-task", "sandbox:other", {"text": "test"})
        self.assertFalse(validate_transition(
            "APPROVED", "PROCESSING", **{**self.approval, "current_fingerprint": altered}))

    def test_expired_approval_cannot_process(self):
        self.assertFalse(validate_transition(
            "APPROVED", "PROCESSING",
            **{**self.approval, "approval_expires_at": self.now}))

    def test_done_requires_receipt_and_processing_state(self):
        self.assertFalse(validate_transition("PROCESSING", "DONE", now=self.now))
        self.assertTrue(validate_transition("PROCESSING", "DONE", delivery_receipt="sandbox:mock-receipt", now=self.now))
        self.assertFalse(validate_transition("READY", "DONE", delivery_receipt="sandbox:mock-receipt", now=self.now))

    def test_done_is_terminal(self):
        self.assertFalse(validate_transition("DONE", "PROCESSING", **self.approval))


if __name__ == "__main__":
    unittest.main()
