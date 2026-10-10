"""Sandbox-only CI acceptance runner.

Requires an ephemeral PostgreSQL service. Never use a Railway or production DSN.
Executes both offline and PostgreSQL suites; skips are treated as failure.
"""
import os
import sys
import unittest


PATTERNS = (
    "test_jarvis_return_queue_offline_e2e.py",
    "test_jarvis_return_queue_adversarial.py",
    "test_jarvis_return_queue_postgres.py",
    "test_jarvis_return_queue_postgres_sandbox.py",
    "test_jarvis_return_queue_postgres_race.py",
    "test_jarvis_return_queue_postgres_guards.py",
)


def main():
    url = os.environ.get("JARVIS_CI_DATABASE_URL", "")
    if not url or "127.0.0.1" not in url and "localhost" not in url:
        raise SystemExit("REFUSED: only local ephemeral CI PostgreSQL is allowed")
    if "/jarvis_return_queue_test" not in url or "jarvis_sandbox" not in url:
        raise SystemExit("REFUSED: incorrect test database or role")
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
