"""End-to-end synthetic subprocess test of the actual disposable sink entrypoint.

Uses only 127.0.0.1 and a temporary SQLite database. Never calls Railway.
"""
import json
import os
import pathlib
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "disposable_sink.py"
TOKEN = "synthetic-process-contract-token-123456"


class SinkProcessContract(unittest.TestCase):
    def test_receipt_survives_real_process_restart(self):
        with tempfile.TemporaryDirectory() as root:
            dbpath = str(pathlib.Path(root) / "receiver.sqlite3")
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            base = "http://127.0.0.1:%d" % port
            env = dict(os.environ)
            env.update({
                "JARVIS_PR47_DISPOSABLE_ONLY": "YES",
                "JARVIS_RECEIVER_E2E_TOKEN": TOKEN,
                "JARVIS_RECEIVER_DB": dbpath,
                "PORT": str(port),
            })

            def request(method, path, payload=None):
                headers = {"Authorization": "Bearer " + TOKEN,
                           "Idempotency-Key": "synthetic-process-restart-1",
                           "Content-Type": "application/json"}
                data = None if payload is None else json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
                try:
                    with urllib.request.urlopen(req, timeout=1) as response:
                        return response.status, json.load(response)
                except urllib.error.HTTPError as error:
                    return error.code, json.load(error)

            def start():
                proc = subprocess.Popen([sys.executable, str(SCRIPT)], env=env,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                for _ in range(60):
                    if proc.poll() is not None:
                        self.fail("sink exited before ready: " + proc.stderr.read().decode()[-1000:])
                    try:
                        with urllib.request.urlopen(base + "/ready", timeout=0.2) as response:
                            if response.status == 200:
                                return proc
                    except (OSError, urllib.error.URLError):
                        time.sleep(0.05)
                proc.terminate()
                proc.wait(timeout=5)
                self.fail("sink did not become ready")

            def stop(proc):
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
                proc.stderr.close()

            proc = start()
            try:
                first = request("POST", "/messages", {"synthetic": True})
                self.assertEqual(first[0], 201)
            finally:
                stop(proc)
            proc = start()
            try:
                self.assertEqual(request("POST", "/messages", {"synthetic": True}), (200, first[1]))
                self.assertEqual(request("GET", "/receipts/synthetic-process-restart-1"), (200, first[1]))
                self.assertEqual(request("POST", "/messages", {"synthetic": False})[0], 409)
                with sqlite3.connect(dbpath) as db:
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM effects").fetchone()[0], 1)
            finally:
                stop(proc)


if __name__ == "__main__":
    unittest.main()
