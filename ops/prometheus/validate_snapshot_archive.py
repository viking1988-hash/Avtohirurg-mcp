#!/usr/bin/env python3
"""Read-only validation of a Prometheus TSDB snapshot tar.gz. Python 3.9+."""
import argparse
import hashlib
import gzip
import json
import re
import sys
import tarfile
from pathlib import PurePosixPath

MAX_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 10000
MAX_MEMBER_NAME_BYTES = 4096
MAX_TAR_METADATA_OVERHEAD = 4 * 1024 * 1024

ULID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")

def validate(archive, expected_sha256, expected_blocks, max_uncompressed_bytes=MAX_UNCOMPRESSED_BYTES):
    if not isinstance(max_uncompressed_bytes, int) or isinstance(max_uncompressed_bytes, bool) or max_uncompressed_bytes < 1:
        raise ValueError("Invalid uncompressed archive size limit")
    if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256):
        raise ValueError("Invalid expected SHA-256")
    if not isinstance(expected_blocks, int) or isinstance(expected_blocks, bool) or expected_blocks < 1:
        raise ValueError("Invalid expected block count")
    digest = hashlib.sha256()
    with open(archive, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual.lower() != expected_sha256.lower():
        raise ValueError(f"SHA-256 mismatch: {actual}")

    # Preflight the decompressed tar stream before tarfile parses PAX headers.
    # This bounds metadata expansion even when a PAX header is never yielded
    # as a regular tar member.
    expanded = 0
    with gzip.open(archive, "rb") as decompressed:
        while True:
            chunk = decompressed.read(1024 * 1024)
            if not chunk:
                break
            expanded += len(chunk)
            if expanded > max_uncompressed_bytes + MAX_TAR_METADATA_OVERHEAD:
                raise ValueError("Decompressed tar stream size limit exceeded")

    blocks = {}
    root = None
    seen_names = set()
    total_uncompressed = 0
    member_count = 0
    # Streaming mode avoids tarfile retaining TarInfo copies of global PAX keys\n    # for every member (quadratic metadata memory amplification).\n    with tarfile.open(archive, "r|gz") as tar:
        for member in tar:
            member_count += 1
            if member_count > MAX_ARCHIVE_MEMBERS:
                raise ValueError("Archive member count limit exceeded")
            if len(member.name.encode("utf-8")) > MAX_MEMBER_NAME_BYTES:
                raise ValueError("Archive member name limit exceeded")
            if member.size < 0:
                raise ValueError("Invalid archive member size")
            if member.isfile():
                total_uncompressed += member.size
                if total_uncompressed > max_uncompressed_bytes:
                    raise ValueError("Uncompressed archive size limit exceeded")
            name = member.name.removeprefix("./")
            if name in seen_names:
                raise ValueError(f"Duplicate archive member: {name}")
            seen_names.add(name)
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or not path.parts:
                raise ValueError(f"Unsafe archive path: {member.name}")
            if not (member.isfile() or member.isdir()):
                raise ValueError(f"Unexpected archive member type: {member.name}")
            if root is None:
                root = path.parts[0]
            if path.parts[0] != root:
                raise ValueError("Archive has multiple top-level roots")
            if len(path.parts) < 2:
                continue
            block = path.parts[1]
            if not ULID.fullmatch(block):
                raise ValueError(f"Unexpected TSDB block name: {block}")
            present = blocks.setdefault(block, set())
            if len(path.parts) == 3 and member.isfile() and path.parts[2] in ("meta.json", "index"):
                present.add(path.parts[2])
                if path.parts[2] == "meta.json":
                    extracted = tar.extractfile(member)
                    if extracted is None or member.size > 1024 * 1024:
                        raise ValueError(f"Invalid meta.json: {block}")
                    try:
                        meta = json.load(extracted)
                    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                        raise ValueError(f"Invalid meta.json: {block}") from exc
                    if not isinstance(meta, dict) or meta.get("ulid") != block:
                        raise ValueError(f"Block ULID mismatch: {block}")
                    if not isinstance(meta.get("version"), int) or isinstance(meta.get("version"), bool):
                        raise ValueError(f"Invalid block metadata version: {block}")
            if len(path.parts) >= 3 and path.parts[2] == "chunks":
                present.add("chunks")
    if len(blocks) != expected_blocks:
        raise ValueError(f"Expected {expected_blocks} blocks, found {len(blocks)}")
    for block, present in blocks.items():
        if not {"meta.json", "index", "chunks"} <= present:
            raise ValueError(f"Missing block files in {block}: {present}")
    return actual, len(blocks)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive")
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--blocks", type=int, default=11)
    args = parser.parse_args()
    try:
        digest, count = validate(args.archive, args.sha256, args.blocks)
    except (OSError, ValueError, tarfile.TarError, json.JSONDecodeError) as exc:
        print(f"BACKUP VALIDATION FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"BACKUP ARCHIVE VALIDATION PASS: sha256={digest} blocks={count}")
    print("NOTE: archive structure validated; promtool per-block analysis is a separate check.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
