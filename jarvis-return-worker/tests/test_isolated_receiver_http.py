"""Loopback HTTP acceptance tests; no external HTTP requests."""
import http.client
import importlib.util
import json
import pathlib
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor


HERE = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("isolated_receiver_http", HERE / "isolated_receiver_http.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class IsolatedHTTPReceiverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(pathlib.Path(self.temp.name) / "inbox.db")
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE inbox (principal TEXT NOT NULL, idem_key TEXT NOT NULL, fingerprint TEXT NOT NULL, receipt TEXT NOT NULL, PRIMARY KEY (principal, idem_key))")
            db.execute("CREATE TABLE effects (id INTEGER PRIMARY KEY, principal TEXT NOT NULL, idem_key TEXT NOT NULL)")
        self.token = "isolated-test-token-123456789"
        self.server = module.serve_local(self.path, self.token)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.thread.join, 5)

    def request(self, method="POST", path="/messages", body=None, key="test-key", authorized=True):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        headers = {"Content-Type": "application/json"}
        if authorized:
            headers["Authorization"] = "Bearer " + self.token
        if key is not None:
            headers["Idempotency-Key"] = key
        connection.request(method, path, body=json.dumps(body if body is not None else {"n": 1}) if method == "POST" else None, headers=headers)
        response = connection.getresponse()
        result = response.status, json.loads(response.read())
        connection.close()
        return result

    def count(self):
        with sqlite3.connect(self.path) as db:
            return db.execute("SELECT COUNT(*) FROM effects").fetchone()[0]

    def test_authenticated_replay_conflict_and_receipt(self):
        first = self.request()
        self.assertEqual(first[0], 201)
        self.assertEqual(self.request()[0], 200)
        self.assertEqual(self.request(body={"n": 2})[0], 409)
        self.assertEqual(self.request("GET", "/receipts/test-key"), (200, first[1]))
        self.assertEqual(self.count(), 1)

    def test_unauthorized_and_missing_key_do_not_mutate(self):
        self.assertEqual(self.request(authorized=False)[0], 401)
        self.assertEqual(self.request(key=None)[0], 400)
        self.assertEqual(self.count(), 0)

    def test_concurrent_http_requests_commit_once(self):
        gate = threading.Barrier(6)
        def send(_):
            gate.wait(timeout=5)
            return self.request()[0]
        with ThreadPoolExecutor(max_workers=6) as pool:
            codes = list(pool.map(send, range(6)))
        self.assertEqual(sorted(codes), [200] * 5 + [201])
        self.assertEqual(self.count(), 1)

    def test_receipt_survives_server_restart(self):
        first = self.request()
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()
        self.server = module.serve_local(self.path, self.token)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.assertEqual(self.request("GET", "/receipts/test-key"), (200, first[1]))
        self.assertEqual(self.count(), 1)


if __name__ == "__main__":
    unittest.main()
