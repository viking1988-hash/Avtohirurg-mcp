#!/usr/bin/env python3
"""Loopback-only, authenticated, exact-route Prometheus snapshot controller.

This is a *prototype*. Do not deploy on an existing Railway Prometheus service
until a proxy keeps the admin API unreachable from the service network.
"""
import hmac
import http.client
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

SNAPSHOT_ROUTE = "/v1/snapshot"
UPSTREAM_ROUTE = "/api/v1/admin/tsdb/snapshot"
_snapshot_lock = threading.Lock()

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if urlsplit(self.path).path != SNAPSHOT_ROUTE or self.path != SNAPSHOT_ROUTE:
            return self._deny(404)
        token = os.environ.get("PROMETHEUS_SNAPSHOT_CONTROLLER_TOKEN", "")
        if len(token) < 32:
            return self._deny(503)
        supplied = self.headers.get("Authorization", "")
        if not hmac.compare_digest(supplied, "Bearer " + token):
            return self._deny(401)
        if self.headers.get("Content-Length", "0") not in ("", "0"):
            return self._deny(400)
        if not _snapshot_lock.acquire(blocking=False):
            return self._deny(409)
        try:
            conn = http.client.HTTPConnection("127.0.0.1", 19091, timeout=30)
            conn.request("POST", UPSTREAM_ROUTE)
            response = conn.getresponse()
            payload = response.read(1024 * 1024 + 1)
            if len(payload) > 1024 * 1024:
                return self._deny(502)
            self.send_response(response.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            conn.close()
        except (OSError, http.client.HTTPException):
            self._deny(502)
        finally:
            _snapshot_lock.release()

    def do_GET(self):
        self._deny(405)

    def _deny(self, status):
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, fmt, *args):
        # Never log authorization headers.
        return

def main():
    # Fixed loopback binding: caller cannot override to 0.0.0.0.
    ThreadingHTTPServer(("127.0.0.1", 19092), Handler).serve_forever()

if __name__ == "__main__":
    main()
