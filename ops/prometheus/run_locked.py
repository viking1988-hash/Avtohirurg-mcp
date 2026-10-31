#!/usr/bin/env python3
"""Single-host staging backup lock and guarded execution (POSIX/Linux only)."""
import argparse
import fcntl
import os
from pathlib import Path
import subprocess
import sys

def run_locked(lock_path, command, timeout=1800):
    lock_path = Path(lock_path)
    if not lock_path.parent.is_dir() or lock_path.parent.is_symlink():
        raise ValueError("Lock parent directory must exist and not be symlinked")
    if lock_path.is_symlink():
        raise ValueError("Lock path must not be a symlink")
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another backup task holds the lock") from exc
        if not command:
            raise ValueError("Command is required")
        if timeout < 1:
            raise ValueError("Timeout must be positive")
        result = subprocess.run(command, check=False, timeout=timeout)
        return result.returncode
    finally:
        os.close(fd)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", required=True)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    try:
        return run_locked(args.lock, command, args.timeout)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"BACKUP TASK FAILED: {exc}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
