"""Authorized snapshot controller forwarding test without a real admin server."""
import importlib.util
import json
import os
from pathlib import Path
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen
from unittest.mock import patch
import unittest

PATH = Path(__file__).resolve().parents[1] / "snapshot_controller.py"
spec = importlib.util.spec_from_file_location("snapshot_controller_forward", PATH)
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)

class FakeAdmin(BaseHTTPRequestHandler):
    calls = []
    def do_POST(self):
        self.__class__.calls.append(self.path)
        payload = json.dumps({"status": "success", "data": {"name": "synthetic-snapshot"}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
    def log_message(self, *args):
        pass

class ForwardTests(unittest.TestCase):
    def test_authorized_exact_route_forwarded_once(self):
        FakeAdmin.calls = []
        upstream = ThreadingHTTPServer(("127.0.0.1", 0), FakeAdmin)
        downstream = ThreadingHTTPServer(("127.0.0.1", 0), controller.Handler)
        threads = [
            threading.Thread(target=upstream.serve_forever, daemon=True),
            threading.Thread(target=downstream.serve_forever, daemon=True),
        ]
        for thread in threads:
            thread.start()
        try:
            original = controller.http.client.HTTPConnection
            def connect(host, port, timeout=30):
                self.assertEqual(host, "127.0.0.1")
                self.assertEqual(port, 19091)
                return original(host, upstream.server_address[1], timeout=timeout)
            with patch.dict(os.environ, {"PROMETHEUS_SNAPSHOT_CONTROLLER_TOKEN": "a"*32}):
                with patch.object(controller.http.client, "HTTPConnection", side_effect=connect):
                    request = Request(
                        "http://127.0.0.1:%d/v1/snapshot" % downstream.server_address[1],
                        method="POST",
                        headers={"Authorization": "Bearer " + "a"*32},
                    )
                    with urlopen(request, timeout=3) as response:
                        self.assertEqual(response.status, 200)
                        self.assertEqual(json.load(response)["status"], "success")
            self.assertEqual(FakeAdmin.calls, ["/api/v1/admin/tsdb/snapshot"])
        finally:
            for server in (downstream, upstream):
                server.shutdown()
                server.server_close()
            for thread in threads:
                thread.join(timeout=3)

if __name__ == "__main__":
    unittest.main()
