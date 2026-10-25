import unittest
from jarvis_return_worker.ai_price_comparison import compare, estimate_usd


class PriceComparisonTests(unittest.TestCase):
    def setUp(self):
        self.candidates = [
            {"id": "model-b", "input_usd_per_million": 1, "output_usd_per_million": 4,
             "source_url": "https://provider.example/pricing", "checked_on": "2026-10-25",
             "availability": "UNVERIFIED"},
            {"id": "model-a", "input_usd_per_million": 2, "output_usd_per_million": 8,
             "source_url": "https://provider.example/models", "checked_on": "2026-10-24",
             "availability": "VERIFIED"},
        ]

    def test_estimate_and_sort(self):
        rows = compare(self.candidates, 100_000, 50_000, as_of="2026-10-25")
        self.assertEqual([row["id"] for row in rows], ["model-b", "model-a"])
        self.assertEqual(rows[0]["estimated_cost_usd"], "0.300000")
        self.assertEqual(rows[1]["estimated_cost_usd"], "0.600000")
        self.assertEqual(rows[0]["availability"], "UNVERIFIED")

    def test_half_up_at_micro_usd_boundary(self):
        self.assertEqual(estimate_usd(1, 0, 0.5, 0), __import__("decimal").Decimal("0.000001"))

    def test_rejects_negative_tokens_and_rates(self):
        with self.assertRaisesRegex(ValueError, "invalid_token_count"):
            estimate_usd(-1, 0, 1, 1)
        with self.assertRaisesRegex(ValueError, "invalid_rate"):
            estimate_usd(1, 0, -1, 1)
        for rate in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(rate=rate), self.assertRaisesRegex(ValueError, "invalid_rate"):
                estimate_usd(1, 0, rate, 1)

    def test_rejects_missing_source_and_unknown_availability(self):
        bad = [dict(self.candidates[0], source_url="http://provider.example")]
        with self.assertRaisesRegex(ValueError, "non_https_source"):
            compare(bad, 1, 1)
        bad = [dict(self.candidates[0], availability="LIKELY")]
        with self.assertRaisesRegex(ValueError, "invalid_availability"):
            compare(bad, 1, 1)

    def test_rejects_future_snapshot(self):
        with self.assertRaisesRegex(ValueError, "snapshot_from_future"):
            compare(self.candidates, 1, 1, as_of="2026-10-24")


    def test_public_snapshot_comparison_keeps_account_access_unverified(self):
        import json
        from pathlib import Path

        snapshot_path = Path(__file__).resolve().parents[1] / "benchmark-pricing-snapshot-2026-10-25.json"
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        rows = compare(snapshot["candidates"], snapshot["workload"]["input_tokens"],
                       snapshot["workload"]["output_tokens"], as_of=snapshot["as_of"])
        self.assertEqual([row["id"] for row in rows], [
            "claude-haiku-5-5", "gpt-6-luna", "gemini-3.1-flash-lite",
            "gemini-3.5-flash-lite", "gemini-3.8-flash",
            "claude-sonnet-5-5", "gpt-6.1-sol",
        ])
        self.assertEqual([row["estimated_cost_usd"] for row in rows], [
            "0.000900", "0.000900", "0.002450", "0.003500",
            "0.006750", "0.018000", "0.018000",
        ])
        self.assertTrue(all(row["availability"] == "UNVERIFIED" for row in rows))


if __name__ == "__main__":
    unittest.main()
