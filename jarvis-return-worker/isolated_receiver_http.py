"""Fake-only HTTP receiver for isolated idempotency E2E.

Not configured in Railway. No external transport. Uses the SQLite inbox reference
from tests/test_receiver_idempotency_reference.py and a local HTTP server.
The HTTP auth token is provided explicitly; never bind to a public interface.
"""
import hmac
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tests.test_receiver_idempotency_reference import ReceiverInbox


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
            if self.path != "/messages":
                return self.reply(404, {"error": "not_found"})
            key = self.headers.get("Idempotency-Key", "")
            length = self.headers.get("Content-Length")
            if not key or not length or not length.isdigit() or int(length) > 65536:
                return self.reply(400, {"error": "invalid_key_or_length"})
            try:
                body = json.loads(self.rfile.read(int(length)))
            except (ValueError, UnicodeDecodeError):
                return self.reply(400, {"error": "invalid_json"})
            status, result = inbox.submit("isolated-test-sender", "POST", "/messages", key, body)
            return self.reply(status, result)

        def do_GET(self):
            if not self.authenticate():
                return self.reply(401, {"error": "unauthorized"})
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
