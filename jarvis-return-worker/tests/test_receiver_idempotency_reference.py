"""Isolated receiver idempotency reference. Not wired to Railway or production.

Run: python -m unittest discover -s jarvis-return-worker/tests -p 'test_receiver_idempotency_reference.py'
"""
import json
import os
import sqlite3
import tempfile
import threading
import unittest


class ReceiverInbox:
    """Atomic SQLite receiver simulation; one DB transaction per operation."""

    def __init__(self, path):
        self.path = path

    def submit(self, principal, method, route, key, body):
        if not all(isinstance(x, str) and x for x in (principal, method, route, key)):
            raise ValueError("authenticated principal, method, route and key required")
        fingerprint = json.dumps(
            [principal, method.upper(), route, body],
            sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        )
        with sqlite3.connect(self.path, timeout=10, isolation_level=None) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                existing = db.execute(
                    "SELECT fingerprint, receipt FROM inbox WHERE principal=? AND idem_key=?",
                    (principal, key),
                ).fetchone()
                if existing:
                    if existing[0] != fingerprint:
                        db.execute("COMMIT")
                        return 409, {"error": "idempotency_conflict"}
                    db.execute("COMMIT")
                    return 200, json.loads(existing[1])
                receipt = {"receipt_id": "receipt-" + str(db.execute(
                    "SELECT COALESCE(MAX(id), 0)+1 FROM effects"
                ).fetchone()[0]), "status": "COMMITTED"}
                db.execute("INSERT INTO effects (principal, idem_key) VALUES (?, ?)", (principal, key))
                db.execute(
                    "INSERT INTO inbox (principal, idem_key, fingerprint, receipt) VALUES (?, ?, ?, ?)",
                    (principal, key, fingerprint, json.dumps(receipt)),
                )
                db.execute("COMMIT")
                return 201, receipt
            except BaseException:
                db.execute("ROLLBACK")
                raise

    def receipt(self, principal, key):
        with sqlite3.connect(self.path) as db:
            row = db.execute(
                "SELECT receipt FROM inbox WHERE principal=? AND idem_key=?", (principal, key)
            ).fetchone()
            return json.loads(row[0]) if row else None

    def effect_count(self):
        with sqlite3.connect(self.path) as db:
            return db.execute("SELECT COUNT(*) FROM effects").fetchone()[0]


class ReceiverInboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = os.path.join(self.temp.name, "inbox.sqlite3")
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE inbox (principal TEXT NOT NULL, idem_key TEXT NOT NULL, fingerprint TEXT NOT NULL, receipt TEXT NOT NULL, PRIMARY KEY (principal, idem_key))")
            db.execute("CREATE TABLE effects (id INTEGER PRIMARY KEY, principal TEXT NOT NULL, idem_key TEXT NOT NULL)")
        self.inbox = ReceiverInbox(self.path)

    def send(self, body=None, key="event-1", principal="sender-1", method="POST", route="/messages"):
        return self.inbox.submit(principal, method, route, key, {"value": 1} if body is None else body)

    def test_concurrent_duplicates_commit_one_effect(self):
        barrier = threading.Barrier(8)
        def worker(_):
            barrier.wait(timeout=10)
            return self.send()
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(worker, range(8)))
        self.assertEqual(sorted(code for code, _ in results), [200] * 7 + [201])
        self.assertEqual(len({item["receipt_id"] for _, item in results}), 1)
        self.assertEqual(self.inbox.effect_count(), 1)

    def test_conflicting_payload_method_and_route_rejected(self):
        self.assertEqual(self.send()[0], 201)
        for args in ({"body": {"value": 2}}, {"method": "PATCH"}, {"route": "/other"}):
            self.assertEqual(self.send(**args)[0], 409)
        self.assertEqual(self.inbox.effect_count(), 1)

    def test_response_loss_and_restart_reconcile_without_mutation(self):
        status, receipt = self.send()
        self.assertEqual(status, 201)
        restarted = ReceiverInbox(self.path)
        self.assertEqual(restarted.receipt("sender-1", "event-1"), receipt)
        self.assertEqual(restarted.submit("sender-1", "POST", "/messages", "event-1", {"value": 1})[0], 200)
        self.assertEqual(restarted.effect_count(), 1)

    def test_principal_scoping(self):
        self.assertEqual(self.send()[0], 201)
        self.assertIsNone(self.inbox.receipt("sender-2", "event-1"))
        self.assertEqual(self.send(principal="sender-2")[0], 201)
        self.assertEqual(self.inbox.effect_count(), 2)

    def test_two_workers_with_overlapping_leases_commit_once(self):
        # Both workers may reach the receiver; only the receiver's transaction fences effects.
        gate = threading.Barrier(2)
        def worker(_):
            gate.wait(timeout=10)
            return ReceiverInbox(self.path).submit(
                "sender-1", "POST", "/messages", "same-operation", {"value": 1}
            )
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(worker, range(2)))
        self.assertEqual(sorted(code for code, _ in results), [200, 201])
        self.assertEqual(self.inbox.effect_count(), 1)

    def test_failed_transaction_rolls_back_key_and_effect(self):
        with sqlite3.connect(self.path) as db:
            db.execute("""
                CREATE TRIGGER reject_receipt BEFORE INSERT ON inbox
                BEGIN SELECT RAISE(ABORT, 'synthetic receipt write failure'); END
            """)
        with self.assertRaises(sqlite3.IntegrityError):
            self.send()
        self.assertEqual(self.inbox.effect_count(), 0)
        self.assertIsNone(self.inbox.receipt("sender-1", "event-1"))
        with sqlite3.connect(self.path) as db:
            db.execute("DROP TRIGGER reject_receipt")
        self.assertEqual(self.send()[0], 201)
        self.assertEqual(self.inbox.effect_count(), 1)

    def test_concurrent_conflicting_payload_never_overwrites_first(self):
        gate = threading.Barrier(2)
        def worker(value):
            gate.wait(timeout=10)
            return ReceiverInbox(self.path).submit(
                "sender-1", "POST", "/messages", "conflict-key", {"value": value}
            )
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(worker, (1, 2)))
        self.assertEqual(sorted(code for code, _ in results), [201, 409])
        self.assertEqual(self.inbox.effect_count(), 1)
        self.assertIsNotNone(self.inbox.receipt("sender-1", "conflict-key"))

    def test_real_http_concurrent_duplicate_and_conflict(self):
        """Exercise the threaded HTTP receiver, not just ReceiverInbox.submit."""
        import http.client
        from concurrent.futures import ThreadPoolExecutor
        from http.server import ThreadingHTTPServer
        import importlib.util
        from pathlib import Path

        receiver_path = Path(__file__).resolve().parents[1] / "isolated_receiver_http.py"
        spec = importlib.util.spec_from_file_location("pr47_http_receiver_test", receiver_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        token = "synthetic-private-test-token-only"
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0), module.make_handler(self.path, token))
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        def request(body, key="pr47-durable-concurrent"):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
            try:
                connection.request("POST", "/messages",
                                   body=json.dumps(body).encode("utf-8"),
                                   headers={"Authorization": "Bearer " + token,
                                            "Idempotency-Key": key,
                                            "Content-Type": "application/json"})
                response = connection.getresponse()
                return response.status, json.loads(response.read())
            finally:
                connection.close()

        gate = threading.Barrier(8, timeout=10)
        def duplicate(_):
            gate.wait()
            return request({"synthetic": 1})
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(duplicate, range(8)))
        self.assertEqual(sorted(code for code, _ in results), [200] * 7 + [201])
        self.assertEqual(len({value["receipt_id"] for _, value in results}), 1)
        self.assertEqual(self.inbox.effect_count(), 1)
        self.assertEqual(request({"synthetic": 2})[0], 409)
        self.assertEqual(request({"synthetic": 1})[0], 200)
        self.assertEqual(self.inbox.effect_count(), 1)

    def test_missing_identity_or_key_rejected(self):
        for principal, key in (("", "event-1"), ("sender-1", "")):
            with self.assertRaises(ValueError):
                self.send(principal=principal, key=key)
        self.assertEqual(self.inbox.effect_count(), 0)


if __name__ == "__main__":
    unittest.main()
