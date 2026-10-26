#!/usr/bin/env python3
"""Package an already-completed, isolated Prometheus snapshot.

Never reads the active TSDB: input must be a directory under a snapshots root.
Fails closed on symlinks, unexpected files, missing blocks or size limits.
"""
import argparse
import hashlib
import os
from pathlib import Path
import re
import tarfile

ULID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")

def package(snapshot, snapshots_root, output, max_bytes=512*1024*1024):
    snapshot = Path(snapshot).resolve(strict=True)
    root = Path(snapshots_root).resolve(strict=True)
    output = Path(output)
    if snapshot.parent != root or not snapshot.is_dir() or snapshot.is_symlink():
        raise ValueError("Source must be a direct child of the snapshots root")
    if not re.fullmatch(r"[0-9A-Za-z_-]{1,80}", snapshot.name):
        raise ValueError("Unsafe snapshot name")
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    if output.resolve(strict=False).is_relative_to(root):
        raise ValueError("Output cannot be inside snapshots root")
    files = []
    blocks = set()
    total = 0
    for base, dirs, names in os.walk(snapshot, followlinks=False):
        for entry in dirs + names:
            item = Path(base) / entry
            if item.is_symlink():
                raise ValueError("Snapshot contains symlink")
            if not (item.is_dir() or item.is_file()):
                raise ValueError("Snapshot contains unsupported file")
            relative = item.relative_to(snapshot)
            if relative.parts and ULID.fullmatch(relative.parts[0]):
                blocks.add(relative.parts[0])
            elif relative.parts:
                raise ValueError("Unexpected snapshot member")
            if item.is_file():
                total += item.stat().st_size
                if total > max_bytes:
                    raise ValueError("Snapshot exceeds uncompressed size limit")
                files.append(item)
    if not blocks:
        raise ValueError("Snapshot has no TSDB blocks")
    for block in blocks:
        for part in ("meta.json", "index"):
            if not (snapshot / block / part).is_file():
                raise ValueError("Incomplete snapshot block")
        if not (snapshot / block / "chunks").is_dir():
            raise ValueError("Missing chunks directory")
    temporary = output.with_name(output.name + ".partial")
    if temporary.exists() or temporary.is_symlink():
        raise FileExistsError(temporary)
    try:
        with tarfile.open(temporary, "x:gz", dereference=False) as archive:
            for file in sorted(files):
                archive.add(file, arcname=str(Path("snapshot") / file.relative_to(snapshot)), recursive=False)
        digest = hashlib.sha256(temporary.read_bytes()).hexdigest()
        if temporary.stat().st_size > max_bytes:
            raise ValueError("Archive exceeds compressed size limit")
        if output.exists():
            raise FileExistsError(output)
        os.rename(temporary, output)
        return digest, len(blocks)
    finally:
        if temporary.exists():
            temporary.unlink()

def main():
    p = argparse.ArgumentParser()
    p.add_argument("snapshot")
    p.add_argument("snapshots_root")
    p.add_argument("output")
    args = p.parse_args()
    digest, blocks = package(args.snapshot, args.snapshots_root, args.output)
    print(f"SNAPSHOT PACKAGE PASS sha256={digest} blocks={blocks}")

if __name__ == "__main__":
    main()
