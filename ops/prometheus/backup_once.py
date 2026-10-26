#!/usr/bin/env python3
"""Single-shot, fail-closed snapshot packaging and upload.

Only operates on a completed snapshot directory. No admin API, no live TSDB
access, no background scheduler and no automatic deletion of source files.
"""
import argparse
import json
import os
from pathlib import Path
import sys

from package_completed_snapshot import package
from upload_verified_archive import upload

def run(snapshot, root, archive, url, token):
    snapshot = Path(snapshot)
    archive = Path(archive)
    if not snapshot.is_dir() or snapshot.is_symlink():
        raise ValueError("Snapshot must be a completed real directory")
    if archive.exists() or archive.is_symlink():
        raise FileExistsError("Refusing to overwrite archive")
    digest, blocks = package(snapshot, root, archive)
    # The archive stays on disk on failed upload to allow manual recovery.
    result = upload(archive, url, token, snapshot.name, blocks)
    if result.get("sha256") != digest or result.get("blocks") != blocks:
        raise RuntimeError("Receiver attestation does not match packaged snapshot")
    return result

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot")
    parser.add_argument("snapshots_root")
    parser.add_argument("archive")
    parser.add_argument("--url", required=True)
    args = parser.parse_args()
    try:
        result = run(args.snapshot, args.snapshots_root, args.archive,
                     args.url, os.environ.get("PROMETHEUS_BACKUP_TOKEN", ""))
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"BACKUP SINGLE SHOT FAILED: {exc}", file=sys.stderr)
        return 1
    print("BACKUP SINGLE SHOT VERIFIED:", json.dumps(result, sort_keys=True))
    return 0

if __name__ == "__main__":
    sys.exit(main())
