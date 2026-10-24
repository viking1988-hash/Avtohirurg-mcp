#!/usr/bin/env python3
"""Conservative backup retention planner. DRY RUN ONLY: never deletes files."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import re
import sys

PATTERN = re.compile(r"^prometheus-(\d{8}T\d{6}Z)-[A-Za-z0-9_-]+\.tar\.gz$")

def plan(directory, keep_latest=7, protected=()):
    directory = Path(directory)
    if not directory.is_dir() or directory.is_symlink():
        raise ValueError("Backup directory must exist and not be a symlink")
    if keep_latest < 1:
        raise ValueError("keep_latest must be at least 1")
    candidates = []
    for path in directory.iterdir():
        match = PATTERN.fullmatch(path.name)
        if not match or not path.is_file() or path.is_symlink():
            continue
        try:
            when = datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        candidates.append((when, path))
    candidates.sort(key=lambda x: (x[0], x[1].name), reverse=True)
    preserved = set(path for _, path in candidates[:keep_latest])
    preserved.update(directory / name for name in protected)
    return [path for _, path in candidates if path not in preserved]

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("backup_dir")
    p.add_argument("--keep-latest", type=int, default=7)
    p.add_argument("--protect", action="append", default=[])
    args = p.parse_args()
    try:
        items = plan(args.backup_dir, args.keep_latest, args.protect)
    except (OSError, ValueError) as exc:
        print(f"RETENTION PLAN FAILED: {exc}", file=sys.stderr)
        return 1
    for path in items:
        print(f"DRY_RUN_CANDIDATE: {path.name}")
    print(f"DRY_RUN_ONLY: {len(items)} candidates, 0 deleted")
    return 0

if __name__ == "__main__":
    sys.exit(main())
