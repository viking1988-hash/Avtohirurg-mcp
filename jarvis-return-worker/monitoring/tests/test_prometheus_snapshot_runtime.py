"""Offline security tests for the private Prometheus snapshot proxy."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

MONITORING = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MONITORING))
import prometheus_snapshot_runtime as runtime

TOKEN = "synthetic-ci-token-" + "x" * 32
SNAPSHOT = "20261025T170000Z-ci-snapshot"
BLOCK = "01M5NEYN71T8MY4RF27T1K2GN4"


class AdminProxyTests(unittest.TestCase):
    def test_authorized_snapshot_post_is_allowed(self):
        self.assertTrue(runtime.is_allowed_admin_request(
            "POST", runtime.ADMIN_SNAPSHOT_PATH, "skip_head=false",
            f"Bearer {TOKEN}", TOKEN,
        ))

    def test_encoded_admin_paths_cannot_bypass_the_guard(self):
        encoded = "/api/v1/%61dmin/tsdb/snapshot"
        self.assertTrue(runtime.is_admin_path(encoded))
        self.assertFalse(runtime.is_allowed_admin_request(
            "POST", encoded, "skip_head=false", f"Bearer {TOKEN}", TOKEN,
        ))

    def test_missing_or_wrong_token_is_rejected(self):
        for supplied in ("", "Bearer wrong", f"Basic {TOKEN}"):
            with self.subTest(supplied=supplied):
                self.assertFalse(runtime.is_allowed_admin_request(
                    "POST", runtime.ADMIN_SNAPSHOT_PATH, "skip_head=false",
                    supplied, TOKEN,
                ))

    def test_only_snapshot_post_and_known_query_are_allowed(self):
        denied = [
            ("GET", runtime.ADMIN_SNAPSHOT_PATH, "skip_head=false"),
            ("DELETE", runtime.ADMIN_SNAPSHOT_PATH, ""),
            ("POST", "/api/v1/admin/tsdb/clean_tombstones", ""),
            ("POST", runtime.ADMIN_SNAPSHOT_PATH, "skip_head=false&delete=true"),
            ("POST", runtime.ADMIN_SNAPSHOT_PATH, "skip_head=unknown"),
        ]
        for method, path, query in denied:
            with self.subTest(method=method, path=path, query=query):
                self.assertFalse(runtime.is_allowed_admin_request(
                    method, path, query, f"Bearer {TOKEN}", TOKEN,
                ))


class SnapshotArchiveTests(unittest.TestCase):
    def test_archive_is_created_only_for_regular_snapshot_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = root / "snapshots" / SNAPSHOT
            block = snapshot / BLOCK
            (block / "chunks").mkdir(parents=True)
            (block / "meta.json").write_text(
                json.dumps({"ulid": BLOCK, "version": 1}), encoding="utf-8",
            )
            (block / "index").write_bytes(b"synthetic-index")
            (block / "chunks" / "000001").write_bytes(b"synthetic-chunk")
            archive, digest, count = runtime.create_archive(
                snapshot, SNAPSHOT, root / "work", 1024 * 1024,
            )
            self.assertTrue(archive.is_file())
            self.assertEqual(len(digest), 64)
            self.assertEqual(count, 1)

    def test_symlinked_snapshot_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = root / "snapshots" / SNAPSHOT
            block = snapshot / BLOCK
            block.mkdir(parents=True)
            outside = root / "outside"
            outside.write_text("synthetic", encoding="utf-8")
            (block / "link").symlink_to(outside)
            with self.assertRaisesRegex(ValueError, "symlink"):
                runtime.create_archive(snapshot, SNAPSHOT, root / "work", 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
