"""Read-only synthetic Prometheus API probe. No external endpoints or credentials."""
import json
import threading
import os
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = "http://jarvis-prometheus-monitoring-sandbox.railway.internal:9090"
QUERIES = {
    "fixture_up": 'up{job="jarvis-synthetic-fixture"}',
    "pending": "outbox_pending_total",
    "reconciliation": "reconciliation_required_queue",
    "failures": "outbox_failed_total",
}
def fetch(path):
    with urllib.request.urlopen(BASE + path, timeout=5) as response:
        return json.load(response)
def snapshot():
    result = {}
    for name, query in QUERIES.items():
        data = fetch("/api/v1/query?" + urllib.parse.urlencode({"query": query}))
        result[name] = [item["value"][1] for item in data.get("data", {}).get("result", [])]
    alerts = fetch("/api/v1/alerts")
    result["alerts"] = sorted(
        [{"name": item["labels"].get("alertname"), "state": item["state"]}
         for item in alerts.get("data", {}).get("alerts", [])],
        key=lambda item: item["name"] or "")
    return result

ALERT_EVENTS = []
ALERT_LOCK = threading.Lock()
# Disabled by default. Set only on an isolated Sandbox probe deployment.
REJECT_FIRST = max(0, min(20, int(os.environ.get("SYNTHETIC_REJECT_FIRST", "0"))))
POST_ATTEMPTS = 0

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/alert":
            self.send_error(404)
            return
        global POST_ATTEMPTS
        with ALERT_LOCK:
            POST_ATTEMPTS += 1
            reject = POST_ATTEMPTS <= REJECT_FIRST
            attempt = POST_ATTEMPTS
        if reject:
            print("SYNTHETIC_ALERT_REJECTED_503 " + json.dumps({"attempt": attempt}), flush=True)
            self.send_response(503)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(min(size, 65536)))
            events = [{"status": a.get("status"), "name": a.get("labels", {}).get("alertname")}
                      for a in payload.get("alerts", [])]
            with ALERT_LOCK:
                ALERT_EVENTS.extend(events)
            print("SYNTHETIC_ALERT_RECEIVED " + json.dumps(events, sort_keys=True), flush=True)
            body = b"accepted\\n"
            self.send_response(202)
        except Exception:
            body = b"invalid\\n"
            self.send_response(400)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        if self.path == "/health":
            body = b"ok\n"
        elif self.path == "/status":
            try:
                body = (json.dumps(snapshot(), sort_keys=True) + "\n").encode()
            except Exception as exc:
                body = (json.dumps({"error_type": type(exc).__name__}) + "\n").encode()
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *args):
        pass

if __name__ == "__main__":
    def poll():
        while True:
            try:
                print("SYNTHETIC_PROMETHEUS_SNAPSHOT " + json.dumps(snapshot(), sort_keys=True), flush=True)
            except Exception as exc:
                print("PROBE_WAIT " + type(exc).__name__ + " " + str(getattr(exc, 'reason', '')), flush=True)
            time.sleep(30)
    threading.Thread(target=poll, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "9102"))), Handler).serve_forever()
