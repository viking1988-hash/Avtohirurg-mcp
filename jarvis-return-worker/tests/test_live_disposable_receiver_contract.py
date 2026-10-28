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
                self.assertEqual(result.testsRun, 1)
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
