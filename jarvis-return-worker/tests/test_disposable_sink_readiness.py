"""Private synthetic sink readiness contract."""
import importlib.util
import pathlib
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("sink_receiver", ROOT / "isolated_receiver_http.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ReadinessTests(unittest.TestCase):
    def test_ready_without_token_but_receipts_stay_protected(self):
        with tempfile.TemporaryDirectory() as temp:
            database = str(pathlib.Path(temp) / "inbox.sqlite3")
            with sqlite3.connect(database) as db:
                db.execute("CREATE TABLE inbox (principal TEXT, idem_key TEXT, fingerprint TEXT, receipt TEXT, PRIMARY KEY (principal, idem_key))")
                db.execute("CREATE TABLE effects (id INTEGER PRIMARY KEY, principal TEXT, idem_key TEXT)")
            server = MODULE.serve_local(database, "synthetic-token-long-enough")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = "http://127.0.0.1:%d" % server.server_address[1]
                with urllib.request.urlopen(url + "/ready", timeout=3) as response:
                    self.assertEqual(response.status, 200)
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(url + "/receipts/test", timeout=3)
                self.assertEqual(error.exception.code, 401)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
