"""Synthetic metrics only: never reads CRM, Worker, tokens, or real data."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
import time

START = time.monotonic()
PORT = int(os.environ.get("PORT", "9101"))

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ("/metrics", "/health"):
            self.send_error(404)
            return
        if self.path == "/health":
            body = b"ok\n"
        else:
            elapsed = int(time.monotonic() - START)
            # Controlled synthetic transitions: normal -> incident -> recovery.
            incident = 1 if 120 <= elapsed < 1200 else 0
            failed = 1 if elapsed >= 120 else 0
            body = (
                "# HELP outbox_pending_total Synthetic pending deliveries\n"
                "# TYPE outbox_pending_total gauge\n"
                f"outbox_pending_total {incident}\n"
                "# HELP reconciliation_required_queue Synthetic reconciliation backlog\n"
                "# TYPE reconciliation_required_queue gauge\n"
                f"reconciliation_required_queue {incident}\n"
                "# HELP outbox_failed_total Synthetic failed delivery count\n"
                "# TYPE outbox_failed_total counter\n"
                f"outbox_failed_total {failed}\n"
            ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass

if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
