#!/usr/bin/env python3
"""Read-only post-restart verification of an already persisted synthetic backup."""
import hashlib
import json
import os
from pathlib import Path
from validate_snapshot_archive import validate
from validate_restored_tsdb import validate as validate_restored
from backup_receiver import _extract_validated
from tempfile import TemporaryDirectory

def main():
    root = Path(os.environ.get("PROMETHEUS_BACKUP_DIR", "/backup/prometheus-snapshots"))
    metadata = json.loads((root / "last-verified.json").read_text(encoding="utf-8"))
    name = metadata["archive"]
    if "/" in name or name in (".", "..") or not name.endswith(".tar.gz"):
        raise ValueError("Unsafe persisted archive name")
    archive = root / name
    if archive.is_symlink() or not archive.is_file():
        raise ValueError("Persisted archive missing or symlinked")
    expected_sha = metadata["sha256"]
    blocks = metadata["blocks"]
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if digest != expected_sha:
        raise ValueError("Persisted archive digest mismatch")
    validate(archive, expected_sha, blocks)
    with TemporaryDirectory(prefix="persisted-backup-check-") as restored:
        _extract_validated(archive, restored)
        validate_restored(restored, "/bin/promtool", blocks, timeout=180)
    print("PERSISTED_BACKUP_AFTER_RESTART_PASS sha256=" + digest + " blocks=" + str(blocks), flush=True)

if __name__ == "__main__":
    main()
