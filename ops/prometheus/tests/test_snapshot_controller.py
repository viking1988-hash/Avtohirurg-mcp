"""Negative tests for localhost-only snapshot controller."""
import importlib.util
import os
from pathlib import Path
import threading
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import unittest
from unittest.mock import patch

PATH = Path(__file__).resolve().parents[1] / "snapshot_controller.py"
spec = importlib.util.spec_from_file_location("snapshot_controller", PATH)
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)

class ControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), controller.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = "http://127.0.0.1:%d" % cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def request_status(self, path, method="POST", headers=None):
        req = Request(self.url + path, method=method, headers=headers or {})
        try:
            with urlopen(req, timeout=3) as response:
                return response.status
        except HTTPError as exc:
            return exc.code

    def test_get_rejected(self):
        self.assertEqual(self.request_status("/v1/snapshot", "GET"), 405)

    def test_other_admin_route_rejected(self):
        self.assertEqual(self.request_status("/api/v1/admin/tsdb/delete_series"), 404)

    def test_query_string_rejected(self):
        self.assertEqual(self.request_status("/v1/snapshot?skip_head=true"), 404)

    def test_no_token_config_rejected(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(self.request_status("/v1/snapshot"), 503)

    def test_wrong_token_rejected(self):
        with patch.dict(os.environ, {"PROMETHEUS_SNAPSHOT_CONTROLLER_TOKEN": "a"*32}):
            self.assertEqual(self.request_status("/v1/snapshot", headers={"Authorization": "Bearer wrong"}), 401)

    def test_concurrent_request_rejected(self):
        with patch.dict(os.environ, {"PROMETHEUS_SNAPSHOT_CONTROLLER_TOKEN": "a"*32}):
            controller._snapshot_lock.acquire()
            try:
                self.assertEqual(self.request_status("/v1/snapshot", headers={"Authorization": "Bearer " + "a"*32}), 409)
            finally:
                controller._snapshot_lock.release()

    def test_unauthorized_request_never_reaches_upstream(self):
        with patch.dict(os.environ, {"PROMETHEUS_SNAPSHOT_CONTROLLER_TOKEN": "a"*32}):
            self.assertEqual(self.request_status("/v1/snapshot", headers={"Authorization": "Bearer invalid"}), 401)

    def test_authorized_request_with_body_rejected_before_upstream(self):
        with patch.dict(os.environ, {"PROMETHEUS_SNAPSHOT_CONTROLLER_TOKEN": "a"*32}):
            headers = {"Authorization": "Bearer " + "a"*32, "Content-Length": "1"}
            self.assertEqual(self.request_status("/v1/snapshot", headers=headers), 400)

if __name__ == "__main__":
    unittest.main()
