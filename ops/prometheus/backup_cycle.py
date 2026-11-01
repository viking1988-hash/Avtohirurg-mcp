#!/usr/bin/env python3
"""Opt-in one-cycle scheduler gate for completed Prometheus snapshots.

No admin API, no automatic deletion and no daemon. External scheduler can
invoke this command; concurrency is prevented with an advisory file lock.
"""
import argparse
import fcntl
import os
from pathlib import Path
import sys
from datetime import datetime, timezone

from backup_once import run

def execute(snapshot, snapshots_root, output_dir, url, token, lock_path):
    output_dir = Path(output_dir)
    if not output_dir.is_dir() or output_dir.is_symlink():
        raise ValueError("Output directory must exist and not be symlinked")
    lock_path = Path(lock_path)
    if lock_path.is_symlink():
        raise ValueError("Lock file must not be a symlink")
    if not lock_path.parent.is_dir():
        raise ValueError("Lock directory must exist")
    # O_NOFOLLOW prevents symlink substitution between check and open.
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(lock_path, flags, 0o600)
    with os.fdopen(fd, "a+b") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another backup cycle is active") from exc
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        archive = output_dir / f"snapshot-{stamp}-{Path(snapshot).name}.tar.gz"
        return run(snapshot, snapshots_root, archive, url, token)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot")
    parser.add_argument("snapshots_root")
    parser.add_argument("output_dir")
    parser.add_argument("--url", required=True)
    parser.add_argument("--lock", required=True)
    args = parser.parse_args()
    if os.environ.get("PROMETHEUS_BACKUP_SCHEDULER_ENABLED") != "true":
        print("BACKUP SCHEDULER DISABLED: opt-in flag is not true")
        return 2
    try:
        result = execute(args.snapshot, args.snapshots_root, args.output_dir, args.url,
                         os.environ.get("PROMETHEUS_BACKUP_TOKEN", ""), args.lock)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"BACKUP CYCLE FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"BACKUP CYCLE VERIFIED: {result['archive']}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
