"""End-to-end duplicate webhook completion under concurrent PostgreSQL transactions."""
import os
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from jarvis_return_worker.queue import QueueRepository


class WebhookConcurrentCompletionTests(unittest.TestCase):
    def test_same_provider_response_id_finishes_once_and_reconciles_duplicate(self):
        url = os.environ.get("JARVIS_WORKER_CI_DATABASE_URL")
        if not url:
            self.skipTest("isolated CI PostgreSQL URL not configured")
        repo = QueueRepository(SimpleNamespace(database_url=url, worker_id="webhook-concurrent-ci",
                                                lease_seconds=30, heartbeat_seconds=5,
                                                max_attempts=3))
        repo.migrate()
        suffix = uuid.uuid4().hex
        ids = ["ci-webhook-" + suffix + "-a", "ci-webhook-" + suffix + "-b"]
        owner = "ci-webhook-owner-" + suffix
        with repo.connection() as conn:
            for event_id in ids:
                conn.execute("""INSERT INTO jarvis_outbox_events
                    (event_id,aggregate_id,aggregate_type,event_type,event_version,payload,correlation_id,causation_id)
                    VALUES (%s,%s,'ci','WebhookRequested',1,'{}'::jsonb,%s,%s)""",
                    (event_id,event_id,event_id,event_id))
                conn.execute("""INSERT INTO jarvis_webhook_operations
                    (event_id,idempotency_key,method,path,state,lease_owner,lease_until,
                     attempt_count,version,request_id)
                    VALUES (%s,%s,'POST','/ci', 'PROCESSING',%s,
                            clock_timestamp()+interval '30 seconds',1,1,%s)""",
                    (event_id,event_id,owner,event_id + "-request"))
        response_id = "ci-provider-response-" + suffix
        barrier = threading.Barrier(2)

        def complete(event_id):
            barrier.wait(timeout=5)
            return repo.finish_webhook_operation(
                event_id,owner,1,1,
                {"request_id":event_id+"-request","response_id":response_id,
                 "http_status":200,"provider_status":"success",
                 "receipt":{"provider":"fake","message_id":response_id},
                 "duration_ms":1,"retry_count":0})

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(complete,event_id) for event_id in ids]
            rows = [future.result(timeout=12) for future in futures]
        self.assertEqual(sorted(row["state"] for row in rows),
                         ["DONE","RECONCILIATION_REQUIRED"])
        with repo.connection() as conn:
            stored = conn.execute("""SELECT event_id,state,response_id,receipt,
                reconciliation_reason FROM jarvis_webhook_operations
                WHERE event_id=ANY(%s) ORDER BY event_id""",(ids,)).fetchall()
        self.assertEqual(len(stored),2)
        done = [row for row in stored if row["state"] == "DONE"]
        pending = [row for row in stored if row["state"] == "RECONCILIATION_REQUIRED"]
        self.assertEqual(len(done),1)
        self.assertEqual(done[0]["response_id"],response_id)
        self.assertIsNotNone(done[0]["receipt"])
        self.assertEqual(len(pending),1)
        self.assertEqual(pending[0]["reconciliation_reason"],
                         "duplicate_provider_response_id")
        self.assertEqual(pending[0]["receipt"]["duplicate_response_id"],response_id)


if __name__ == "__main__":
    unittest.main()
