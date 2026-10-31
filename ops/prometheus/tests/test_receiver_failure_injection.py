"""Failure-injection HTTP tests for disposable backup receiver. No Railway writes."""
import hashlib
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backup_receiver as receiver
from test_backup_receiver import make_archive, TOKEN


class ReceiverFailureInjectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.backups = self.root / "backups"
        self.backups.mkdir()
        self.promtool = self.root / "promtool"
        self.promtool.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.promtool.chmod(0o755)
        handler = receiver.make_handler(self.backups, TOKEN, str(self.promtool), False)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.server.upload_busy = False
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.server_close)

    def upload(self, data, digest):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=10)
        try:
            connection.request("POST", receiver.UPLOAD_PATH, body=data, headers={
                "Authorization": "Bearer " + TOKEN,
                "Content-Length": str(len(data)),
                "X-Archive-SHA256": digest,
                "X-Expected-Blocks": "1",
                "X-Snapshot-ID": "failure-fixture",
            })
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def fixture(self):
        archive = self.root / "fixture.tar.gz"
        digest = make_archive(archive)
        return archive.read_bytes(), digest

    def test_metadata_write_failure_is_reported_and_archive_remains(self):
        data, digest = self.fixture()
        with patch.object(receiver, "_write_status", side_effect=OSError("synthetic metadata failure")):
            status, body = self.upload(data, digest)
        self.assertEqual(status, 201)
        self.assertEqual(body["status_warning"], "OSError")
        self.assertEqual(body["retention_deletions"], 0)
        self.assertFalse((self.backups / "last-verified.json").exists())
        archives = list(self.backups.glob("prometheus-*.tar.gz"))
        self.assertEqual(len(archives), 1)
        self.assertEqual(hashlib.sha256(archives[0].read_bytes()).hexdigest(), digest)

    def test_disk_write_failure_rejects_upload_without_publishing(self):
        data, digest = self.fixture()
        with patch.object(receiver.tempfile, "mkstemp", side_effect=OSError("synthetic disk full")):
            status, body = self.upload(data, digest)
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "backup rejected")
        self.assertEqual(list(self.backups.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
