#!/usr/bin/env python3
"""Validate every TSDB block of an already extracted snapshot using promtool."""
import argparse
from pathlib import Path
import subprocess
import sys

def validate(db, promtool, expected_blocks=11, timeout=120):
    db = Path(db)
    if not db.is_dir() or db.is_symlink():
        raise ValueError("Restored TSDB directory is missing or symlinked")
    blocks = sorted(p for p in db.iterdir() if p.is_dir() and (p / "meta.json").is_file())
    if len(blocks) != expected_blocks:
        raise ValueError(f"Expected {expected_blocks} blocks, found {len(blocks)}")
    for block in blocks:
        if not (block / "index").is_file() or not (block / "chunks").is_dir():
            raise ValueError(f"Block structure incomplete: {block.name}")
        try:
            result = subprocess.run(
                [str(promtool), "tsdb", "analyze", str(db), block.name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"promtool timeout on {block.name}") from exc
        if result.returncode != 0:
            raise RuntimeError(f"promtool failed for {block.name}: {result.stderr[-500:]}")
        print(f"PASS: {block.name}")
    return len(blocks)

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("restored_tsdb")
    p.add_argument("--promtool", default="/bin/promtool")
    p.add_argument("--blocks", type=int, default=11)
    p.add_argument("--timeout", type=int, default=120)
    args = p.parse_args()
    try:
        count = validate(args.restored_tsdb, args.promtool, args.blocks, args.timeout)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"RESTORE VALIDATION FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"RESTORED TSDB VALIDATION PASS: {count} blocks")
    return 0

if __name__ == "__main__":
    sys.exit(main())
