"""Safety regression: staging Prometheus must not expose admin API by default."""
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[3]
START = ROOT / "jarvis-return-worker/monitoring/start-prometheus.sh"
DOCKERFILE = ROOT / "Dockerfile.worker-private-monitoring-sandbox"

class PrometheusAdminAPISafetyTests(unittest.TestCase):
    def test_start_script_does_not_enable_admin_api(self):
        content = START.read_text(encoding="utf-8")
        self.assertNotIn("--web.enable-admin-api", content)
        self.assertIn("--storage.tsdb.path=/prometheus", content)

    def test_dockerfile_does_not_enable_admin_api(self):
        content = DOCKERFILE.read_text(encoding="utf-8")
        self.assertNotIn("--web.enable-admin-api", content)

if __name__ == "__main__":
    unittest.main()
