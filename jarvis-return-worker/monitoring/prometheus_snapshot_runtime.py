#!/usr/bin/env python3
"""Run Prometheus with loopback-only Admin API, private read proxy and backup scheduler."""
import hashlib
import hmac
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit
from urllib.request import Request, urlopen

ADMIN_SNAPSHOT_PATH = "/api/v1/admin/tsdb/snapshot"
SNAPSHOT_NAME = re.compile(r"^[0-9A-Za-z_-]{1,80}$")
HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
               "te", "trailers", "transfer-encoding", "upgrade", "host", "authorization"}


def is_admin_path(path):
    # Check a decoded view so escaped path bytes cannot bypass the Admin API guard.
    return unquote(path).startswith("/api/v1/admin")


def is_allowed_admin_request(method, path, query, supplied_token, expected_token):
    if method != "POST" or path != ADMIN_SNAPSHOT_PATH:
        return False
    params = parse_qs(query, keep_blank_values=True)
    if set(params) - {"skip_head"} or len(params.get("skip_head", [])) > 1:
        return False
    if params.get("skip_head", ["false"])[0].lower() not in ("true", "false"):
        return False
    return hmac.compare_digest(supplied_token, f"Bearer {expected_token}")


def make_proxy_handler(token, local_port):
    class ProxyHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            # Never log authorization headers or request bodies.
            super().log_message(fmt, *args)

        def _error(self, status, message):
            payload = json.dumps({"error": message}).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)

        def _forward(self):
            parsed = urlsplit(self.path)
            if is_admin_path(parsed.path):
                if not is_allowed_admin_request(
                    self.command, parsed.path, parsed.query,
                    self.headers.get("Authorization", ""), token
                ):
                    self._error(403, "admin endpoint is restricted to an authenticated snapshot POST")
                    return
            if unquote(parsed.path) in ("/-/quit", "/-/reload"):
                self._error(403, "lifecycle endpoint is disabled")
                return
            if self.command not in ("GET", "HEAD", "POST", "OPTIONS"):
                self._error(405, "method not allowed")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._error(400, "invalid content length")
                return
            if length < 0 or length > 8 * 1024 * 1024:
                self._error(413, "request body too large")
                return
            body = self.rfile.read(length) if length else None
            headers = {
                key: value for key, value in self.headers.items()
                if key.lower() not in HOP_HEADERS and key.lower() != "content-length"
            }
            if length:
                headers["Content-Length"] = str(length)
            connection = http.client.HTTPConnection("127.0.0.1", local_port, timeout=120)
            try:
                connection.request(self.command, self.path, body=body, headers=headers)
                response = connection.getresponse()
                self.send_response(response.status, response.reason)
                for key, value in response.getheaders():
                    if key.lower() not in HOP_HEADERS:
                        self.send_header(key, value)
                self.send_header("Connection", "close")
                self.end_headers()
                while True:
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
            except (OSError, http.client.HTTPException) as exc:
                self._error(502, type(exc).__name__)
            finally:
                connection.close()

        do_GET = _forward
        do_HEAD = _forward
        do_POST = _forward
        do_OPTIONS = _forward

    return ProxyHandler


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_snapshot_tree(path):
    path = Path(path)
    if not path.is_dir() or path.is_symlink():
        raise ValueError("Prometheus snapshot directory is missing or symlinked")
    for root, directories, files in os.walk(path, followlinks=False):
        for name in directories + files:
            if (Path(root) / name).is_symlink():
                raise ValueError("Prometheus snapshot contains a symlink")
    blocks = [child for child in path.iterdir()
              if child.is_dir() and SNAPSHOT_NAME.fullmatch(child.name)
              and (child / "meta.json").is_file()]
    if not blocks:
        raise ValueError("Snapshot contains no TSDB blocks")
    return len(blocks)


def create_archive(snapshot_dir, snapshot_id, work_dir, max_bytes):
    count = _safe_snapshot_tree(snapshot_dir)
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    if work_dir.is_symlink():
        raise ValueError("Archive work directory must not be a symlink")
    free_bytes = shutil.disk_usage(work_dir).free
    source_bytes = sum(
        (Path(root) / name).stat().st_size
        for root, _directories, files in os.walk(snapshot_dir, followlinks=False)
        for name in files
    )
    reserve_bytes = 16 * 1024 * 1024
    if source_bytes + reserve_bytes > free_bytes:
        raise OSError("Insufficient temporary disk space for a safe archive build")
    archive = work_dir / f".{snapshot_id}.tar.gz.partial"
    if archive.exists() or archive.is_symlink():
        raise FileExistsError("Snapshot archive work file already exists")
    try:
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(snapshot_dir, arcname=snapshot_id, recursive=True)
        size = archive.stat().st_size
        if size > max_bytes:
            raise ValueError("Snapshot archive exceeds configured size limit")
        if size > free_bytes:
            raise OSError("Insufficient temporary disk space")
        digest = _sha256(archive)
        final = work_dir / f"prometheus-{snapshot_id}.tar.gz"
        if final.exists() or final.is_symlink():
            raise FileExistsError("Snapshot archive already exists in work directory")
        os.rename(archive, final)
        return final, digest, count
    finally:
        if archive.exists():
            archive.unlink()


def create_snapshot(api_url, snapshot_root, work_dir, upload_url, token, max_bytes):
    request = Request(api_url, data=b"", method="POST")
    with urlopen(request, timeout=120) as response:
        payload = json.loads(response.read(1024 * 1024))
    if payload.get("status") != "success" or not isinstance(payload.get("data"), dict):
        raise RuntimeError("Prometheus snapshot API did not return success")
    snapshot_id = payload["data"].get("name", "")
    if not isinstance(snapshot_id, str) or not SNAPSHOT_NAME.fullmatch(snapshot_id):
        raise ValueError("Prometheus returned an invalid snapshot name")
    snapshot_dir = Path(snapshot_root) / snapshot_id
    archive = None
    try:
        archive, digest, block_count = create_archive(snapshot_dir, snapshot_id, work_dir, max_bytes)
        return upload_archive(upload_url, token, archive, digest, snapshot_id, block_count)
    finally:
        if archive is not None:
            archive.unlink(missing_ok=True)
        snapshot_root_path = Path(snapshot_root)
        if (snapshot_dir.parent == snapshot_root_path and not snapshot_root_path.is_symlink()
                and not snapshot_dir.is_symlink() and snapshot_dir.is_dir()):
            shutil.rmtree(snapshot_dir)


def upload_archive(url, token, archive, digest, snapshot_id, block_count):
    target = urlsplit(url)
    if target.scheme not in ("http", "https") or not target.hostname or target.username or target.password:
        raise ValueError("Backup receiver URL must be an HTTP(S) URL without user information")
    path = target.path or "/"
    if target.query or target.fragment:
        raise ValueError("Backup receiver URL must not contain a query or fragment")
    cls = http.client.HTTPSConnection if target.scheme == "https" else http.client.HTTPConnection
    connection = cls(target.hostname, target.port, timeout=300)
    try:
        connection.putrequest("POST", path)
        connection.putheader("Authorization", f"Bearer {token}")
        connection.putheader("Content-Type", "application/gzip")
        connection.putheader("Content-Length", str(Path(archive).stat().st_size))
        connection.putheader("X-Archive-SHA256", digest)
        connection.putheader("X-Expected-Blocks", str(block_count))
        connection.putheader("X-Snapshot-ID", snapshot_id)
        connection.endheaders()
        with open(archive, "rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                connection.send(chunk)
        response = connection.getresponse()
        payload = json.loads(response.read(64 * 1024))
        if response.status not in (200, 201) or payload.get("status") not in ("verified", "already-verified"):
            raise RuntimeError(f"Backup receiver rejected archive with HTTP {response.status}")
        return payload
    finally:
        connection.close()


def backup_loop(stop, api_url, snapshot_root, work_dir, upload_url, token, interval, max_bytes, retry_interval=900):
    # Create and verify the first backup after readiness, then use the daily cadence.
    while not stop.is_set():
        delay = interval
        try:
            result = create_snapshot(api_url, snapshot_root, work_dir, upload_url, token, max_bytes)
            print(json.dumps({
                "event": "prometheus_backup_verified",
                "archive": result.get("archive"),
                "sha256": result.get("sha256"),
                "blocks": result.get("blocks"),
                "restore_test": result.get("restore_test"),
            }, sort_keys=True), flush=True)
        except Exception as exc:
            # No credentials or authorization headers are included in this log.
            print(json.dumps({
                "event": "prometheus_backup_failed",
                "error_type": type(exc).__name__,
                "message": str(exc)[:200],
            }, sort_keys=True), file=sys.stderr, flush=True)
            delay = retry_interval
        if stop.wait(delay):
            return


def wait_ready(process, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Prometheus exited before readiness")
        try:
            with urlopen("http://127.0.0.1:9091/-/ready", timeout=2) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(1)
    raise TimeoutError("Prometheus did not become ready")


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prometheus", default="/bin/prometheus")
    parser.add_argument("--config", default="/etc/prometheus/prometheus.yml")
    parser.add_argument("--storage", default="/prometheus")
    parser.add_argument("prometheus_args", nargs="*")
    args = parser.parse_args()

    token = os.environ.get("PROMETHEUS_BACKUP_TOKEN", "")
    upload_url = os.environ.get("PROMETHEUS_BACKUP_URL", "")
    if len(token) < 32 or not upload_url:
        raise SystemExit("Snapshot mode requires PROMETHEUS_BACKUP_TOKEN and PROMETHEUS_BACKUP_URL")
    interval = int(os.environ.get("PROMETHEUS_BACKUP_INTERVAL_SECONDS", "86400"))
    if interval < 3600:
        raise SystemExit("PROMETHEUS_BACKUP_INTERVAL_SECONDS must be at least 3600")
    retry_interval = int(os.environ.get("PROMETHEUS_BACKUP_RETRY_SECONDS", "900"))
    if retry_interval < 60:
        raise SystemExit("PROMETHEUS_BACKUP_RETRY_SECONDS must be at least 60")
    max_bytes = int(os.environ.get("PROMETHEUS_BACKUP_MAX_BYTES", str(512 * 1024 * 1024)))
    if max_bytes < 1024 * 1024:
        raise SystemExit("PROMETHEUS_BACKUP_MAX_BYTES is too small")

    child_args = [
        args.prometheus,
        f"--config.file={args.config}",
        f"--storage.tsdb.path={args.storage}",
        "--web.enable-admin-api",
        "--web.listen-address=127.0.0.1:9091",
        *args.prometheus_args,
    ]
    process = subprocess.Popen(child_args)
    stop = threading.Event()
    proxy = None
    try:
        wait_ready(process)
        handler = make_proxy_handler(token, 9091)
        proxy = ThreadingHTTPServer(("0.0.0.0", 9090), handler)
        proxy_thread = threading.Thread(target=proxy.serve_forever, daemon=True)
        proxy_thread.start()
        print("Prometheus private proxy ready on :9090; Admin API remains loopback-only", flush=True)
        api_url = "http://127.0.0.1:9091/api/v1/admin/tsdb/snapshot?skip_head=false"
        thread = threading.Thread(
            target=backup_loop,
            args=(stop, api_url, Path(args.storage) / "snapshots",
                  os.environ.get("PROMETHEUS_BACKUP_WORK_DIR", "/tmp/prometheus-backup-work"),
                  upload_url, token, interval, max_bytes, retry_interval),
            daemon=True,
        )
        thread.start()
        return process.wait()
    finally:
        stop.set()
        if proxy is not None:
            proxy.shutdown()
            proxy.server_close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    sys.exit(main())
