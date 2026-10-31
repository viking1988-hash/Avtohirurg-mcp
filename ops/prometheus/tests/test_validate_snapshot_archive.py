"""Offline tests for snapshot archive verification. Run: python -m unittest discover -s ops/prometheus/tests -v"""
import hashlib
import importlib.util
import io
import json
import pathlib
import tarfile
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "validate_snapshot_archive.py"
spec = importlib.util.spec_from_file_location("validate_snapshot_archive", SCRIPT)
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)

BLOCK = "01M5NEYN71T8MY4RF27T1K2GN4"
ROOT = "snapshot-20261023T224352Z"

def archive_at(path, *, bad_meta=False, unsafe=False, missing_index=False, duplicate=False, invalid_version=False):
    with tarfile.open(path, "w:gz") as tar:
        meta = {"ulid": "WRONG" if bad_meta else BLOCK, "version": "bad" if invalid_version else 1}
        items = {
            f"{ROOT}/{BLOCK}/meta.json": json.dumps(meta).encode(),
            f"{ROOT}/{BLOCK}/chunks/000001": b"chunk",
        }
        if not missing_index:
            items[f"{ROOT}/{BLOCK}/index"] = b"index"
        if unsafe:
            items[f"{ROOT}/../outside"] = b"unsafe"
        for name, data in items.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        if duplicate:
            data = items[f"{ROOT}/{BLOCK}/index"]
            info = tarfile.TarInfo(f"{ROOT}/{BLOCK}/index")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return hashlib.sha256(path.read_bytes()).hexdigest()

class ArchiveValidatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = pathlib.Path(self.temp.name) / "backup.tar.gz"

    def test_valid_archive(self):
        sha = archive_at(self.path)
        self.assertEqual(validator.validate(self.path, sha, 1), (sha, 1))

    def test_wrong_checksum(self):
        archive_at(self.path)
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            validator.validate(self.path, "0" * 64, 1)

    def test_wrong_block_count(self):
        sha = archive_at(self.path)
        with self.assertRaisesRegex(ValueError, "Expected 2 blocks"):
            validator.validate(self.path, sha, 2)

    def test_missing_index(self):
        sha = archive_at(self.path, missing_index=True)
        with self.assertRaisesRegex(ValueError, "Missing block files"):
            validator.validate(self.path, sha, 1)

    def test_invalid_meta_ulid(self):
        sha = archive_at(self.path, bad_meta=True)
        with self.assertRaisesRegex(ValueError, "Block ULID mismatch"):
            validator.validate(self.path, sha, 1)

    def test_invalid_metadata_version(self):
        sha = archive_at(self.path, invalid_version=True)
        with self.assertRaisesRegex(ValueError, "Invalid block metadata version"):
            validator.validate(self.path, sha, 1)

    def test_duplicate_member(self):
        sha = archive_at(self.path, duplicate=True)
        with self.assertRaisesRegex(ValueError, "Duplicate archive member"):
            validator.validate(self.path, sha, 1)

    def test_rejects_invalid_expected_block_count(self):
        sha = archive_at(self.path)
        for count in (0, -1, True):
            with self.subTest(count=count), self.assertRaisesRegex(ValueError, "Invalid expected block count"):
                validator.validate(self.path, sha, count)

    def test_path_traversal(self):
        sha = archive_at(self.path, unsafe=True)
        with self.assertRaisesRegex(ValueError, "Unsafe archive path"):
            validator.validate(self.path, sha, 1)

if __name__ == "__main__":
    unittest.main()
