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


if __name__ == "__main__":
    unittest.main()
