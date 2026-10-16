"""Validate safety-critical Sandbox alert contract without external services."""
import re
import unittest
from pathlib import Path

RULES = Path(__file__).resolve().parents[1] / "monitoring" / "alerts.yml"


def _alert_blocks(text):
    parts = re.split(r"(?m)^  - alert: ", text)
    return {part.splitlines()[0].strip(): part for part in parts[1:]}


class AlertContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = RULES.read_text(encoding="utf-8")
        cls.blocks = _alert_blocks(cls.rules)

    def test_expected_rules_and_unique_names(self):
        expected = {
            "JarvisWorkerUnavailable",
            "JarvisOutboxPending",
            "JarvisReconciliationRequired",
            "JarvisOutboxReplayFailures",
            "JarvisSyntheticFixtureUnavailable",
        }
        self.assertEqual(set(self.blocks), expected)
        self.assertEqual(self.rules.count("  - alert: "), len(expected))

    def test_every_rule_has_wait_and_staging_scope(self):
        for name, body in self.blocks.items():
            with self.subTest(name=name):
                self.assertRegex(body, r"(?m)^    for: [1-9][0-9]*m$")
                self.assertRegex(body, r"(?m)^      environment: staging$")
                self.assertRegex(body, r"(?m)^    expr: .+$")

    def test_critical_safety_conditions(self):
        self.assertIn('up{job="jarvis-sandbox-worker"} == 0',
                      self.blocks["JarvisWorkerUnavailable"])
        self.assertIn("outbox_pending_total > 0",
                      self.blocks["JarvisOutboxPending"])
        self.assertIn("reconciliation_required_queue > 0",
                      self.blocks["JarvisReconciliationRequired"])
        self.assertIn("increase(outbox_failed_total[10m]) > 0",
                      self.blocks["JarvisOutboxReplayFailures"])

    def test_no_external_delivery_or_secret_configuration(self):
        forbidden = ("webhook_url:", "api_key:", "bearer_token:", "receiver:",
                     "auto_retry:", "resend:")
        for term in forbidden:
            with self.subTest(term=term):
                self.assertNotIn(term, self.rules)


if __name__ == "__main__":
    unittest.main()
