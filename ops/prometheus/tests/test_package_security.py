"""Regression tests for deterministic and no-clobber snapshot packaging."""
import importlib.util
import json
import pathlib
import tempfile
import unittest
from unittest import mock

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "package_completed_snapshot.py"
spec = importlib.util.spec_from_file_location("package_completed_snapshot", SCRIPT)
packager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packager)

BLOCK = "01M5NEYN71T8MY4RF27T1K2GN4"


class SnapshotPackageSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.snapshots = self.root / "snapshots"
        self.snapshots.mkdir()
        self.snapshot = self.snapshots / "synthetic"
        block = self.snapshot / BLOCK
        (block / "chunks").mkdir(parents=True)
        (block / "meta.json").write_text(json.dumps({"ulid": BLOCK, "version": 1}))
        (block / "index").write_bytes(b"index")
        (block / "chunks" / "000001").write_bytes(b"chunks")

    def test_retry_produces_identical_bytes_and_sha(self):
        first = self.root / "first.tar.gz"
        second = self.root / "second.tar.gz"
        sha1, count1 = packager.package(self.snapshot, self.snapshots, first)
        sha2, count2 = packager.package(self.snapshot, self.snapshots, second)
        self.assertEqual((sha1, count1), (sha2, count2))
        self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_competing_destination_is_not_overwritten(self):
        destination = self.root / "competing.tar.gz"
        original_link = packager.os.link

        def race(source, target, **kwargs):
            pathlib.Path(target).write_bytes(b"competitor")
            return original_link(source, target, **kwargs)

        with mock.patch.object(packager.os, "link", side_effect=race):
            with self.assertRaises(FileExistsError):
                packager.package(self.snapshot, self.snapshots, destination)
        self.assertEqual(destination.read_bytes(), b"competitor")
        self.assertFalse((self.root / "competing.tar.gz.partial").exists())


if __name__ == "__main__":
    unittest.main()
