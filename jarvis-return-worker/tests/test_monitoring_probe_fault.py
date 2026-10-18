"""Exercise synthetic receiver HTTP 503 -> recovery without Railway or external traffic."""
import importlib.util
import json
import pathlib
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

PROBE = pathlib.Path(__file__).resolve().parents[1] / "monitoring" / "probe.py"
spec = importlib.util.spec_from_file_location("sandbox_monitoring_probe", PROBE)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class SyntheticReceiverFaultTests(unittest.TestCase):
    def test_two_rejections_then_accepted_alert(self):
        old_reject, old_attempts = probe.REJECT_FIRST, probe.POST_ATTEMPTS
        server = ThreadingHTTPServer(("127.0.0.1", 0), probe.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            probe.REJECT_FIRST = 2
            probe.POST_ATTEMPTS = 0
            url = "http://127.0.0.1:%d/alert" % server.server_port
            data = json.dumps({"alerts": [{"status": "firing", "labels": {"alertname": "SyntheticRetry"}}]}).encode()
            statuses = []
            for _ in range(3):
                request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
                try:
                    with urllib.request.urlopen(request, timeout=3) as response:
                        statuses.append(response.status)
                except urllib.error.HTTPError as exc:
                    statuses.append(exc.code)
            self.assertEqual(statuses, [503, 503, 202])
            self.assertEqual(probe.POST_ATTEMPTS, 3)
            self.assertEqual(probe.ALERT_EVENTS[-1], {"name": "SyntheticRetry", "status": "firing"})
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
            probe.REJECT_FIRST, probe.POST_ATTEMPTS = old_reject, old_attempts


if __name__ == "__main__":
    unittest.main()
