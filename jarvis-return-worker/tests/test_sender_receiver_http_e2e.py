"""End-to-end sender HTTP client -> loopback receiver, with durable receipt lookup.

No Railway, external HTTP or real provider calls. Run via pytest in worker CI.
"""
import http.client
import importlib.util
import json
import pathlib
import sqlite3
import tempfile
import threading
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
def load(path, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SenderToReceiverE2ETests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = str(pathlib.Path(self.temp.name) / "receiver.sqlite3")
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TABLE inbox (principal TEXT NOT NULL, idem_key TEXT NOT NULL, fingerprint TEXT NOT NULL, receipt TEXT NOT NULL, PRIMARY KEY (principal, idem_key))")
            conn.execute("CREATE TABLE effects (id INTEGER PRIMARY KEY, principal TEXT NOT NULL, idem_key TEXT NOT NULL)")
        receiver = load("isolated_receiver_http.py", "receiver_e2e")
        self.server = receiver.serve_local(self.db, "test-receiver-token-123456")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.thread.join, 5)

    def test_real_sender_client_header_and_receipt_replay(self):
        # Exercise the actual sender HTTPClient, not a hand-built idempotency header.
        from jarvis_return_worker.http_client import HTTPClient
        # HTTPClient uses urllib; this receiver is bound to loopback only.
        client = HTTPClient(timeout_seconds=3, max_retries=0)
        url = "http://127.0.0.1:%d/messages" % self.server.server_port
        headers = {"Authorization": "Bearer test-receiver-token-123456"}
        first = client.request("POST", url, body={"synthetic": True}, headers=headers,
                               correlation_id="e2e-correlation", idempotency_key="e2e-operation-1")
        second = client.request("POST", url, body={"synthetic": True}, headers=headers,
                                correlation_id="e2e-correlation", idempotency_key="e2e-operation-1")
        self.assertEqual(first["http_status"], 201)
        self.assertEqual(second["http_status"], 200)
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM effects").fetchone()[0], 1)
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        connection.request("GET", "/receipts/e2e-operation-1", headers=headers)
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        receipt = json.loads(response.read())
        connection.close()
        self.assertEqual(receipt["status"], "COMMITTED")


if __name__ == "__main__":
    unittest.main()
