"""Synthetic-only HTTP compatibility tests for durable sandbox receiver."""
import http.client
import importlib.util
import json
import sqlite3
import threading
from pathlib import Path

def test_message_lifecycle_durable_replay_and_conflict(tmp_path):
    dbpath = str(tmp_path / "receiver.sqlite3")
    with sqlite3.connect(dbpath) as db:
        db.execute("CREATE TABLE inbox (principal TEXT NOT NULL, idem_key TEXT NOT NULL, fingerprint TEXT NOT NULL, receipt TEXT NOT NULL, PRIMARY KEY (principal, idem_key))")
        db.execute("CREATE TABLE effects (id INTEGER PRIMARY KEY, principal TEXT NOT NULL, idem_key TEXT NOT NULL)")
    source = Path(__file__).resolve().parents[1] / "isolated_receiver_http.py"
    spec = importlib.util.spec_from_file_location("pr47_lifecycle_receiver", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    token = "synthetic-private-token-123456789"
    def start():
        server = module.serve_local(dbpath, token)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread
    def call(server, method, route, key="pr47-send", body=None):
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
        headers = {"Authorization": "Bearer " + token, "Idempotency-Key": key,
                   "Content-Type": "application/json"}
        try:
            conn.request(method, route, json.dumps(body).encode() if body is not None else None, headers)
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()
    server, thread = start()
    try:
        code, first = call(server, "POST", "/messages", body={"payload": {"x": 1}})
        assert code == 201
        mid = first["provider_message_id"]
        # Public staging domain must never allow unauthenticated reads or mutations.
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
        try:
            for method, route in (("GET", "/messages/" + mid),
                                  ("PATCH", "/messages/" + mid),
                                  ("POST", "/messages/" + mid + "/cancel"),
                                  ("GET", "/audit/effects/pr47-durable-test")):
                conn.request(method, route)
                response = conn.getresponse()
                assert response.status == 401, (method, route, response.status)
                response.read()
        finally:
            conn.close()

        assert call(server, "GET", "/messages/" + mid) == (200, first)
        update = call(server, "PATCH", "/messages/" + mid, "pr47-update", {"payload": {"x": 2}})
        assert update[0] == 200 and update[1]["status"] == "UPDATED"
        assert call(server, "PATCH", "/messages/" + mid, "pr47-update", {"payload": {"x": 2}}) == update
        assert call(server, "PATCH", "/messages/" + mid, "pr47-update", {"payload": {"x": 3}})[0] == 409
        cancel = call(server, "POST", "/messages/" + mid + "/cancel", "pr47-cancel", {})
        assert cancel[0] == 200 and cancel[1]["status"] == "CANCELLED"
        assert call(server, "POST", "/messages/" + mid + "/cancel", "pr47-cancel", {}) == cancel
        assert call(server, "GET", "/messages/" + mid)[0] == 200
        with sqlite3.connect(dbpath) as db:
            assert db.execute("SELECT COUNT(*) FROM effects").fetchone()[0] == 3
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    server, thread = start()
    try:
        assert call(server, "POST", "/messages", body={"payload": {"x": 1}}) == (200, first)
        assert call(server, "POST", "/messages/" + mid + "/cancel", "pr47-cancel", {}) == cancel
        with sqlite3.connect(dbpath) as db:
            assert db.execute("SELECT COUNT(*) FROM effects").fetchone()[0] == 3
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
