"""Offline integration tests for archive validation + atomic transfer."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

OPS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(OPS))
spec = importlib.util.spec_from_file_location("ingest_snapshot", OPS / "ingest_snapshot.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
BLOCK = "01M5NEYN71T8MY4RF27T1K2GN4"

class IngestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.archive = root / "prometheus-20261024T120000Z-fixture.tar.gz"
        self.backup = root / "backup"
        self.backup.mkdir()
        with tarfile.open(self.archive, "w:gz") as tar:
            files = {
                f"snapshot/{BLOCK}/meta.json": json.dumps({"ulid": BLOCK, "version": 1}).encode(),
                f"snapshot/{BLOCK}/index": b"index",
                f"snapshot/{BLOCK}/chunks/000001": b"chunk",
            }
            for name, data in files.items():
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        self.sha = hashlib.sha256(self.archive.read_bytes()).hexdigest()

    def test_end_to_end_ingest(self):
        target = mod.ingest(self.archive, self.backup, self.sha, 1)
        self.assertEqual(target.read_bytes(), self.archive.read_bytes())

    def test_bad_digest_never_writes(self):
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            mod.ingest(self.archive, self.backup, "0" * 64, 1)
        self.assertEqual(list(self.backup.iterdir()), [])

    def test_bad_block_count_never_writes(self):
        with self.assertRaisesRegex(ValueError, "Expected 2 blocks"):
            mod.ingest(self.archive, self.backup, self.sha, 2)
        self.assertEqual(list(self.backup.iterdir()), [])

    def test_existing_backup_preserved(self):
        target = self.backup / self.archive.name
        target.write_bytes(b"original")
        with self.assertRaises(FileExistsError):
            mod.ingest(self.archive, self.backup, self.sha, 1)
        self.assertEqual(target.read_bytes(), b"original")

if __name__ == "__main__":
    unittest.main()
