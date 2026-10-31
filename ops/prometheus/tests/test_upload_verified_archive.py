"""Negative tests for receiver verification contract."""
import importlib.util
import json
from pathlib import Path
import tempfile
from unittest import TestCase
from unittest.mock import patch

FILE = Path(__file__).resolve().parents[1] / "upload_verified_archive.py"
spec = importlib.util.spec_from_file_location("upload_verified_archive", FILE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class ReceiverAttestationTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.archive = str(Path(self.temp.name) / "snapshot.tar.gz")
        Path(self.archive).write_bytes(b"placeholder")
    def test_rejects_short_token_before_opening_archive(self):
        with self.assertRaisesRegex(ValueError, "token"):
            module.upload(self.archive, "http://127.0.0.1:8080/v1/upload", "short", "snapshot_1", 1)

    def test_rejects_nonprivate_ambiguous_host(self):
        with self.assertRaisesRegex(ValueError, "hostname"):
            module.upload(self.archive, "http://localhost:8080/v1/upload", "x"*32, "snapshot_1", 1)

    def test_rejects_query_in_receiver_url(self):
        with self.assertRaisesRegex(ValueError, "receiver URL"):
            module.upload(self.archive, "http://127.0.0.1:8080/v1/upload?x=1", "x"*32, "snapshot_1", 1)

    def test_rejects_invalid_snapshot_id(self):
        with self.assertRaisesRegex(ValueError, "snapshot ID"):
            module.upload(self.archive, "http://127.0.0.1:8080/v1/upload", "x"*32, "../escape", 1)
