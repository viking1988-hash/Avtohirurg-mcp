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
