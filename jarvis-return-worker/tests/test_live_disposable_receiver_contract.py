"""Run disposable receiver contract against an actual loopback HTTP server in CI.

No external network, Railway deployment, or provider messages.
"""
import importlib.util
import os
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


class LiveDisposableContractTests(unittest.TestCase):
    def test_receipt_and_deduplication_survive_receiver_restart(self):
        # Restart the HTTP server against the same durable SQLite file.
        import json
        import urllib.request
        import urllib.error
        import uuid
        with tempfile.TemporaryDirectory() as temp:
            dbpath = str(pathlib.Path(temp) / "receiver.sqlite3")
            with sqlite3.connect(dbpath) as db:
                db.execute("CREATE TABLE inbox (principal TEXT NOT NULL, idem_key TEXT NOT NULL, fingerprint TEXT NOT NULL, receipt TEXT NOT NULL, PRIMARY KEY (principal, idem_key))")
                db.execute("CREATE TABLE effects (id INTEGER PRIMARY KEY, principal TEXT NOT NULL, idem_key TEXT NOT NULL)")
            receiver = load("isolated_receiver_http.py", "receiver_restart_contract")
            token = "disposable-restart-test-token-123456"
            key = "restart-" + uuid.uuid4().hex
            body = json.dumps({"synthetic": True}).encode("utf-8")
            def request(port, method, path, payload=None):
                req = urllib.request.Request(
                    "http://127.0.0.1:%d%s" % (port, path),
                    data=payload,
                    headers={"Authorization": "Bearer " + token,
                             "Idempotency-Key": key,
                             "Content-Type": "application/json"},
                    method=method)
                with urllib.request.urlopen(req, timeout=3) as response:
                    return response.status, json.load(response)
            server = receiver.serve_local(dbpath, token)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                status, receipt = request(server.server_port, "POST", "/messages", body)
                self.assertEqual(status, 201)
            finally:
                server.shutdown()
                thread.join(timeout=5)
                server.server_close()
            server = receiver.serve_local(dbpath, token)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                self.assertEqual(request(server.server_port, "POST", "/messages", body), (200, receipt))
                self.assertEqual(request(server.server_port, "GET", "/receipts/" + key), (200, receipt))
                with sqlite3.connect(dbpath) as db:
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM effects").fetchone()[0], 1)
            finally:
                server.shutdown()
                thread.join(timeout=5)
                server.server_close()

    def test_parallel_http_retries_with_same_key_have_one_effect(self):
        # Simulate two workers sending after overlapping leases.
        import json
        import uuid
        import urllib.request
        from concurrent.futures import ThreadPoolExecutor
        with tempfile.TemporaryDirectory() as temp:
            dbpath = str(pathlib.Path(temp) / "receiver.sqlite3")
            with sqlite3.connect(dbpath) as db:
                db.execute("CREATE TABLE inbox (principal TEXT NOT NULL, idem_key TEXT NOT NULL, fingerprint TEXT NOT NULL, receipt TEXT NOT NULL, PRIMARY KEY (principal, idem_key))")
                db.execute("CREATE TABLE effects (id INTEGER PRIMARY KEY, principal TEXT NOT NULL, idem_key TEXT NOT NULL)")
            receiver = load("isolated_receiver_http.py", "receiver_parallel_contract")
            token = "disposable-parallel-test-token-123456"
            key = "parallel-" + uuid.uuid4().hex
            server = receiver.serve_local(dbpath, token)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            gate = threading.Barrier(2)
            def send(_):
                gate.wait(timeout=5)
                req = urllib.request.Request(
                    "http://127.0.0.1:%d/messages" % server.server_port,
                    data=json.dumps({"synthetic": True}).encode("utf-8"),
                    headers={"Authorization": "Bearer " + token,
                             "Idempotency-Key": key, "Content-Type": "application/json"},
                    method="POST")
                with urllib.request.urlopen(req, timeout=5) as response:
                    return response.status, json.load(response)
            try:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(pool.map(send, range(2)))
                self.assertEqual(sorted(status for status, _ in results), [200, 201])
                self.assertEqual(results[0][1], results[1][1])
                with sqlite3.connect(dbpath) as db:
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM effects").fetchone()[0], 1)
            finally:
                server.shutdown()
                thread.join(timeout=5)
                server.server_close()

    def test_contract_runs_against_loopback_server(self):
        with tempfile.TemporaryDirectory() as temp:
            dbpath = str(pathlib.Path(temp) / "receiver.sqlite3")
            with sqlite3.connect(dbpath) as db:
                db.execute("CREATE TABLE inbox (principal TEXT NOT NULL, idem_key TEXT NOT NULL, fingerprint TEXT NOT NULL, receipt TEXT NOT NULL, PRIMARY KEY (principal, idem_key))")
                db.execute("CREATE TABLE effects (id INTEGER PRIMARY KEY, principal TEXT NOT NULL, idem_key TEXT NOT NULL)")
            receiver = load("isolated_receiver_http.py", "disposable_receiver_live")
            server = receiver.serve_local(dbpath, "disposable-test-token-123456")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            old_url = os.environ.get("JARVIS_RECEIVER_E2E_URL")
            old_token = os.environ.get("JARVIS_RECEIVER_E2E_TOKEN")
            try:
                os.environ["JARVIS_RECEIVER_E2E_URL"] = "http://127.0.0.1:%d" % server.server_port
                os.environ["JARVIS_RECEIVER_E2E_TOKEN"] = "disposable-test-token-123456"
                contract = load("tests/test_disposable_receiver_contract.py", "live_receiver_contract")
                suite = unittest.defaultTestLoader.loadTestsFromTestCase(contract.DisposableReceiverContract)
                result = unittest.TestResult()
                suite.run(result)
                self.assertEqual(result.testsRun, 2)
                self.assertFalse(result.skipped, "contract must run, not skip")
                self.assertFalse(result.errors, result.errors)
                self.assertFalse(result.failures, result.failures)
                with sqlite3.connect(dbpath) as db:
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM effects").fetchone()[0], 1)
            finally:
                if old_url is None:
                    os.environ.pop("JARVIS_RECEIVER_E2E_URL", None)
                else:
                    os.environ["JARVIS_RECEIVER_E2E_URL"] = old_url
                if old_token is None:
                    os.environ.pop("JARVIS_RECEIVER_E2E_TOKEN", None)
                else:
                    os.environ["JARVIS_RECEIVER_E2E_TOKEN"] = old_token
                server.shutdown()
                thread.join(timeout=5)
                server.server_close()


if __name__ == "__main__":
    unittest.main()
