"""Offline tests for atomic backup volume transfer."""
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "stage_verified_archive.py"
spec = importlib.util.spec_from_file_location("stage_verified_archive", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class TransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.src = self.root / "snapshot.tar.gz"
        self.src.write_bytes(b"snapshot test fixture")
        self.dest = self.root / "backup"
        self.dest.mkdir()
        self.sha = hashlib.sha256(self.src.read_bytes()).hexdigest()

    def test_success(self):
        result = module.stage(self.src, self.dest, self.sha)
        self.assertEqual(result.read_bytes(), self.src.read_bytes())
        self.assertEqual(list(self.dest.iterdir()), [result])

    def test_wrong_checksum_leaves_no_archive(self):
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            module.stage(self.src, self.dest, "0" * 64)
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_existing_backup_not_overwritten(self):
        target = self.dest / self.src.name
        target.write_bytes(b"keep")
        with self.assertRaises(FileExistsError):
            module.stage(self.src, self.dest, self.sha)
        self.assertEqual(target.read_bytes(), b"keep")

    def test_missing_destination_rejected(self):
        with self.assertRaisesRegex(ValueError, "Destination"):
            module.stage(self.src, self.root / "missing", self.sha)

    def test_symlink_source_rejected(self):
        link = self.root / "link.tar.gz"
        link.symlink_to(self.src)
        with self.assertRaisesRegex(ValueError, "non-symlink"):
            module.stage(link, self.dest, self.sha)

if __name__ == "__main__":
    unittest.main()
