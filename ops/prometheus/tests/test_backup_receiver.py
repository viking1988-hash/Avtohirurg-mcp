"""HTTP integration tests for authenticated backup receiver and restore gate."""
import hashlib
import http.client
import io
import json
import os
import socket
import time
from pathlib import Path
import sys
import tarfile
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from unittest.mock import patch

OPS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(OPS))
import backup_receiver as receiver

BLOCK = "01M5NEYN71T8MY4RF27T1K2GN4"
TOKEN = "test-only-token-" + "x" * 32


def make_archive(path, *, include_index=True):
    with tarfile.open(path, "w:gz") as archive:
        files = {
            f"snapshot/{BLOCK}/meta.json": json.dumps({"ulid": BLOCK, "version": 1}).encode(),
            f"snapshot/{BLOCK}/chunks/000001": b"chunk",
        }
        if include_index:
            files[f"snapshot/{BLOCK}/index"] = b"index"
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class ReceiverTests(unittest.TestCase):
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
        self.url = self.server.server_address

    def post(self, data, digest, *, token=TOKEN, blocks="1", snapshot="fixture-snapshot"):
        connection = http.client.HTTPConnection(*self.url, timeout=10)
        self.addCleanup(connection.close)
        connection.request(
            "POST",
            receiver.UPLOAD_PATH,
            body=data,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/gzip",
                "Content-Length": str(len(data)),
                "X-Archive-SHA256": digest,
                "X-Expected-Blocks": blocks,
                "X-Snapshot-ID": snapshot,
            },
        )
        response = connection.getresponse()
        body = json.loads(response.read())
        return response.status, body

    def test_unauthenticated_incomplete_headers_release_bounded_connection_slot(self):
        class TinyServer(receiver.BoundedHTTPServer):
            max_connections = 1
            header_timeout_seconds = 0.2

        handler = receiver.make_handler(self.backups, TOKEN, str(self.promtool), False)
        server = TinyServer(("127.0.0.1", 0), handler)
        server.upload_busy = False
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        stalled = socket.create_connection(server.server_address, timeout=2)
        self.addCleanup(stalled.close)
        stalled.sendall(b"GET /health HTTP/1.1\r\nHost: incomplete")
        self.assertTrue(server._connection_slots.acquire(blocking=False) is False)
        time.sleep(0.5)
        self.assertTrue(server._connection_slots.acquire(blocking=False))
        server._connection_slots.release()
        connection = http.client.HTTPConnection(*server.server_address, timeout=2)
        self.addCleanup(connection.close)
        connection.request("GET", "/healthz")
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        response.read()

    def test_valid_upload_is_restore_checked_before_publish(self):
        archive = self.root / "fixture.tar.gz"
        digest = make_archive(archive)
        code, result = self.post(archive.read_bytes(), digest)
        self.assertEqual(code, 201)
        self.assertEqual(result["status"], "verified")
        saved = self.backups / result["archive"]
        self.assertTrue(saved.is_file())
        self.assertEqual(hashlib.sha256(saved.read_bytes()).hexdigest(), digest)
        self.assertEqual(result["restore_test"], "promtool-tsdb-analyze-pass")
        self.assertTrue((self.backups / "last-verified.json").is_file())

    def test_atomic_publish_rejects_competing_destination_without_overwrite(self):
        archive = self.root / "fixture.tar.gz"
        digest = make_archive(archive)
        competing = b"external-archive-must-survive"
        real_link = os.link
        observed = []

        def competing_link(source, destination, **kwargs):
            destination = Path(destination)
            destination.write_bytes(competing)
            observed.append(destination)
            return real_link(source, destination, **kwargs)

        with patch.object(receiver.os, "link", side_effect=competing_link):
            status, _ = self.post(archive.read_bytes(), digest)
        self.assertEqual(status, 400)
        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0].read_bytes(), competing)
        self.assertEqual(list(self.backups.glob(".incoming-*.partial")), [])
        self.assertFalse((self.backups / "last-verified.json").exists())

    def test_snapshot_id_suffix_does_not_collide(self):
        archive = self.root / "fixture.tar.gz"
        digest = make_archive(archive)
        data = archive.read_bytes()
        first_code, _ = self.post(data, digest, snapshot="prefix-shared")
        second_code, second = self.post(data, digest, snapshot="shared")
        self.assertEqual(first_code, 201)
        self.assertEqual(second_code, 201)
        self.assertTrue(second["archive"].endswith("-shared.tar.gz"))
        self.assertEqual(len(list(self.backups.glob("prometheus-*.tar.gz"))), 2)

    def test_retention_preserves_just_published_archive(self):
        self.server.RequestHandlerClass = receiver.make_handler(self.backups, TOKEN, str(self.promtool), True)
        archive = self.root / "fixture.tar.gz"
        digest = make_archive(archive)
        data = archive.read_bytes()
        first_code, _ = self.post(data, digest, snapshot="z")
        second_code, second = self.post(data, digest, snapshot="a")
        self.assertEqual(first_code, 201)
        self.assertEqual(second_code, 201)
        self.assertTrue((self.backups / second["archive"]).is_file())

    def test_stalled_upload_releases_exclusive_slot(self):
        connection = socket.create_connection(self.url, timeout=5)
        self.addCleanup(connection.close)
        connection.sendall(
            (f"POST {receiver.UPLOAD_PATH} HTTP/1.1\r\n"
             f"Host: 127.0.0.1\r\n"
             f"Authorization: Bearer {TOKEN}\r\n"
             "Content-Length: 1024\r\n"
             f"X-Archive-SHA256: {'0' * 64}\r\n"
             "X-Expected-Blocks: 1\r\n"
             "X-Snapshot-ID: stalled\r\n\r\n").encode("ascii")
        )
        for _ in range(100):
            if self.server.upload_busy:
                break
            time.sleep(0.01)
        self.assertTrue(self.server.upload_busy)
        connection.shutdown(socket.SHUT_WR)
        for _ in range(100):
            if not self.server.upload_busy:
                break
            time.sleep(0.01)
        self.assertFalse(self.server.upload_busy)
        self.assertEqual(list(self.backups.glob(".incoming-*.partial")), [])

    def test_trickle_upload_expires_absolute_deadline(self):
        with patch.object(receiver, "UPLOAD_DEADLINE_SECONDS", 0.2):
            connection = socket.create_connection(self.url, timeout=5)
            self.addCleanup(connection.close)
            connection.sendall(
                (f"POST {receiver.UPLOAD_PATH} HTTP/1.1\r\n"
                 "Host: 127.0.0.1\r\n"
                 f"Authorization: Bearer {TOKEN}\r\n"
                 "Content-Length: 1024\r\n"
                 f"X-Archive-SHA256: {'0' * 64}\r\n"
                 "X-Expected-Blocks: 1\r\n"
                 "X-Snapshot-ID: trickle\r\n\r\n").encode("ascii")
            )
            for _ in range(100):
                if self.server.upload_busy:
                    break
                time.sleep(0.005)
            self.assertTrue(self.server.upload_busy)
            for _ in range(15):
                try:
                    connection.sendall(b"x")
                except OSError:
                    break
                time.sleep(0.05)
            for _ in range(100):
                if not self.server.upload_busy:
                    break
                time.sleep(0.01)
            self.assertFalse(self.server.upload_busy)
            self.assertEqual(list(self.backups.glob(".incoming-*.partial")), [])

    def test_unauthorized_upload_writes_nothing(self):
        archive = self.root / "fixture.tar.gz"
        digest = make_archive(archive)
        code, _ = self.post(archive.read_bytes(), digest, token="wrong")
        self.assertEqual(code, 401)
        self.assertEqual(list(self.backups.iterdir()), [])

    def test_bad_checksum_writes_nothing(self):
        archive = self.root / "fixture.tar.gz"
        make_archive(archive)
        code, _ = self.post(archive.read_bytes(), "0" * 64)
        self.assertEqual(code, 400)
        self.assertEqual(list(self.backups.iterdir()), [])

    def test_repeated_upload_is_idempotent(self):
        archive = self.root / "fixture.tar.gz"
        digest = make_archive(archive)
        data = archive.read_bytes()
        first_code, first = self.post(data, digest)
        second_code, second = self.post(data, digest)
        self.assertEqual(first_code, 201)
        self.assertEqual(second_code, 200)
        self.assertEqual(second["status"], "already-verified")
        self.assertEqual(first["archive"], second["archive"])
        archives = list(self.backups.glob("prometheus-*.tar.gz"))
        self.assertEqual(len(archives), 1)

    def test_compression_bomb_rejected_without_publication(self):
        archive = self.root / "bomb.tar.gz"
        with tarfile.open(archive, "w:gz") as stream:
            for name, data in {
                f"snapshot/{BLOCK}/meta.json": json.dumps({"ulid": BLOCK, "version": 1}).encode(),
                f"snapshot/{BLOCK}/index": b"x" * 2048,
                f"snapshot/{BLOCK}/chunks/000001": b"x" * 2048,
            }.items():
                info = tarfile.TarInfo(name)
                info.size = len(data)
                stream.addfile(info, io.BytesIO(data))
        payload = archive.read_bytes()
        self.assertLess(len(payload), 1024)
        with patch.object(receiver, "MAX_EXTRACT_BYTES", 2500):
            status, body = self.post(payload, hashlib.sha256(payload).hexdigest())
        self.assertEqual(status, 400)
        self.assertIn("Uncompressed archive size limit exceeded", body["reason"])
        self.assertEqual(list(self.backups.iterdir()), [])

    def test_oversized_pax_metadata_rejected_over_http(self):
        archive = self.root / "pax-bomb.tar.gz"
        with tarfile.open(archive, "w:gz", format=tarfile.PAX_FORMAT) as stream:
            info = tarfile.TarInfo(f"snapshot/{BLOCK}/index")
            info.size = 1
            info.pax_headers = {"comment": "x" * (5 * 1024 * 1024)}
            stream.addfile(info, io.BytesIO(b"x"))
        payload = archive.read_bytes()
        with patch.object(receiver, "MAX_EXTRACT_BYTES", 100):
            status, body = self.post(payload, hashlib.sha256(payload).hexdigest())
        self.assertEqual(status, 400)
        self.assertIn("Decompressed tar stream size limit exceeded", body["reason"])
        self.assertEqual(list(self.backups.iterdir()), [])

    def test_excessive_tar_members_rejected_over_http(self):
        archive = self.root / "many-members.tar.gz"
        with tarfile.open(archive, "w:gz") as stream:
            for index in range(6):
                info = tarfile.TarInfo(f"snapshot/{BLOCK}/extra-{index}")
                info.type = tarfile.DIRTYPE
                stream.addfile(info)
        payload = archive.read_bytes()
        with patch("validate_snapshot_archive.MAX_ARCHIVE_MEMBERS", 5):
            status, body = self.post(payload, hashlib.sha256(payload).hexdigest())
        self.assertEqual(status, 400)
        self.assertIn("Archive member count limit exceeded", body["reason"])
        self.assertEqual(list(self.backups.iterdir()), [])

    def test_corrupt_archive_writes_nothing(self):
        data = b"not an archive"
        digest = hashlib.sha256(data).hexdigest()
        code, _ = self.post(data, digest)
        self.assertEqual(code, 400)
        self.assertEqual(list(self.backups.iterdir()), [])

    def test_last_verified_status_survives_receiver_restart(self):
        archive_name = "prometheus-20261025T170000Z-fixture-snapshot.tar.gz"
        (self.backups / archive_name).write_bytes(b"verified")
        (self.backups / "last-verified.json").write_text(
            json.dumps({
                "archive": archive_name,
                "verified_at": "2026-10-25T17:00:00+00:00",
                "restore_test": "promtool-tsdb-analyze-pass",
            }),
            encoding="utf-8",
        )
        receiver._state["last_success_timestamp"] = 0
        receiver._state["last_restore_success_timestamp"] = 0
        self.assertTrue(receiver.load_last_verified(self.backups))
        self.assertEqual(receiver._state["last_success_timestamp"], 1792947600)
        self.assertEqual(receiver._state["last_restore_success_timestamp"], 1792947600)
        self.assertEqual(receiver._state["last_archive_bytes"], len(b"verified"))

    def test_health_and_metrics_do_not_expose_token(self):
        connection = http.client.HTTPConnection(*self.url, timeout=10)
        connection.request("GET", "/healthz")
        health = connection.getresponse()
        self.assertEqual(health.status, 200)
        self.assertEqual(json.loads(health.read())["status"], "ok")
        connection.request("GET", "/metrics")
        metrics = connection.getresponse().read().decode()
        self.assertIn("prometheus_backup_failures_total", metrics)
        self.assertIn("prometheus_backup_volume_free_bytes", metrics)
        self.assertNotIn(TOKEN, metrics)
        connection.close()


class RetentionTests(unittest.TestCase):
    def test_retention_keeps_latest_seven_daily_and_four_weekly_buckets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = datetime(2026, 10, 25, tzinfo=timezone.utc)
            paths = []
            for age in range(0, 50):
                stamp = (now.replace(hour=0) - __import__("datetime").timedelta(days=age)).strftime("%Y%m%dT010000Z")
                path = root / f"prometheus-{stamp}-snapshot-{age}.tar.gz"
                path.write_bytes(b"synthetic")
                paths.append(path)
            candidates = receiver.retention_candidates(root, now)
            kept = set(paths) - set(candidates)
            self.assertGreaterEqual(len(kept), 11)
            self.assertIn(paths[0], kept)
            self.assertTrue(all(p.exists() for p in paths))  # planner is non-destructive


if __name__ == "__main__":
    unittest.main()
