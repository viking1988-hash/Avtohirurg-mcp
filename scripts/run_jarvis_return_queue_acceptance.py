"""Sandbox-only CI acceptance runner.

Requires an ephemeral PostgreSQL service. Never use a Railway or production DSN.
Executes both offline and PostgreSQL suites; skips are treated as failure.
"""
import os
import sys
import unittest
from pathlib import Path
from urllib.parse import urlsplit, unquote

# Direct script execution adds scripts/ to sys.path, not the repository root.
# Import queue modules from the checked-out repository under test.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


PATTERNS = (
    "test_jarvis_return_queue_offline_e2e.py",
    "test_jarvis_return_queue_adversarial.py",
    "test_jarvis_return_queue_postgres.py",
    "test_jarvis_return_queue_postgres_sandbox.py",
    "test_jarvis_return_queue_postgres_race.py",
    "test_jarvis_return_queue_postgres_guards.py",
)


def validate_ci_dsn(url):
    """Reject remote hosts and lookalike DSNs, without opening a connection."""
    try:
        parts = urlsplit(url)
        valid = (
            parts.scheme in ("postgresql", "postgres")
            and parts.hostname in ("127.0.0.1", "localhost", "::1")
            and unquote(parts.username or "") == "jarvis_sandbox"
            and unquote(parts.path) == "/jarvis_return_queue_test"
            and parts.port == 5432
            and not parts.query
            and not parts.fragment
        )
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise SystemExit("REFUSED: only exact local ephemeral CI PostgreSQL DSN is allowed")


def main():
    validate_ci_dsn(os.environ.get("JARVIS_CI_DATABASE_URL", ""))
    total = 0
    for pattern in PATTERNS:
        suite = unittest.defaultTestLoader.discover("tests", pattern=pattern)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        total += result.testsRun
        if not result.wasSuccessful() or result.skipped:
            raise SystemExit("FAILED: " + pattern)
    print("PASS: full isolated acceptance suite", total, "tests")


if __name__ == "__main__":
    main()
