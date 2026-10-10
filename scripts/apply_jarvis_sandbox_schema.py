"""Explicit, isolated Jarvis staging SQL migration runner.

Never runs on import. Requires opt-in and exact sandbox database identity.
Does not use CRM or production databases.
Usage (from a trusted sandbox runner):
  JARVIS_SANDBOX_DATABASE_URL=postgresql://... \
  JARVIS_SANDBOX_DATABASE_NAME=jarvis_return_queue_test \
  python scripts/apply_jarvis_sandbox_schema.py --apply

Requires psycopg (pip install 'psycopg[binary]').
"""
import argparse
import os
from pathlib import Path
from urllib.parse import urlsplit, unquote

EXPECTED_DB = "jarvis_return_queue_test"
SQL_PATH = Path(__file__).resolve().parents[1] / "docs/sql/jarvis-return-queue-staging-proposal.sql"

def validate_target(url: str, declared_name: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in ("postgresql", "postgres"):
        raise ValueError("PostgreSQL URL required")
    database = unquote(parsed.path.lstrip("/"))
    if database != EXPECTED_DB or declared_name != EXPECTED_DB:
        raise ValueError("Refusing migration: database name does not match Jarvis sandbox")
    if not parsed.hostname or not parsed.username or not parsed.password:
        raise ValueError("Database host, user and password required")
    if parsed.hostname in ("localhost", "127.0.0.1"):
        raise ValueError("Refusing ambiguous local database target")

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="explicitly execute schema")
    args = parser.parse_args()
    url = os.environ.get("JARVIS_SANDBOX_DATABASE_URL", "")
    name = os.environ.get("JARVIS_SANDBOX_DATABASE_NAME", "")
    validate_target(url, name)
    if not args.apply:
        print("Target validated; no changes applied (pass --apply to execute)")
        return
    import psycopg
    # Confirm server-side database identity; URL parsing alone is insufficient.
    with psycopg.connect(url, connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database()")
            if cur.fetchone()[0] != EXPECTED_DB:
                raise RuntimeError("Server database identity mismatch")
            # Proposal includes a parameterized UPDATE example; only apply DDL.
            sql = SQL_PATH.read_text(encoding="utf-8").split("-- Example single-winner claim")[0]
            cur.execute(sql)
    print("Jarvis sandbox schema applied successfully")

if __name__ == "__main__":
    main()
