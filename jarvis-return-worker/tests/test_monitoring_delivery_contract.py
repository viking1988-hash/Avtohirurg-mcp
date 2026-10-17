"""Offline safety checks for synthetic Alertmanager delivery and retry settings.

This is a configuration contract test, not proof of a live network-failure E2E.
"""
import pathlib
import re
import unittest

MONITORING = pathlib.Path(__file__).resolve().parents[1] / "monitoring"


class DeliveryContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = (MONITORING / "alertmanager.sandbox.yml").read_text(encoding="utf-8")
        cls.probe = (MONITORING / "probe.py").read_text(encoding="utf-8")

    def test_only_internal_synthetic_sink(self):
        urls = re.findall(r"(?m)^\s*-?\s*url:\s*(\S+)", self.config)
        self.assertEqual(urls, ["http://jarvis-synthetic-prometheus-probe.railway.internal:9102/alert"])
        self.assertNotIn("https://", self.config)

    def test_resolved_events_and_repeat_interval(self):
        self.assertRegex(self.config, r"(?m)^\s*send_resolved:\s*true\s*$")
        self.assertRegex(self.config, r"(?m)^\s*repeat_interval:\s*1h\s*$")
        self.assertIn("group_by: ['alertname', 'environment']", self.config)

    def test_probe_acknowledges_only_valid_alerts(self):
        self.assertIn('if self.path != "/alert":', self.probe)
        self.assertIn("self.send_response(202)", self.probe)
        self.assertIn("self.send_response(400)", self.probe)
        self.assertIn("SYNTHETIC_ALERT_RECEIVED", self.probe)

    def test_no_failure_injection_claim(self):
        # A real retry test needs an isolated receiver that deliberately
        # returns HTTP 503, then recovers, with observed Alertmanager logs.
        self.assertNotIn("SYNTHETIC_ALERT_REJECTED_503", self.probe)


if __name__ == "__main__":
    unittest.main()
