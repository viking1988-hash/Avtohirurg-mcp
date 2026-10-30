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

    def test_lowercase_retry_after_http_date_on_429(self):
        from urllib.error import HTTPError
        from io import BytesIO
        headers = Message()
        headers["retry-after"] = "Wed, 01 Jan 2099 00:00:00 GMT"
        def opener(request, timeout):
            raise HTTPError(request.full_url, 429, "rate limited", headers,
                            BytesIO(b'{"error":"synthetic"}'))
        client = HTTPClient(opener=opener, max_retries=0)
        with self.assertRaises(DeliveryError) as raised:
            client.request("POST", "https://example.test/webhook")
        self.assertEqual(raised.exception.retry_after,
                         "Wed, 01 Jan 2099 00:00:00 GMT")

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


    async def test_insufficient_remaining_webhook_lease_never_dispatches(self):
        from jarvis_return_worker.orchestrator import Orchestrator
        from jarvis_return_worker.queue import Conflict
        repo = Mock()
        repo.verify_webhook_dispatch_lease.side_effect = Conflict("insufficient Webhook lease remaining for HTTP attempt")
        repo.fail_webhook_operation.side_effect = Conflict("fenced")
        dispatcher = Mock()
        orchestrator = object.__new__(Orchestrator)
        orchestrator.repository = repo
        orchestrator.dispatcher = dispatcher
        orchestrator.policy = Mock()
        orchestrator.policy.webhook_operation.return_value.action = "dispatch"
        operation = {"event_id": "e2", "attempt_count": 2, "version": 3,
                     "method": "POST", "path": "/test", "idempotency_key": "k2"}
        with patch.dict("os.environ", {"JARVIS_WEBHOOK_BASE_URL":"https://example.test",
                                       "JARVIS_WEBHOOK_TIMEOUT_SECONDS":"8",
                                       "JARVIS_WEBHOOK_MAX_RETRIES":"2"}):
            await orchestrator.process_webhook_operation(operation, "owner")
        dispatcher.dispatch.assert_not_called()
        repo.verify_webhook_dispatch_lease.assert_called_once_with(
            "e2", "owner", 2, 3, 49)




    async def test_http_budget_checked_before_provider_lookup(self):
        from jarvis_return_worker.orchestrator import Orchestrator
        from jarvis_return_worker.queue import Conflict
        repo = Mock()
        repo.verify_webhook_dispatch_lease.side_effect = Conflict("insufficient Webhook lease remaining")
        repo.fail_webhook_operation.side_effect = Conflict("fenced")
        dispatcher = Mock()
        orchestrator = object.__new__(Orchestrator)
        orchestrator.repository = repo
        orchestrator.dispatcher = dispatcher
        orchestrator.policy = Mock()
        orchestrator.policy.webhook_operation.return_value.action = "dispatch"
        operation = {"event_id":"late", "attempt_count":1, "version":2,
                     "method":"POST", "path":"/test", "idempotency_key":"late-key"}
        with patch.dict("os.environ", {"JARVIS_WEBHOOK_BASE_URL":"https://example.test",
                                       "JARVIS_WEBHOOK_TIMEOUT_SECONDS":"8",
                                       "JARVIS_WEBHOOK_MAX_RETRIES":"2"}):
            await orchestrator.process_webhook_operation(operation,"worker")
        dispatcher.dispatch.assert_not_called()
        self.assertEqual(repo.verify_webhook_dispatch_lease.call_args.args[-1],49)



    async def test_fenced_send_verifies_before_provider_lookup(self):
        from jarvis_return_worker.orchestrator import Orchestrator
        calls = []
        repo = Mock()
        repo.verify_webhook_dispatch_lease.side_effect = lambda *args: calls.append("verify")
        provider = Mock()
        provider.send.side_effect = lambda *args: (calls.append("send") or {"response_id":"test-id"})
        dispatcher = Mock()
        dispatcher.dispatch.side_effect = lambda name: (calls.append("lookup") or provider)
        orchestrator = object.__new__(Orchestrator)
        orchestrator.repository = repo
        orchestrator.dispatcher = dispatcher
        orchestrator.policy = Mock()
        orchestrator.policy.webhook_operation.return_value.action = "dispatch"
        operation = {"event_id":"ordered", "attempt_count":1, "version":2,
                     "method":"POST", "path":"/test", "idempotency_key":"key"}
        with patch.dict("os.environ", {"JARVIS_WEBHOOK_BASE_URL":""}):
            await orchestrator.process_webhook_operation(operation,"worker")
        self.assertEqual(calls,["verify","lookup","send"])
        repo.finish_webhook_operation.assert_called_once()



    async def test_fenced_send_never_looks_up_provider_after_lease_conflict(self):
        from jarvis_return_worker.orchestrator import Orchestrator
        from jarvis_return_worker.queue import Conflict
        calls = []
        repo = Mock()
        def reject(*args):
            calls.append("verify")
            raise Conflict("stale Webhook lease")
        repo.verify_webhook_dispatch_lease.side_effect = reject
        repo.fail_webhook_operation.side_effect = Conflict("stale Webhook lease")
        dispatcher = Mock()
        orchestrator = object.__new__(Orchestrator)
        orchestrator.repository = repo
        orchestrator.dispatcher = dispatcher
        orchestrator.policy = Mock()
        orchestrator.policy.webhook_operation.return_value.action = "dispatch"
        operation = {"event_id":"fenced", "attempt_count":1, "version":2,
                     "method":"POST", "path":"/test", "idempotency_key":"key"}
        with patch.dict("os.environ", {"JARVIS_WEBHOOK_BASE_URL":""}):
            await orchestrator.process_webhook_operation(operation,"worker")
        self.assertEqual(calls,["verify"])
        dispatcher.dispatch.assert_not_called()
        repo.finish_webhook_operation.assert_not_called()



    async def test_webhook_provider_lookup_failure_never_marks_success(self):
        from jarvis_return_worker.orchestrator import Orchestrator
        repo = Mock()
        dispatcher = Mock()
        dispatcher.dispatch.side_effect = RuntimeError("provider unavailable")
        orchestrator = object.__new__(Orchestrator)
        orchestrator.repository = repo
        orchestrator.dispatcher = dispatcher
        orchestrator.policy = Mock()
        orchestrator.policy.webhook_operation.return_value.action = "dispatch"
        operation = {"event_id":"lookup-error", "attempt_count":1, "version":2,
                     "method":"POST", "path":"/test", "idempotency_key":"key"}
        with patch.dict("os.environ", {"JARVIS_WEBHOOK_BASE_URL":""}):
            await orchestrator.process_webhook_operation(operation,"worker")
        repo.verify_webhook_dispatch_lease.assert_called_once()
        repo.finish_webhook_operation.assert_not_called()
        repo.fail_webhook_operation.assert_called_once()
        self.assertEqual(repo.fail_webhook_operation.call_args.args[5],
                         "webhook_outcome_unknown")



    async def test_webhook_send_failure_never_marks_success(self):
        from jarvis_return_worker.orchestrator import Orchestrator
        from jarvis_return_worker.transport import Classification, DeliveryError
        repo = Mock()
        provider = Mock()
        provider.send.side_effect = DeliveryError(Classification.UNKNOWN,"webhook_network_outcome_unknown")
        dispatcher = Mock()
        dispatcher.dispatch.return_value = provider
        orchestrator = object.__new__(Orchestrator)
        orchestrator.repository = repo
        orchestrator.dispatcher = dispatcher
        orchestrator.policy = Mock()
        orchestrator.policy.webhook_operation.return_value.action = "dispatch"
        operation = {"event_id":"send-error", "attempt_count":1, "version":2,
                     "method":"POST", "path":"/test", "idempotency_key":"key"}
        with patch.dict("os.environ", {"JARVIS_WEBHOOK_BASE_URL":""}):
            await orchestrator.process_webhook_operation(operation,"worker")
        repo.verify_webhook_dispatch_lease.assert_called_once()
        provider.send.assert_called_once()
        repo.finish_webhook_operation.assert_not_called()
        repo.fail_webhook_operation.assert_called_once()
        self.assertEqual(repo.fail_webhook_operation.call_args.args[5],
                         "webhook_network_outcome_unknown")



    async def test_webhook_send_failure_with_lost_lease_does_not_overwrite_state(self):
        from jarvis_return_worker.orchestrator import Orchestrator
        from jarvis_return_worker.queue import Conflict
        from jarvis_return_worker.transport import Classification, DeliveryError
        repo = Mock()
        repo.fail_webhook_operation.side_effect = Conflict("stale Webhook lease")
        provider = Mock()
        provider.send.side_effect = DeliveryError(Classification.UNKNOWN,"webhook_network_outcome_unknown")
        dispatcher = Mock()
        dispatcher.dispatch.return_value = provider
        orchestrator = object.__new__(Orchestrator)
        orchestrator.repository = repo
        orchestrator.dispatcher = dispatcher
        orchestrator.policy = Mock()
        orchestrator.policy.webhook_operation.return_value.action = "dispatch"
        operation = {"event_id":"lost-during-send", "attempt_count":1, "version":2,
                     "method":"POST", "path":"/test", "idempotency_key":"key"}
        with patch.dict("os.environ", {"JARVIS_WEBHOOK_BASE_URL":""}):
            await orchestrator.process_webhook_operation(operation,"worker")
        repo.verify_webhook_dispatch_lease.assert_called_once()
        provider.send.assert_called_once()
        repo.finish_webhook_operation.assert_not_called()
        repo.fail_webhook_operation.assert_called_once()



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


class WebhookReceiptConcurrencyTests(unittest.TestCase):
    def test_duplicate_response_id_uses_transaction_scoped_lock(self):
        from jarvis_return_worker.queue import QueueRepository
        import inspect
        source = inspect.getsource(QueueRepository.finish_webhook_operation)
        lock_pos = source.index("pg_advisory_xact_lock")
        duplicate_pos = source.index("SELECT event_id FROM jarvis_webhook_operations")
        done_pos = source.index("state='DONE'")
        self.assertLess(lock_pos, duplicate_pos)
        self.assertLess(duplicate_pos, done_pos)
        self.assertIn("hashtextextended(%s, 0)", source)


class WebhookLeaseBudgetTests(unittest.TestCase):
    def test_short_lease_rejected_for_webhook_retry_budget(self):
        from jarvis_return_worker.config import Config
        from unittest.mock import patch
        config = Config(database_url="postgresql://jarvis_sandbox:password@jarvis-sandbox-postgres.railway.internal/jarvis_return_queue_test",
                        read_token="r"*40, approval_token="a"*40, operator_id="ci-human",
                        worker_id="ci", lease_seconds=30, heartbeat_seconds=5)
        with patch.dict("os.environ", {"JARVIS_WEBHOOK_BASE_URL":"https://example.test",
                                        "JARVIS_WEBHOOK_TIMEOUT_SECONDS":"8",
                                        "JARVIS_WEBHOOK_MAX_RETRIES":"2"}):
            with self.assertRaisesRegex(ValueError, "Webhook delivery budget exceeds lease"):
                config.validate()

    def test_adequate_lease_accepted_for_webhook_retry_budget(self):
        from jarvis_return_worker.config import Config
        from unittest.mock import patch
        config = Config(database_url="postgresql://jarvis_sandbox:password@jarvis-sandbox-postgres.railway.internal/jarvis_return_queue_test",
                        read_token="r"*40, approval_token="a"*40, operator_id="ci-human",
                        worker_id="ci", lease_seconds=90, heartbeat_seconds=5)
        with patch.dict("os.environ", {"JARVIS_WEBHOOK_BASE_URL":"https://example.test",
                                        "JARVIS_WEBHOOK_TIMEOUT_SECONDS":"8",
                                        "JARVIS_WEBHOOK_MAX_RETRIES":"2"}):
            config.validate()


class WebhookRemainingLeaseTests(unittest.TestCase):
    def test_rejects_claim_with_insufficient_remaining_budget(self):
        from jarvis_return_worker.queue import QueueRepository, Conflict
        from datetime import datetime, timedelta, timezone
        from contextlib import contextmanager
        repo = QueueRepository(Mock(lease_seconds=90))
        conn = Mock()
        row = {"lease_until":datetime.now(timezone.utc)+timedelta(seconds=2)}
        conn.execute.return_value.fetchone.return_value = {"seconds":2}
        @contextmanager
        def fake_connection():
            yield conn
        repo.connection = fake_connection
        repo._webhook_owned = Mock(return_value=row)
        with self.assertRaisesRegex(Conflict,"insufficient Webhook lease remaining"):
            repo.verify_webhook_dispatch_lease("event","owner",1,1,49)
        repo._webhook_owned.assert_called_once()

    def test_rejects_budget_larger_than_configured_lease(self):
        from jarvis_return_worker.queue import QueueRepository, Conflict
        repo = QueueRepository(Mock(lease_seconds=30))
        with self.assertRaisesRegex(Conflict,"invalid Webhook remaining lease budget"):
            repo.verify_webhook_dispatch_lease("event","owner",1,1,50)


if __name__ == "__main__":
    unittest.main()


class HTTPResponseHeaderCaseTests(unittest.TestCase):
    def test_lowercase_response_id_is_preserved(self):
        from email.message import Message
        from unittest.mock import Mock
        from jarvis_return_worker.http_client import HTTPClient
        headers = Message()
        headers["x-response-id"] = "synthetic-response-id"
        response = Mock(status=200, headers=headers)
        response.read.return_value = b'{"receipt_id":"synthetic-receipt"}'
        client = HTTPClient(opener=lambda request, timeout: response)
        result = client.request("POST", "https://example.test/synthetic",
                                body={"synthetic": True},
                                idempotency_key="synthetic-key")
        self.assertEqual(result["response_id"], "synthetic-response-id")


class ReturnWorkerDispatchFenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_stale_claim_never_invokes_provider(self):
        from jarvis_return_worker.orchestrator import Orchestrator
        from jarvis_return_worker.queue import Conflict
        repository = Mock()
        repository.verify_dispatch_lease.side_effect = Conflict("stale lease")
        repository.fail.side_effect = Conflict("stale lease")
        dispatcher = Mock()
        provider = Mock()
        dispatcher.dispatch.return_value = provider
        orchestrator = Orchestrator(repository, dispatcher)
        orchestrator.policy = Mock()
        orchestrator.policy.approval.return_value.action = "send"
        orchestrator.policy.delivery_failure.return_value.action = "reconcile"
        task = {"task_id": "synthetic-stale", "attempt_count": 1,
                "destination_key": "sandbox:never-send",
                "action_fingerprint": "test-only"}
        await orchestrator.process(task, "expired-owner")
        repository.verify_dispatch_lease.assert_called_once_with(
            "synthetic-stale", "expired-owner", 1)
        provider.send.assert_not_called()
        repository.save_delivery_receipt.assert_not_called()
        repository.finish.assert_not_called()

    async def test_lease_lost_after_pre_send_check_never_claims_exactly_once(self):
        """A stale sender can reach the provider after a successful pre-send check.

        This documents the non-atomic external-effect boundary; receiver-side
        idempotency or manual reconciliation is required.
        """
        from jarvis_return_worker.orchestrator import Orchestrator
        from jarvis_return_worker.queue import Conflict
        repository = Mock()
        calls = []
        repository.verify_dispatch_lease.side_effect = lambda *args: calls.append("fence_ok")
        repository.save_delivery_receipt.side_effect = Conflict("lease expired after send")
        repository.fail.side_effect = Conflict("stale lease")
        dispatcher = Mock()
        provider = Mock()
        provider.provider = "fake"
        provider.send.side_effect = lambda *args: (
            calls.append("external_effect") or {"receipt_id": "synthetic-receipt"})
        dispatcher.dispatch.return_value = provider
        orchestrator = Orchestrator(repository, dispatcher)
        orchestrator.policy = Mock()
        orchestrator.policy.approval.return_value.action = "send"
        orchestrator.policy.delivery_failure.return_value.action = "reconcile"
        task = {"task_id": "synthetic-post-fence-race", "attempt_count": 1,
                "destination_key": "sandbox:never-send",
                "action_fingerprint": "test-only"}
        await orchestrator.process(task, "stale-owner")
        self.assertEqual(calls, ["fence_ok", "external_effect"])
        repository.finish.assert_not_called()
        repository.fail.assert_called_once()

    async def test_two_stale_workers_can_reach_provider_without_receiver_dedup(self):
        """Demonstrate why DB fencing alone cannot promise exactly-once effects."""
        import asyncio
        from jarvis_return_worker.orchestrator import Orchestrator
        from jarvis_return_worker.queue import Conflict
        from threading import Barrier
        barrier = Barrier(2, timeout=5)
        sends = []
        repository = Mock()
        repository.verify_dispatch_lease.side_effect = lambda *args: barrier.wait()
        repository.save_delivery_receipt.side_effect = Conflict("stale lease")
        repository.fail.side_effect = Conflict("stale lease")
        provider = Mock()
        provider.provider = "fake"
        provider.send.side_effect = lambda task, key: (
            sends.append(key) or {"receipt_id": "synthetic-receipt"})
        dispatcher = Mock()
        dispatcher.dispatch.return_value = provider
        orchestrator = Orchestrator(repository, dispatcher)
        orchestrator.policy = Mock()
        orchestrator.policy.approval.return_value.action = "send"
        orchestrator.policy.delivery_failure.return_value.action = "reconcile"
        task = {"task_id": "synthetic-race", "attempt_count": 1,
                "destination_key": "sandbox:never-send",
                "action_fingerprint": "same-action"}
        await asyncio.gather(
            orchestrator.process(dict(task), "worker-a"),
            orchestrator.process(dict(task), "worker-b"))
        self.assertEqual(sends, ["synthetic-race:same-action"] * 2)
        repository.finish.assert_not_called()

    async def test_live_claim_checks_fence_before_provider(self):
        from jarvis_return_worker.orchestrator import Orchestrator
        from jarvis_return_worker.transport import Classification, DeliveryError
        calls = []
        repository = Mock()
        repository.verify_dispatch_lease.side_effect = lambda *args: calls.append("verify")
        dispatcher = Mock()
        provider = Mock()
        provider.provider = "fake"
        provider.send.side_effect = lambda *args: (calls.append("send") or
            {"receipt_id": "synthetic-receipt"})
        dispatcher.dispatch.return_value = provider
        orchestrator = Orchestrator(repository, dispatcher)
        orchestrator.policy = Mock()
        orchestrator.policy.approval.return_value.action = "send"
        task = {"task_id": "synthetic-live", "attempt_count": 1,
                "destination_key": "sandbox:never-send",
                "action_fingerprint": "test-only"}
        await orchestrator.process(task, "live-owner")
        self.assertEqual(calls, ["verify", "send"])
        repository.finish.assert_called_once()

class AuditProviderMappingTests(unittest.TestCase):
    def test_http_approval_audit_uses_http_provider(self):
        from jarvis_return_worker.audit import append_audit_outbox

        after = {
            "task_id": "synthetic-http-audit",
            "state": "APPROVED",
            "version": 4,
            "destination_key": "sandbox:http:https://receiver.invalid/synthetic",
            "correlation_id": "synthetic-correlation",
            "approved_at": datetime(2026, 10, 30, tzinfo=timezone.utc),
            "approved_fingerprint": "sha256:synthetic",
        }
        with patch("jarvis_return_worker.outbox.append_event",
                   return_value={"event_id": "synthetic-event"}) as append_event:
            result = append_audit_outbox(
                object(), "synthetic-audit-event", None, after, "approve"
            )

        self.assertEqual(result, {"event_id": "synthetic-event"})
        self.assertEqual(append_event.call_args.kwargs["payload"]["provider"], "http")
