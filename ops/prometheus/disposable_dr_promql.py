#!/usr/bin/env python3
"""Read-only disposable Prometheus restore smoke test. Never target production."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import urllib.request
import urllib.parse

from backup_receiver import _extract_validated
from validate_snapshot_archive import validate as validate_archive
from validate_restored_tsdb import validate as validate_restored

def main():
    root = Path(os.environ.get("PROMETHEUS_BACKUP_DIR", "/backup/prometheus-snapshots"))
    if not root.is_dir() or root.is_symlink():
        raise ValueError("Unsafe backup directory")
    status = root / "last-verified.json"
    if status.is_symlink() or not status.is_file():
        raise ValueError("Unsafe backup status file")
    meta = json.loads(status.read_text())
    name = meta["archive"]
    if Path(name).name != name or not name.endswith(".tar.gz"):
        raise ValueError("Unsafe archive name")
    archive = root / name
    if archive.is_symlink() or not archive.is_file():
        raise ValueError("Archive missing")
    with archive.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    if digest != meta["sha256"]:
        raise ValueError("SHA256 mismatch")
    validate_archive(archive, digest, meta["blocks"])
    with tempfile.TemporaryDirectory(prefix="prom-dr-sandbox-") as tmp:
        db = Path(tmp) / "tsdb"
        db.mkdir()
        _extract_validated(archive, db)
        validate_restored(db, "/bin/promtool", meta["blocks"], timeout=180)
        cmd = ["/bin/prometheus", "--storage.tsdb.path=" + str(db),
               "--web.listen-address=127.0.0.1:19090",
               "--storage.tsdb.retention.time=365d",
               "--config.file=/opt/prometheus-backup/prometheus-dr.yml"]
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            for _ in range(60):
                if proc.poll() is not None:
                    stderr = proc.communicate(timeout=5)[1].decode("utf-8", errors="replace")
                    raise RuntimeError("Prometheus exited during startup: " + stderr[-2500:])
                try:
                    with urllib.request.urlopen("http://127.0.0.1:19090/-/ready", timeout=2) as response:
                        if response.status == 200:
                            break
                except Exception:
                    time.sleep(1)
            else:
                raise RuntimeError("Prometheus readiness timeout")
            blocks = sorted(p for p in db.iterdir() if p.is_dir() and (p / "meta.json").is_file())
            block_meta = json.loads((blocks[0] / "meta.json").read_text())
            query_time = (block_meta["minTime"] + block_meta["maxTime"]) / 2000
            url = "http://127.0.0.1:19090/api/v1/query?" + urllib.parse.urlencode({"query": "up", "time": query_time})
            with urllib.request.urlopen(url, timeout=10) as response:
                result = json.load(response)
            if result.get("status") != "success" or not result.get("data", {}).get("result"):
                raise RuntimeError("PromQL returned no restored up series")
            print("DISPOSABLE_PROMETHEUS_DR_PROMQL_PASS sha256=" + digest +
                  " blocks=" + str(meta["blocks"]) + " query=up", flush=True)
        finally:
            proc.terminate()
            try:
                proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()

if __name__ == "__main__":
    main()
