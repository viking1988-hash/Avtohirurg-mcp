#!/usr/bin/env python3
"""Upload an already validated completed snapshot archive to the private receiver.

Never contacts the Prometheus admin API or touches live TSDB. A successful HTTP
response is required; no local archive deletion is performed.
"""
import argparse
import hashlib
import http.client
import json
from pathlib import Path
import os
from urllib.parse import urlsplit

from validate_snapshot_archive import validate

MAX_BYTES = 512 * 1024 * 1024

def upload(archive, url, token, snapshot_id, expected_blocks):
    archive = Path(archive)
    if archive.is_symlink() or not archive.is_file():
        raise ValueError("Archive must be a regular file")
    if not isinstance(token, str) or len(token) < 32:
        raise ValueError("Backup token is missing or too short")
    import re
    if not re.fullmatch(r"[0-9A-Za-z_-]{1,80}", snapshot_id):
        raise ValueError("Invalid snapshot ID")
    parsed = urlsplit(url)
    if parsed.scheme != "http" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path != "/v1/upload":
        raise ValueError("Expected private HTTP receiver URL ending in /v1/upload")
    if parsed.hostname in ("0.0.0.0", "localhost"):
        raise ValueError("Ambiguous receiver hostname")
    size = archive.stat().st_size
    if size < 1 or size > MAX_BYTES:
        raise ValueError("Archive size outside limit")
    digest = hashlib.sha256()
    with archive.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b""):
            digest.update(chunk)
    sha = digest.hexdigest()
    validate(archive, sha, expected_blocks)
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=120)
    try:
        conn.putrequest("POST", "/v1/upload")
        for name, value in {
            "Authorization": "Bearer " + token,
            "Content-Type": "application/gzip",
            "Content-Length": str(size),
            "X-Archive-SHA256": sha,
            "X-Expected-Blocks": str(expected_blocks),
            "X-Snapshot-ID": snapshot_id,
        }.items():
            conn.putheader(name, value)
        conn.endheaders()
        with archive.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024*1024), b""):
                conn.send(chunk)
        response = conn.getresponse()
        body = response.read(65537)
        if len(body) > 65536:
            raise ValueError("Receiver response too large")
        if response.status not in (200, 201):
            raise RuntimeError(f"Receiver rejected upload (HTTP {response.status})")
        result = json.loads(body)
        if not isinstance(result, dict) or result.get("status") != "verified" or result.get("sha256") != sha or result.get("blocks") != expected_blocks or result.get("restore_test") != "promtool-tsdb-analyze-pass":
            raise ValueError("Receiver did not confirm matching archive and restore verification")
        return result
    finally:
        conn.close()

def main():
    p = argparse.ArgumentParser()
    p.add_argument("archive")
    p.add_argument("--url", required=True)
    p.add_argument("--snapshot-id", required=True)
    p.add_argument("--blocks", type=int, required=True)
    args = p.parse_args()
    result = upload(args.archive, args.url, os.environ.get("PROMETHEUS_BACKUP_TOKEN", ""), args.snapshot_id, args.blocks)
    print("BACKUP UPLOAD RESPONSE:", json.dumps(result, sort_keys=True))

if __name__ == "__main__":
    main()
