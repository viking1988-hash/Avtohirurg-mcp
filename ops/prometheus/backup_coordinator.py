#!/usr/bin/env python3
"""Opt-in one-shot snapshot -> verified upload coordinator. No daemon or cleanup.

DISPOSABLE/ISOLATED TEST ONLY until Railway service topology is accepted.
Requires controller and completed snapshots to share a filesystem.
"""
import argparse
import http.client
import json
import os
from pathlib import Path
import re
import sys

from backup_cycle import execute

SNAPSHOT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


def request_snapshot(token):
    if len(token) < 32:
        raise ValueError("Controller token must contain at least 32 characters")
    conn = http.client.HTTPConnection("127.0.0.1", 19092, timeout=45)
    try:
        conn.request("POST", "/v1/snapshot", headers={"Authorization": "Bearer " + token})
        response = conn.getresponse()
        data = response.read(65537)
        if response.status != 200 or len(data) > 65536:
            raise RuntimeError("Snapshot controller refused or returned oversized response")
        result = json.loads(data)
        if result.get("status") != "success":
            raise RuntimeError("Snapshot controller did not confirm success")
        name = result.get("data", {}).get("name")
        if not isinstance(name, str) or not SNAPSHOT_NAME.fullmatch(name):
            raise ValueError("Unsafe snapshot name from controller")
        return name
    finally:
        conn.close()


def coordinate(snapshots_root, output_dir, url, backup_token, controller_token, lock_path):
    root = Path(snapshots_root)
    if not root.is_dir() or root.is_symlink():
        raise ValueError("Snapshot root must be a real directory")
    name = request_snapshot(controller_token)
    snapshot = root / name
    if not snapshot.is_dir() or snapshot.is_symlink():
        raise RuntimeError("Completed snapshot not present in expected root")
    return execute(snapshot, root, output_dir, url, backup_token, lock_path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshots_root")
    parser.add_argument("output_dir")
    parser.add_argument("--url", required=True)
    parser.add_argument("--lock", required=True)
    args = parser.parse_args()
    if os.environ.get("PROMETHEUS_BACKUP_COORDINATOR_ENABLED") != "true":
        print("COORDINATOR DISABLED: explicit opt-in required", file=sys.stderr)
        return 2
    try:
        result = coordinate(args.snapshots_root, args.output_dir, args.url,
                            os.environ.get("PROMETHEUS_BACKUP_TOKEN", ""),
                            os.environ.get("PROMETHEUS_SNAPSHOT_CONTROLLER_TOKEN", ""),
                            args.lock)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print("COORDINATOR FAILED: " + str(exc), file=sys.stderr)
        return 1
    print("COORDINATOR VERIFIED: " + json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
