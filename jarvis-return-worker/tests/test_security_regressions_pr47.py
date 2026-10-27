"""Regression checks for fail-closed webhook dispatch and legacy approvals."""
import unittest
from unittest.mock import Mock, patch
from datetime import datetime, timezone
from email.message import Message

from jarvis_return_worker.http_client import HTTPClient
from jarvis_return_worker.transport import DeliveryError


class HTTPClientSafetyTests(unittest.TestCase):
    def test_lowercase_response_id_is_preserved(self):
        response = Mock()
        response.status = 200
        headers = Message()
        headers["x-response-id"] = "provider-123"
        response.headers = headers
        response.read.return_value = b'{"accepted":true}'
        client = HTTPClient(opener=lambda request, timeout: response)
        result = client.request("POST", "https://example.test/webhook")
        self.assertEqual(result["response_id"], "provider-123")

    def test_reserved_headers_rejected_case_insensitively(self):
        client = HTTPClient(opener=lambda *args, **kwargs: self.fail("network attempted"))
        for header in ("idempotency-key", "X-REQUEST-ID", "Content-Length", "HOST"):
            with self.subTest(header=header):
                with self.assertRaises(DeliveryError):
                    client.request("POST", "https://example.test/webhook", headers={header: "override"})

    def test_http_date_retry_after_is_bounded(self):
        client = HTTPClient(random_fn=lambda: 0.5)
        self.assertEqual(client._delay(1, "Wed, 01 Jan 2099 00:00:00 GMT"), 10)
        self.assertEqual(client._delay(1, "Wed, 01 Jan 2000 00:00:00 GMT"), 0)
        self.assertGreater(client._delay(1, "invalid"), 0)


class DispatchFenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_stale_webhook_claim_does_not_send(self):
        from jarvis_return_worker.orchestrator import Orchestrator
        from jarvis_return_worker.queue import Conflict
        repo = Mock()
        repo.verify_webhook_dispatch_lease.side_effect = Conflict("stale lease")
        repo.fail_webhook_operation.side_effect = Conflict("stale lease")
        dispatcher = Mock()
        orchestrator = object.__new__(Orchestrator)
        orchestrator.repository = repo
        orchestrator.dispatcher = dispatcher
        orchestrator.policy = Mock()
        orchestrator.policy.webhook_operation.return_value.action = "dispatch"
        operation = {"event_id": "e1", "attempt_count": 1, "version": 1,
                     "method": "POST", "path": "/test", "idempotency_key": "k1"}
        await orchestrator.process_webhook_operation(operation, "owner")
        dispatcher.dispatch.assert_not_called()
        repo.verify_webhook_dispatch_lease.assert_called_once()


class LegacyApprovalTests(unittest.TestCase):
    def test_legacy_event_requires_fresh_human_approval(self):
        from jarvis_return_worker.app import _create_dispatch_intent
        conn = Mock()
        old = {"task_id": "legacy-task", "state": "APPROVED",
               "correlation_id": "legacy-corr"}
        new = {**old, "state": "READY", "version": 2}
        conn.execute.return_value.fetchone.side_effect = [old, new]
        event = {"event_id": "legacy-event", "aggregate_id": "legacy-task",
                 "event_type": "TaskApproved",
                 "payload": {"new_state": "APPROVED"}}
        with patch("jarvis_return_worker.audit.record") as audit:
            _create_dispatch_intent(conn, event)
        sql = [call.args[0] for call in conn.execute.call_args_list]
        self.assertTrue(any("approved_fingerprint=NULL" in statement for statement in sql))
        self.assertFalse(any("INSERT INTO jarvis_provider_dispatch_intents" in statement
                             for statement in sql))
        audit.assert_called_once()
        self.assertEqual(audit.call_args.args[3], "requeue_requires_fresh_approval")

    def test_legacy_event_does_not_change_nonapproved_task(self):
        from jarvis_return_worker.app import _create_dispatch_intent
        conn = Mock()
        conn.execute.return_value.fetchone.return_value = {"state": "DONE"}
        event = {"event_id": "old", "aggregate_id": "done-task",
                 "event_type": "TaskApproved",
                 "payload": {"new_state": "APPROVED"}}
        _create_dispatch_intent(conn, event)
        self.assertEqual(conn.execute.call_count, 1)


if __name__ == "__main__":
    unittest.main()
