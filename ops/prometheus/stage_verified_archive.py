#!/usr/bin/env python3
"""Stage a completed snapshot archive onto a backup volume, without touching live TSDB."""
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import sys
import tempfile

def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as src:
        for chunk in iter(lambda: src.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def stage(source, destination_dir, expected_sha256):
    source = Path(source)
    destination_dir = Path(destination_dir)
    if not source.is_file() or source.is_symlink():
        raise ValueError("Source must be a regular, non-symlink file")
    if source.suffix != ".gz" or not source.name.endswith(".tar.gz"):
        raise ValueError("Source must be a .tar.gz snapshot archive")
    if not destination_dir.is_dir() or destination_dir.is_symlink():
        raise ValueError("Destination must be an existing non-symlink directory")
    if len(expected_sha256) != 64 or any(c not in "0123456789abcdefABCDEF" for c in expected_sha256):
        raise ValueError("Invalid expected SHA-256")
    target = destination_dir / source.name
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"Refusing to overwrite existing backup: {target}")
    # Temporary file is on the destination volume; rename is atomic there.
    fd, temp_name = tempfile.mkstemp(prefix=".incoming-", suffix=".partial", dir=destination_dir)
    try:
        with os.fdopen(fd, "wb") as out, source.open("rb") as inp:
            shutil.copyfileobj(inp, out, length=1024 * 1024)
            out.flush()
            os.fsync(out.fileno())
        if sha256(temp_name).lower() != expected_sha256.lower():
            raise ValueError("Copied archive SHA-256 mismatch")
        if target.exists() or target.is_symlink():
            raise FileExistsError(f"Target appeared during transfer: {target}")
        # This script requires an exclusive single-writer lock provided by its caller.
        os.rename(temp_name, target)
        return target
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source")
    parser.add_argument("destination_dir")
    parser.add_argument("--sha256", required=True)
    args = parser.parse_args()
    try:
        path = stage(args.source, args.destination_dir, args.sha256)
    except (OSError, ValueError) as exc:
        print(f"BACKUP TRANSFER FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"BACKUP TRANSFER PASS: {path}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
