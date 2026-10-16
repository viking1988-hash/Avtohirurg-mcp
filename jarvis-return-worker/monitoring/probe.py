"""Read-only synthetic Prometheus API probe. No external endpoints or credentials."""
import json
import os
import threading
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

class Handler(BaseHTTPRequestHandler):
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
