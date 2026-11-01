"""Offline upload protocol tests: actual HTTP transfer, attestation and failure."""
import importlib.util
import hashlib
import json
import tarfile
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("upload_verified_archive", BASE / "upload_verified_archive.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
BLOCK = "01M5NEYN71T8MY4RF27T1K2GN4"

class UploadProtocolTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.archive = Path(self.temp.name) / "prometheus.tar.gz"
        with tarfile.open(self.archive, "w:gz") as tar:
            for name, payload in {
                "snapshot/" + BLOCK + "/meta.json": json.dumps({"ulid": BLOCK, "version": 1}).encode(),
                "snapshot/" + BLOCK + "/index": b"index",
                "snapshot/" + BLOCK + "/chunks/000001": b"chunk",
            }.items():
                import io
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload))
        self.sha = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.received = []
        outer = self
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers["Content-Length"])
                data = self.rfile.read(length)
                outer.received.append((self.path, dict(self.headers), data))
                result = {
                    "status": "verified",
                    "sha256": outer.sha,
                    "blocks": 1,
                    "restore_test": "promtool-tsdb-analyze-pass",
                }
                if outer.reject:
                    result["sha256"] = "0"*64
                body = json.dumps(result).encode()
                self.send_response(201)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *args):
                pass
        self.reject = False
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1/upload"

    def test_sends_bytes_and_required_headers(self):
        result = module.upload(self.archive, self.url, "x"*32, "snapshot_1", 1)
        self.assertEqual(result["sha256"], self.sha)
        self.assertEqual(len(self.received), 1)
        path, headers, data = self.received[0]
        self.assertEqual(path, "/v1/upload")
        self.assertEqual(headers["Authorization"], "Bearer " + "x"*32)
        self.assertEqual(headers["X-Archive-SHA256"], self.sha)
        self.assertEqual(headers["X-Expected-Blocks"], "1")
        self.assertEqual(headers["X-Snapshot-ID"], "snapshot_1")
        self.assertEqual(data, self.archive.read_bytes())

    def test_rejects_mismatched_receiver_attestation(self):
        self.reject = True
        with self.assertRaisesRegex(ValueError, "did not confirm"):
            module.upload(self.archive, self.url, "x"*32, "snapshot_1", 1)
        self.assertTrue(self.archive.exists())
