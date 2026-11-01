#!/usr/bin/env python3
"""Fail-closed backup ingestion: validate a completed snapshot then stage it.

This command deliberately DOES NOT create snapshots, enable admin API, or delete
backups. The caller must supply an already-consistent snapshot archive and its
out-of-band trusted SHA-256. Run under run_locked.py for single-writer safety.
"""
import argparse
from pathlib import Path
import sys

from validate_snapshot_archive import validate
from stage_verified_archive import stage

def ingest(source, destination, sha256, blocks):
    # No changes to destination until the archive is fully validated.
    validate(source, sha256, blocks)
    target = stage(source, destination, sha256)
    # Defense in depth: re-check destination content after atomic rename.
    try:
        validate(target, sha256, blocks)
    except Exception:
        # Preserve suspect archive for forensic review; never delete it silently.
        raise
    return target

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("snapshot_archive")
    p.add_argument("backup_dir")
    p.add_argument("--sha256", required=True)
    p.add_argument("--blocks", type=int, required=True)
    args = p.parse_args()
    try:
        target = ingest(Path(args.snapshot_archive), Path(args.backup_dir), args.sha256, args.blocks)
    except (OSError, ValueError, RuntimeError, EOFError) as exc:
        print(f"BACKUP INGEST FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"BACKUP INGEST PASS: {target}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
