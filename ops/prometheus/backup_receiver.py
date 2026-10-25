#!/usr/bin/env python3
"""Authenticated receiver for completed Prometheus snapshot archives.

The receiver never touches the live Prometheus TSDB. Uploads are written to a
private temporary file, checksum/archive/block validated, extracted only into a
temporary restore-test directory, checked with promtool, then atomically
published in the backup volume. Retention is opt-in and runs only after a
verified new archive has been published.
"""
import hashlib
import hmac
import json
import os
import re
import shutil
import tempfile
import tarfile
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from validate_snapshot_archive import validate as validate_archive
from validate_restored_tsdb import validate as validate_restored

MAX_UPLOAD_BYTES = int(os.environ.get("PROMETHEUS_BACKUP_MAX_BYTES", str(512 * 1024 * 1024)))
UPLOAD_PATH = "/v1/upload"
BACKUP_NAME = re.compile(r"^prometheus-(\d{8}T\d{6}Z)-([0-9A-Za-z_-]{1,80})\.tar\.gz$")
_state_lock = threading.Lock()
_state = {
    "last_success_timestamp": 0,
    "last_restore_success_timestamp": 0,
    "last_archive_bytes": 0,
    "failures_total": 0,
    "retention_deletions_total": 0,
    "last_error": "",
}


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _extract_validated(archive, destination):
    """Extract regular files/directories after the archive validator has passed."""
    destination = Path(destination)
    with tarfile.open(archive, "r:gz") as stream:
        members = list(stream)
        if not members:
            raise ValueError("Empty snapshot archive")
        root = PurePosixPath(members[0].name.removeprefix("./")).parts[0]
        for member in members:
            parts = PurePosixPath(member.name.removeprefix("./")).parts
            if not parts or parts[0] != root:
                raise ValueError("Archive has multiple top-level roots")
            relative = parts[1:]
            if not relative:
                continue
            target = destination.joinpath(*relative)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            source = stream.extractfile(member)
            if source is None:
                raise ValueError(f"Could not read archive member: {member.name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with source, target.open("xb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)


def _timestamp_from_name(path):
    match = BACKUP_NAME.fullmatch(path.name)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def retention_candidates(directory, now=None):
    """Return old managed archives outside the 7-daily + 4-weekly retention set."""
    directory = Path(directory)
    now = now or datetime.now(timezone.utc)
    entries = []
    for path in directory.iterdir():
        when = _timestamp_from_name(path)
        if when is None or path.is_symlink() or not path.is_file():
            continue
        entries.append((when, path))
    entries.sort(key=lambda item: (item[0], item[1].name), reverse=True)
    if not entries:
        return []

    keep = {entries[0][1]}  # Always preserve the newest verified archive.
    daily = {}
    weekly = {}
    for when, path in entries:
        age_days = max(0, (now.date() - when.date()).days)
        if age_days < 7:
            daily.setdefault(when.date(), path)
        elif age_days < 35:
            bucket = (age_days - 7) // 7
            weekly.setdefault(bucket, path)
    keep.update(daily.values())
    keep.update(weekly.values())
    return [path for _, path in entries if path not in keep]


def _write_status(path, payload):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix=".status-", suffix=".partial", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(payload, output, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def make_handler(backup_dir, token, promtool="/bin/promtool", retention_enabled=False):
    backup_dir = Path(backup_dir)
    if not backup_dir.is_dir() or backup_dir.is_symlink():
        raise ValueError("Backup directory must exist and not be a symlink")
    if not isinstance(token, str) or len(token) < 32:
        raise ValueError("PROMETHEUS_BACKUP_TOKEN must contain at least 32 characters")

    class Handler(BaseHTTPRequestHandler):
        server_version = "PrometheusBackupReceiver/1"

        def log_message(self, fmt, *args):
            # Keep access logs free of headers and request bodies.
            super().log_message(fmt, *args)

        def _respond(self, code, payload):
            body = json.dumps(payload, sort_keys=True).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/healthz":
                with _state_lock:
                    payload = {
                        "status": "ok",
                        "last_success_timestamp": _state["last_success_timestamp"],
                        "last_restore_success_timestamp": _state["last_restore_success_timestamp"],
                    }
                self._respond(200, payload)
                return
            if path == "/metrics":
                with _state_lock:
                    snapshot = dict(_state)
                body = (
                    "# HELP prometheus_backup_last_success_timestamp_seconds Last verified archive time.\n"
                    "# TYPE prometheus_backup_last_success_timestamp_seconds gauge\n"
                    f"prometheus_backup_last_success_timestamp_seconds {snapshot['last_success_timestamp']}\n"
                    "# HELP prometheus_backup_last_restore_success_timestamp_seconds Last promtool restore test time.\n"
                    "# TYPE prometheus_backup_last_restore_success_timestamp_seconds gauge\n"
                    f"prometheus_backup_last_restore_success_timestamp_seconds {snapshot['last_restore_success_timestamp']}\n"
                    "# HELP prometheus_backup_failures_total Failed uploads or restore checks.\n"
                    "# TYPE prometheus_backup_failures_total counter\n"
                    f"prometheus_backup_failures_total {snapshot['failures_total']}\n"
                    "# HELP prometheus_backup_retention_deletions_total Retention deletions after verified upload.\n"
                    "# TYPE prometheus_backup_retention_deletions_total counter\n"
                    f"prometheus_backup_retention_deletions_total {snapshot['retention_deletions_total']}\n"
                    "# HELP prometheus_backup_last_archive_bytes Size of last verified archive.\n"
                    "# TYPE prometheus_backup_last_archive_bytes gauge\n"
                    f"prometheus_backup_last_archive_bytes {snapshot['last_archive_bytes']}\n"
                ).encode("ascii")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; version=0.0.4")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            self._respond(404, {"error": "not found"})

        def do_POST(self):
            if urlsplit(self.path).path != UPLOAD_PATH:
                self._respond(404, {"error": "not found"})
                return
            supplied = self.headers.get("Authorization", "")
            expected = f"Bearer {token}"
            if not hmac.compare_digest(supplied, expected):
                self._respond(401, {"error": "unauthorized"})
                return
            try:
                self._receive()
            except (OSError, ValueError, RuntimeError, EOFError, tarfile.TarError) as exc:
                with _state_lock:
                    _state["failures_total"] += 1
                    _state["last_error"] = type(exc).__name__
                self._respond(400, {"error": "backup rejected", "reason": str(exc)[:200]})

        def _receive(self):
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError as exc:
                raise ValueError("Content-Length is required") from exc
            if length < 1 or length > MAX_UPLOAD_BYTES:
                raise ValueError("Archive size is outside the configured limit")
            expected_sha = self.headers.get("X-Archive-SHA256", "")
            if not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha):
                raise ValueError("Invalid X-Archive-SHA256")
            expected_blocks_raw = self.headers.get("X-Expected-Blocks", "")
            if not expected_blocks_raw.isdecimal() or int(expected_blocks_raw) < 1:
                raise ValueError("Invalid X-Expected-Blocks")
            expected_blocks = int(expected_blocks_raw)
            snapshot_id = self.headers.get("X-Snapshot-ID", "")
            if not re.fullmatch(r"[0-9A-Za-z_-]{1,80}", snapshot_id):
                raise ValueError("Invalid X-Snapshot-ID")

            with _state_lock:
                if getattr(self.server, "upload_busy", False):
                    raise RuntimeError("Another backup upload is being processed")
                self.server.upload_busy = True
            temporary = None
            try:
                fd, temporary = tempfile.mkstemp(prefix=".incoming-", suffix=".partial", dir=backup_dir)
                digest = hashlib.sha256()
                remaining = length
                with os.fdopen(fd, "wb") as output:
                    while remaining:
                        chunk = self.rfile.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise EOFError("Upload ended before Content-Length")
                        output.write(chunk)
                        digest.update(chunk)
                        remaining -= len(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                actual_sha = digest.hexdigest()
                if not hmac.compare_digest(actual_sha.lower(), expected_sha.lower()):
                    raise ValueError("SHA-256 mismatch")
                validate_archive(temporary, expected_sha, expected_blocks)

                with tempfile.TemporaryDirectory(prefix="prometheus-restore-test-") as restored:
                    _extract_validated(temporary, restored)
                    validate_restored(restored, promtool, expected_blocks, timeout=180)

                existing = sorted(backup_dir.glob(f"prometheus-*-{snapshot_id}.tar.gz"))
                for prior in existing:
                    if prior.is_symlink() or not prior.is_file():
                        continue
                    if hmac.compare_digest(_sha256(prior).lower(), actual_sha.lower()):
                        self._respond(200, {
                            "status": "already-verified",
                            "archive": prior.name,
                            "sha256": actual_sha,
                            "blocks": expected_blocks,
                            "restore_test": "promtool-tsdb-analyze-pass",
                            "retention_deletions": 0,
                        })
                        return
                    raise ValueError("Snapshot ID already exists with a different archive digest")

                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                final_name = f"prometheus-{stamp}-{snapshot_id}.tar.gz"
                target = backup_dir / final_name
                if target.exists() or target.is_symlink():
                    raise FileExistsError("Refusing to overwrite an existing archive")
                os.rename(temporary, target)
                temporary = None
                now_ts = int(datetime.now(timezone.utc).timestamp())
                with _state_lock:
                    _state["last_success_timestamp"] = now_ts
                    _state["last_restore_success_timestamp"] = now_ts
                    _state["last_archive_bytes"] = length
                    _state["last_error"] = ""

                status_warning = ""
                try:
                    _write_status(backup_dir / "last-verified.json", {
                        "archive": final_name,
                        "sha256": actual_sha,
                        "blocks": expected_blocks,
                        "verified_at": datetime.now(timezone.utc).isoformat(),
                        "restore_test": "promtool-tsdb-analyze-pass",
                    })
                except OSError as exc:
                    status_warning = type(exc).__name__

                deleted = 0
                retention_warning = ""
                if retention_enabled:
                    try:
                        for old in retention_candidates(backup_dir):
                            old.unlink()
                            deleted += 1
                        with _state_lock:
                            _state["retention_deletions_total"] += deleted
                    except OSError as exc:
                        retention_warning = type(exc).__name__
                        with _state_lock:
                            _state["last_error"] = "retention-" + retention_warning
                self._respond(201, {
                    "status": "verified",
                    "archive": final_name,
                    "sha256": actual_sha,
                    "blocks": expected_blocks,
                    "restore_test": "promtool-tsdb-analyze-pass",
                    "retention_deletions": deleted,
                    "retention_warning": retention_warning,
                    "status_warning": status_warning,
                })
            finally:
                if temporary and os.path.exists(temporary):
                    os.unlink(temporary)
                with _state_lock:
                    self.server.upload_busy = False

    return Handler


def load_last_verified(backup_dir):
    """Restore success gauges from the durable volume after process restart."""
    status_file = Path(backup_dir) / "last-verified.json"
    try:
        payload = json.loads(status_file.read_text(encoding="utf-8"))
        if payload.get("restore_test") != "promtool-tsdb-analyze-pass":
            return False
        verified_at = datetime.fromisoformat(payload["verified_at"])
        timestamp = int(verified_at.timestamp())
        archive = payload["archive"]
        if not BACKUP_NAME.fullmatch(archive):
            return False
        with _state_lock:
            _state["last_success_timestamp"] = timestamp
            _state["last_restore_success_timestamp"] = timestamp
            archive_path = Path(backup_dir) / archive
            if archive_path.is_file() and not archive_path.is_symlink():
                _state["last_archive_bytes"] = archive_path.stat().st_size
        return True
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return False


def serve():
    backup_dir = Path(os.environ.get("PROMETHEUS_BACKUP_DIR", "/backup/prometheus-snapshots"))
    backup_dir.mkdir(parents=True, exist_ok=True)
    load_last_verified(backup_dir)
    token = os.environ.get("PROMETHEUS_BACKUP_TOKEN", "")
    host = os.environ.get("PROMETHEUS_BACKUP_BIND", "0.0.0.0")
    port = int(os.environ.get("PORT", os.environ.get("PROMETHEUS_BACKUP_PORT", "8080")))
    retention = os.environ.get("PROMETHEUS_BACKUP_RETENTION_ENABLED", "false").lower() == "true"
    handler = make_handler(backup_dir, token, os.environ.get("PROMTOOL", "/bin/promtool"), retention)
    server = ThreadingHTTPServer((host, port), handler)
    server.upload_busy = False
    print(f"Prometheus backup receiver ready on {host}:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    serve()
