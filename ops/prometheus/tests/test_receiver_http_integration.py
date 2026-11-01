"""End-to-end local HTTP upload against the real receiver implementation.

The fake promtool is an explicit test double. This proves receiver archive
validation/publication and idempotency, NOT genuine TSDB restoration.
"""
import hashlib
import importlib.util
import io
import json
import sys
import tarfile
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
import backup_receiver
import upload_verified_archive

BLOCK = "01M5NEYN71T8MY4RF27T1K2GN4"

class ReceiverHTTPTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.backups = self.root / "backups"
        self.backups.mkdir()
        self.archive = self.root / "input.tar.gz"
        with tarfile.open(self.archive, "w:gz") as tar:
            for name, payload in {
                f"snapshot/{BLOCK}/meta.json": json.dumps({"ulid": BLOCK, "version": 1}).encode(),
                f"snapshot/{BLOCK}/index": b"index",
                f"snapshot/{BLOCK}/chunks/000001": b"chunk",
            }.items():
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload))
        self.digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        handler = backup_receiver.make_handler(self.backups, "x"*32, "fake-promtool", False)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.server.upload_busy = False
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1/upload"

    def test_verified_publication_and_idempotent_retry(self):
        with patch.object(backup_receiver, "validate_restored", return_value=1) as restored:
            first = upload_verified_archive.upload(self.archive, self.url, "x"*32, "snapshot_1", 1)
            second = upload_verified_archive.upload(self.archive, self.url, "x"*32, "snapshot_1", 1)
        self.assertEqual(first["status"], "verified")
        self.assertEqual(second["status"], "already-verified")
        self.assertEqual(first["sha256"], self.digest)
        self.assertEqual(restored.call_count, 2)
        self.assertEqual(len(list(self.backups.glob("*.tar.gz"))), 1)
        self.assertTrue((self.backups / first["archive"]).is_file())
        self.assertFalse(list(self.backups.glob("*.partial")))

    def test_failed_restore_does_not_publish(self):
        with patch.object(backup_receiver, "validate_restored", side_effect=RuntimeError("test restore failed")):
            with self.assertRaisesRegex(RuntimeError, "HTTP 400"):
                upload_verified_archive.upload(self.archive, self.url, "x"*32, "snapshot_2", 1)
        self.assertFalse(list(self.backups.glob("*.tar.gz")))
        self.assertFalse(list(self.backups.glob("*.partial")))
