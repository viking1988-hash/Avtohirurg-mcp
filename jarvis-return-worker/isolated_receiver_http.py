"""Fake-only HTTP receiver for isolated idempotency E2E.

Synthetic-only receiver. No external transport. Uses the SQLite inbox reference
from tests/test_receiver_idempotency_reference.py and a local HTTP server.
The HTTP auth token is provided explicitly; deploy only on private networking.
"""
import hmac
import importlib.util
import pathlib
import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_reference_path = pathlib.Path(__file__).resolve().parent / 'tests' / 'test_receiver_idempotency_reference.py'
_spec = importlib.util.spec_from_file_location('isolated_receiver_reference', _reference_path)
_reference = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_reference)
ReceiverInbox = _reference.ReceiverInbox


def make_handler(database_path, token):
    inbox = ReceiverInbox(database_path)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def reply(self, status, payload):
            data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def authenticate(self):
            value = self.headers.get("Authorization", "")
            return hmac.compare_digest(value, "Bearer " + token)

        def do_POST(self):
            if not self.authenticate():
                return self.reply(401, {"error": "unauthorized"})
            import re
            from urllib.parse import unquote
            cancel = re.fullmatch(r"/messages/([^/]+)/cancel", self.path)
            if cancel:
                return self._mutate_message("POST", unquote(cancel.group(1)), "/cancel")
            if self.path != "/messages":
                return self.reply(404, {"error": "not_found"})
            key = self.headers.get("Idempotency-Key", "")
            length = self.headers.get("Content-Length")
            if (not key or len(key) > 128 or not key.isascii() or
                    not all(c.isalnum() or c in "-_" for c in key) or
                    not length or not length.isdigit() or
                    not 0 < int(length) <= 65536):
                return self.reply(400, {"error": "invalid_key_or_length"})
            try:
                body = json.loads(self.rfile.read(int(length)))
            except (ValueError, UnicodeDecodeError):
                return self.reply(400, {"error": "invalid_json"})
            try:
                status, result = inbox.submit("isolated-test-sender", "POST", "/messages", key, body)
            except sqlite3.Error:
                # Never acknowledge acceptance when the durable transaction fails.
                return self.reply(503, {"error": "durable_store_unavailable"})
            return self.reply(status, result)

        def _message(self, message_id):
            with sqlite3.connect(database_path, timeout=10) as db:
                rows = db.execute(
                    "SELECT i.idem_key, i.receipt FROM inbox i "
                    "JOIN effects e ON e.principal=i.principal AND e.idem_key=i.idem_key "
                    "WHERE i.principal=? ORDER BY e.id DESC",
                    ("isolated-test-sender",)).fetchall()
                for key, raw in rows:
                    receipt = json.loads(raw)
                    if receipt.get("provider_message_id") == message_id:
                        return key, receipt
            return None, None

        def _mutate_message(self, method, message_id, suffix=""):
            if not self.authenticate():
                return self.reply(401, {"error": "unauthorized"})
            key = self.headers.get("Idempotency-Key", "")
            if not key or len(key) > 128 or not key.isascii() or not all(
                    ch.isalnum() or ch in "-_" for ch in key):
                return self.reply(400, {"error": "invalid_key"})
            if method == "PATCH":
                length = self.headers.get("Content-Length", "")
                if not length.isdigit() or not 0 < int(length) <= 65536:
                    return self.reply(400, {"error": "invalid_length"})
                try:
                    payload = json.loads(self.rfile.read(int(length)))
                except (ValueError, UnicodeDecodeError):
                    return self.reply(400, {"error": "invalid_json"})
            else:
                payload = {}
            # All effects and receipts, including updates/cancellations, share
            # the same durable idempotency transaction and unique key scope.
            try:
                with sqlite3.connect(database_path, timeout=10, isolation_level=None) as db:
                    db.execute("BEGIN IMMEDIATE")
                    rows = db.execute("SELECT idem_key, receipt FROM inbox WHERE principal=?",
                                      ("isolated-test-sender",)).fetchall()
                    target = next((json.loads(raw) for _, raw in rows
                                   if json.loads(raw).get("provider_message_id") == message_id),
                                  None)
                    if target is None:
                        db.execute("COMMIT")
                        return self.reply(404, {"error": "not_found"})
                    fingerprint = json.dumps(
                        ["isolated-test-sender", method, "/messages/" + message_id + suffix, payload],
                        sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                    prior = db.execute("SELECT fingerprint, receipt FROM inbox WHERE principal=? AND idem_key=?",
                                       ("isolated-test-sender", key)).fetchone()
                    if prior:
                        db.execute("COMMIT")
                        if prior[0] != fingerprint:
                            return self.reply(409, {"error": "idempotency_conflict"})
                        return self.reply(200, json.loads(prior[1]))
                    status = "UPDATED" if method == "PATCH" else "CANCELLED"
                    receipt = dict(target, status=status)
                    db.execute("INSERT INTO effects (principal, idem_key) VALUES (?,?)",
                               ("isolated-test-sender", key))
                    db.execute("INSERT INTO inbox (principal, idem_key, fingerprint, receipt) VALUES (?,?,?,?)",
                               ("isolated-test-sender", key, fingerprint, json.dumps(receipt)))
                    db.execute("COMMIT")
                return self.reply(200, receipt)
            except sqlite3.Error:
                return self.reply(503, {"error": "durable_store_unavailable"})

        def do_PATCH(self):
            from urllib.parse import unquote
            import re
            match = re.fullmatch(r"/messages/([^/]+)", self.path)
            if not match:
                return self.reply(404, {"error": "not_found"})
            return self._mutate_message("PATCH", unquote(match.group(1)))

        def do_GET(self):
            if self.path == "/ready":
                return self.reply(200, {"status": "ready"})
            if not self.authenticate():
                return self.reply(401, {"error": "unauthorized"})
            if self.path.startswith("/audit/effects/"):
                key = self.path[len("/audit/effects/"):]
                if (not key.startswith("pr47-durable-") or
                        not key.replace("-", "").replace("_", "").isalnum() or
                        len(key) > 128):
                    return self.reply(400, {"error": "invalid_synthetic_key"})
                with sqlite3.connect(database_path, timeout=5) as db:
                    count = db.execute(
                        "SELECT COUNT(*) FROM effects WHERE principal=? AND idem_key=?",
                        ("isolated-test-sender", key),
                    ).fetchone()[0]
                return self.reply(200, {"effect_count": count})
            if self.path.startswith("/messages/"):
                from urllib.parse import unquote
                import re
                match = re.fullmatch(r"/messages/([^/]+)", self.path)
                if not match:
                    return self.reply(404, {"error": "not_found"})
                try:
                    _, receipt = self._message(unquote(match.group(1)))
                except sqlite3.Error:
                    return self.reply(503, {"error": "durable_store_unavailable"})
                return self.reply(200, receipt) if receipt else self.reply(404, {"error": "not_found"})
            if not self.path.startswith("/receipts/"):
                return self.reply(404, {"error": "not_found"})
            key = self.path[len("/receipts/"):]
            if not key or "/" in key or "?" in key or "#" in key:
                return self.reply(400, {"error": "invalid_key"})
            receipt = inbox.receipt("isolated-test-sender", key)
            return self.reply(200, receipt) if receipt else self.reply(404, {"error": "not_found"})

    return Handler


def serve_local(database_path, token):
    if not token or len(token) < 16:
        raise ValueError("explicit test token required")
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(database_path, token))
    return server
