"""Negative HTTP security tests for the isolated PR47 receiver (no external sends)."""
import http.client
import importlib.util
import json
import sqlite3
import threading
from pathlib import Path


def _load_receiver():
    path = Path(__file__).resolve().parents[1] / "isolated_receiver_http.py"
    spec = importlib.util.spec_from_file_location("pr47_isolated_receiver_negative", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_http_auth_conflict_receipt_restart_and_no_extra_effect(tmp_path):
    path = tmp_path / "inbox.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE inbox (principal TEXT NOT NULL, idem_key TEXT NOT NULL, fingerprint TEXT NOT NULL, receipt TEXT NOT NULL, PRIMARY KEY (principal, idem_key))")
        db.execute("CREATE TABLE effects (id INTEGER PRIMARY KEY, principal TEXT NOT NULL, idem_key TEXT NOT NULL)")
    receiver = _load_receiver()
    token = "synthetic-private-test-token-only"

    def start():
        server = receiver.serve_local(str(path), token)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread

    def request(server, method, route, *, body=None, auth=True, key="pr47-durable-negative"):
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
        headers = {"Idempotency-Key": key}
        if auth:
            headers["Authorization"] = "Bearer " + token
        data = json.dumps(body).encode() if body is not None else None
        try:
            conn.request(method, route, body=data, headers=headers)
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()

    server, thread = start()
    try:
        assert request(server, "POST", "/messages", body={"value": 1}, auth=False)[0] == 401
        assert request(server, "POST", "/messages", body={"value": 1}, key="")[0] == 400
        assert request(server, "POST", "/messages", body={"value": 1}, key="x" * 129)[0] == 400
        assert request(server, "POST", "/messages", body={"value": 1}, key="bad/key")[0] == 400
        assert request(server, "POST", "/messages", body={"value": 1}, key="bad key")[0] == 400
        status, receipt = request(server, "POST", "/messages", body={"value": 1})
        assert status == 201
        assert request(server, "POST", "/messages", body={"value": 2})[0] == 409
        assert request(server, "GET", "/receipts/pr47-durable-negative", auth=False)[0] == 401
        assert request(server, "GET", "/receipts/pr47-durable-negative") == (200, receipt)
        assert request(server, "GET", "/audit/effects/pr47-durable-negative") == (
            200, {"effect_count": 1}
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    server, thread = start()
    try:
        assert request(server, "GET", "/receipts/pr47-durable-negative") == (200, receipt)
        assert request(server, "POST", "/messages", body={"value": 1}) == (200, receipt)
        assert request(server, "GET", "/audit/effects/pr47-durable-negative") == (
            200, {"effect_count": 1}
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_concurrent_conflicting_payloads_preserve_single_effect(tmp_path):
    """Two competing HTTP payloads with one key cannot both commit."""
    from concurrent.futures import ThreadPoolExecutor
    import http.client

    path = tmp_path / "conflict.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE inbox (principal TEXT NOT NULL, idem_key TEXT NOT NULL, fingerprint TEXT NOT NULL, receipt TEXT NOT NULL, PRIMARY KEY (principal, idem_key))")
        db.execute("CREATE TABLE effects (id INTEGER PRIMARY KEY, principal TEXT NOT NULL, idem_key TEXT NOT NULL)")
    server = _load_receiver().serve_local(str(path), "synthetic-private-test-token-only")
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    gate = threading.Barrier(2, timeout=10)

    def send(value):
        gate.wait()
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
        try:
            conn.request("POST", "/messages", body=json.dumps({"value": value}),
                         headers={"Authorization": "Bearer synthetic-private-test-token-only",
                                  "Idempotency-Key": "pr47-durable-conflict-race",
                                  "Content-Type": "application/json"})
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(send, (1, 2)))
        assert sorted(status for status, _ in outcomes) == [201, 409]
        assert next(body for status, body in outcomes if status == 409) == {
            "error": "idempotency_conflict"
        }
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT COUNT(*) FROM effects").fetchone()[0] == 1
            assert db.execute("SELECT COUNT(*) FROM inbox").fetchone()[0] == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_concurrent_identical_requests_replay_one_durable_receipt(tmp_path):
    """Eight HTTP senders share one committed receipt, including after restart."""
    from concurrent.futures import ThreadPoolExecutor

    path = tmp_path / "identical.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE inbox (principal TEXT NOT NULL, idem_key TEXT NOT NULL, fingerprint TEXT NOT NULL, receipt TEXT NOT NULL, PRIMARY KEY (principal, idem_key))")
        db.execute("CREATE TABLE effects (id INTEGER PRIMARY KEY, principal TEXT NOT NULL, idem_key TEXT NOT NULL)")
    receiver = _load_receiver()
    token = "synthetic-private-test-token-only"
    key = "pr47-durable-identical-race"
    barrier = threading.Barrier(8, timeout=15)

    def start():
        server = receiver.serve_local(str(path), token)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread

    def request(server, method, route, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=15)
        headers = {"Authorization": "Bearer " + token, "Idempotency-Key": key}
        try:
            conn.request(method, route,
                         body=json.dumps(body) if body is not None else None,
                         headers=headers)
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()

    server, thread = start()
    try:
        def send(_):
            barrier.wait()
            return request(server, "POST", "/messages", {"value": 42})

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(send, range(8)))
        assert sorted(status for status, _ in results) == [200] * 7 + [201]
        receipts = [body for _, body in results]
        assert all(body == receipts[0] for body in receipts)
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT COUNT(*) FROM effects").fetchone()[0] == 1
            assert db.execute("SELECT COUNT(*) FROM inbox").fetchone()[0] == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    server, thread = start()
    try:
        assert request(server, "GET", "/receipts/" + key) == (200, receipts[0])
        assert request(server, "POST", "/messages", {"value": 42}) == (
            200, receipts[0]
        )
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT COUNT(*) FROM effects").fetchone()[0] == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_sqlite_receipt_write_failure_returns_503_without_effect(tmp_path):
    """Durable transaction failure must not produce HTTP 201 or an orphan effect."""
    path = tmp_path / "failure.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE inbox (principal TEXT NOT NULL, idem_key TEXT NOT NULL, fingerprint TEXT NOT NULL, receipt TEXT NOT NULL, PRIMARY KEY (principal, idem_key))")
        db.execute("CREATE TABLE effects (id INTEGER PRIMARY KEY, principal TEXT NOT NULL, idem_key TEXT NOT NULL)")
        db.execute("""
            CREATE TRIGGER reject_receipt BEFORE INSERT ON inbox
            BEGIN SELECT RAISE(ABORT, 'synthetic durable store failure'); END
        """)
    server = _load_receiver().serve_local(str(path), "synthetic-private-test-token-only")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    key = "pr47-durable-storage-failure"

    def send():
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
        try:
            conn.request("POST", "/messages", body='{"value":1}',
                         headers={"Authorization": "Bearer synthetic-private-test-token-only",
                                  "Idempotency-Key": key})
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()

    try:
        assert send() == (503, {"error": "durable_store_unavailable"})
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT COUNT(*) FROM inbox").fetchone()[0] == 0
            assert db.execute("SELECT COUNT(*) FROM effects").fetchone()[0] == 0
            db.execute("DROP TRIGGER reject_receipt")
        assert send()[0] == 201
        assert send()[0] == 200
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT COUNT(*) FROM effects").fetchone()[0] == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
