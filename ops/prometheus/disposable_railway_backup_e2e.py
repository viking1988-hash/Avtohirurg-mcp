#!/usr/bin/env python3
"""One-shot synthetic Railway snapshot -> archive -> private receiver E2E."""
import json, os, re, sys, time, urllib.request
from pathlib import Path
from backup_once import run
BASE = "http://127.0.0.1:9090"
ROOT = Path("/prometheus/snapshots")
OUT = Path("/tmp/prometheus-backup-e2e")
def main():
    token = os.environ.get("PROMETHEUS_BACKUP_TOKEN", "")
    receiver = os.environ.get("PROMETHEUS_BACKUP_URL", "")
    if len(token) < 32 or not receiver.startswith("http://"):
        raise RuntimeError("Missing isolated receiver configuration")
    for _ in range(60):
        try:
            with urllib.request.urlopen(BASE + "/-/ready", timeout=2): break
        except Exception: time.sleep(1)
    else: raise RuntimeError("Prometheus did not become ready")
    req = urllib.request.Request(BASE + "/api/v1/admin/tsdb/snapshot", data=b"", method="POST")
    with urllib.request.urlopen(req, timeout=45) as response: payload = json.load(response)
    if payload.get("status") != "success": raise RuntimeError("Snapshot API did not return success")
    name = payload.get("data", {}).get("name", "")
    if not re.fullmatch(r"[0-9A-Za-z_-]{1,80}", name): raise RuntimeError("Unsafe snapshot name")
    snapshot = ROOT / name
    if not snapshot.is_dir() or snapshot.is_symlink(): raise RuntimeError("Snapshot directory missing")
    OUT.mkdir(parents=True, exist_ok=True)
    result = run(snapshot, ROOT, OUT / (name + ".tar.gz"), receiver, token)
    print("RAILWAY_BACKUP_E2E_VERIFIED " + json.dumps(result, sort_keys=True), flush=True)
    return 0
if __name__ == "__main__":
    try: sys.exit(main())
    except Exception as exc:
        print("RAILWAY_BACKUP_E2E_FAILED " + type(exc).__name__ + ": " + str(exc), file=sys.stderr, flush=True)
        sys.exit(1)
