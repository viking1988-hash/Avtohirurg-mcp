"""Offline tests for snapshot archive verification. Run: python -m unittest discover -s ops/prometheus/tests -v"""
import gzip
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

    def test_rejects_excessive_directory_entries(self):
        with tarfile.open(self.path, "w:gz") as tar:
            for i in range(6):
                info = tarfile.TarInfo(f"{ROOT}/{BLOCK}/chunks/dir-{i}")
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
        digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        old_limit = validator.MAX_ARCHIVE_MEMBERS
        try:
            validator.MAX_ARCHIVE_MEMBERS = 5
            with self.assertRaisesRegex(ValueError, "member count limit"):
                validator.validate(self.path, digest, 1)
        finally:
            validator.MAX_ARCHIVE_MEMBERS = old_limit

    def test_rejects_large_pax_metadata_before_parsing(self):
        with tarfile.open(self.path, "w:gz", format=tarfile.PAX_FORMAT) as tar:
            info = tarfile.TarInfo(f"{ROOT}/{BLOCK}/index")
            info.size = 1
            info.pax_headers = {"comment": "x" * (5 * 1024 * 1024)}
            tar.addfile(info, io.BytesIO(b"x"))
        sha = hashlib.sha256(self.path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, "Decompressed tar stream size limit"):
            validator.validate(self.path, sha, 1, max_uncompressed_bytes=100)

    def test_rejects_repeated_pax_records_before_parser_allocation(self):
        # Small compressed archive with hundreds of thousands of tiny PAX
        # records must be rejected by raw-header inspection, not tarfile.
        records = b"6 a=b\n" * 500000
        info = tarfile.TarInfo("pax-extended-header")
        info.type = tarfile.XHDTYPE
        info.size = len(records)
        header = info.tobuf(format=tarfile.USTAR_FORMAT)
        padding = b"\x00" * ((-len(records)) % 512)
        with gzip.open(self.path, "wb") as compressed:
            compressed.write(header)
            compressed.write(records)
            compressed.write(padding)
            compressed.write(b"\x00" * 1024)
        digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, "PAX metadata is not allowed"):
            validator.validate(self.path, digest, 1, max_uncompressed_bytes=100)

    def test_rejects_global_pax_metadata_amplification(self):
        with tarfile.open(self.path, "w:gz", format=tarfile.PAX_FORMAT,
                          pax_headers={f"k{i}": "v" for i in range(2000)}) as tar:
            for i in range(2000):
                info = tarfile.TarInfo(f"{ROOT}/{BLOCK}/chunks/dir-{i}")
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
        digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, "PAX metadata is not allowed"):
            validator.validate(self.path, digest, 1, max_uncompressed_bytes=100)

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

    def test_rejects_compression_bomb_by_cumulative_uncompressed_size(self):
        with tarfile.open(self.path, "w:gz") as tar:
            meta = json.dumps({"ulid": BLOCK, "version": 1}).encode()
            items = {
                f"{ROOT}/{BLOCK}/meta.json": meta,
                f"{ROOT}/{BLOCK}/index": b"x" * 1024,
                f"{ROOT}/{BLOCK}/chunks/000001": b"x" * 1024,
            }
            for name, data in items.items():
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.assertLess(self.path.stat().st_size, 1024)
        with self.assertRaisesRegex(ValueError, "Uncompressed archive size limit exceeded"):
            validator.validate(self.path, digest, 1, max_uncompressed_bytes=1500)

    def test_rejects_invalid_uncompressed_limit(self):
        digest = archive_at(self.path)
        for limit in (0, -1, True):
            with self.subTest(limit=limit), self.assertRaisesRegex(ValueError, "Invalid uncompressed archive size limit"):
                validator.validate(self.path, digest, 1, max_uncompressed_bytes=limit)

    def test_path_traversal(self):
        sha = archive_at(self.path, unsafe=True)
        with self.assertRaisesRegex(ValueError, "Unsafe archive path"):
            validator.validate(self.path, sha, 1)

if __name__ == "__main__":
    unittest.main()
