"""Disposable-only durable HTTP sink. Never connect to real providers."""
import json
import os
import pathlib
import sqlite3
from http.server import ThreadingHTTPServer

def main():
    if os.environ.get("JARVIS_PR47_DISPOSABLE_ONLY") != "YES":
        raise SystemExit("disposable-only guard missing")
    token = os.environ.get("JARVIS_RECEIVER_E2E_TOKEN", "")
    if len(token) < 24:
        raise SystemExit("explicit synthetic token required")
    database = pathlib.Path(os.environ.get("JARVIS_RECEIVER_DB", "/data/receiver.sqlite3"))
    if not database.is_absolute() or not database.parent.is_dir():
        raise SystemExit("persistent database directory missing")
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE IF NOT EXISTS inbox (principal TEXT NOT NULL, idem_key TEXT NOT NULL, fingerprint TEXT NOT NULL, receipt TEXT NOT NULL, PRIMARY KEY (principal, idem_key))")
        db.execute("CREATE TABLE IF NOT EXISTS effects (id INTEGER PRIMARY KEY, principal TEXT NOT NULL, idem_key TEXT NOT NULL)")
    port = int(os.environ.get("PORT", "8080"))
    if not 1 <= port <= 65535:
        raise SystemExit("invalid port")
    from isolated_receiver_http import make_handler
    # Private Railway networking only; do not attach a public domain.
    ThreadingHTTPServer(("0.0.0.0", port), make_handler(str(database), token)).serve_forever()

if __name__ == "__main__":
    main()
