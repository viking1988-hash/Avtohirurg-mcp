"""Static safety checks for the sandbox Prometheus alert rules.

These tests do not contact Prometheus, Railway, or external providers.
"""
from pathlib import Path
import unittest

RULES = Path(__file__).resolve().parents[1] / "monitoring" / "alerts.yml"


class MonitoringRulesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = RULES.read_text(encoding="utf-8")

    def test_expected_alerts_present(self):
        for name in (
            "JarvisWorkerUnavailable",
            "JarvisOutboxPending",
            "JarvisReconciliationRequired",
            "JarvisOutboxReplayFailures",
            "JarvisSyntheticFixtureUnavailable",
        ):
            with self.subTest(alert=name):
                self.assertIn("alert: " + name, self.text)

    def test_all_alerts_scoped_to_staging(self):
        self.assertEqual(self.text.count("alert: "), 5)
        self.assertEqual(self.text.count("environment: staging"), 5)

    def test_alerts_do_not_perform_automatic_retries(self):
        for unsafe in ("webhook_url:", "receiver:", "auto_retry:", "resend:"):
            with self.subTest(setting=unsafe):
                self.assertNotIn(unsafe, self.text)

    def test_existing_metric_names(self):
        for metric in (
            "outbox_pending_total",
            "reconciliation_required_queue",
            "outbox_failed_total",
        ):
            with self.subTest(metric=metric):
                self.assertIn(metric, self.text)


if __name__ == "__main__":
    unittest.main()
