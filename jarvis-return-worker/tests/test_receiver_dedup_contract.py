"""Synthetic SQLite receiver-side idempotency contract for PR47.

This tests a durable receiver design, NOT Telegram or the production webhook.
"""
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier


def receive(db_path: Path, key: str, payload: str) -> tuple[str, bool]:
    """One atomic insert is the receiver's deduplication linearization point."""
    with sqlite3.connect(db_path, timeout=10) as db:
        db.execute("PRAGMA busy_timeout=10000")
        cursor = db.execute(
            "INSERT OR IGNORE INTO accepted(idempotency_key, payload) VALUES (?, ?)",
            (key, payload),
        )
        row = db.execute(
            "SELECT payload FROM accepted WHERE idempotency_key = ?", (key,)
        ).fetchone()
        if row[0] != payload:
            raise ValueError("idempotency key reused with different payload")
        return key, cursor.rowcount == 1


def test_receiver_deduplicates_concurrent_workers_and_restart(tmp_path):
    db_path = tmp_path / "receiver.sqlite"
    with sqlite3.connect(db_path) as db:
        db.execute(
            "CREATE TABLE accepted (idempotency_key TEXT PRIMARY KEY, payload TEXT NOT NULL)"
        )
    barrier = Barrier(8, timeout=10)

    def worker(_):
        barrier.wait()
        return receive(db_path, "task-1:fingerprint-1", "approved-payload")

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(worker, range(8)))
    assert sum(created for _, created in results) == 1

    # A new connection models process restart; duplicate acceptance remains blocked.
    assert receive(db_path, "task-1:fingerprint-1", "approved-payload") == (
        "task-1:fingerprint-1", False
    )
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM accepted").fetchone()[0] == 1


def test_receiver_rejects_idempotency_key_payload_mismatch(tmp_path):
    db_path = tmp_path / "receiver.sqlite"
    with sqlite3.connect(db_path) as db:
        db.execute(
            "CREATE TABLE accepted (idempotency_key TEXT PRIMARY KEY, payload TEXT NOT NULL)"
        )
    assert receive(db_path, "same-key", "first") == ("same-key", True)
    import pytest

    with pytest.raises(ValueError, match="different payload"):
        receive(db_path, "same-key", "changed")
    with sqlite3.connect(db_path) as db:
        assert db.execute(
            "SELECT payload FROM accepted WHERE idempotency_key='same-key'"
        ).fetchone()[0] == "first"
