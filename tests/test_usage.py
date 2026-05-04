"""Tests for the per-preset usage ledger."""
import unittest

from tests._helpers import isolated_home


class UsageTests(unittest.TestCase):
    def test_record_and_report(self):
        with isolated_home():
            from claude_oneclick import usage
            usage.record("deepseek-v4-pro", input_tokens=100, output_tokens=50)
            usage.record("deepseek-v4-pro", input_tokens=200, output_tokens=80)
            usage.record("nim-llama-405b", input_tokens=10, output_tokens=5)
            r = usage.report(days=1)
            self.assertEqual(r["total"]["requests"], 3)
            self.assertEqual(r["total"]["input"], 310)
            self.assertEqual(r["total"]["output"], 135)
            self.assertEqual(r["presets"]["deepseek-v4-pro"]["requests"], 2)
            self.assertEqual(r["presets"]["nim-llama-405b"]["input"], 10)

    def test_estimate_cost(self):
        from claude_oneclick.usage import estimate_cost
        # Made-up rates: $1.00 per 1M input, $3.00 per 1M output.
        preset = {"cost_per_1m_input": 1.0, "cost_per_1m_output": 3.0}
        self.assertEqual(estimate_cost(preset, 1_000_000, 0), 1.0)
        self.assertEqual(estimate_cost(preset, 0, 1_000_000), 3.0)
        self.assertEqual(estimate_cost(preset, 500_000, 200_000), 0.5 + 0.6)
        self.assertIsNone(estimate_cost({}, 1, 2))

    def test_error_recorded_separately(self):
        with isolated_home():
            from claude_oneclick import usage
            usage.record("p", input_tokens=1, output_tokens=2, error=True)
            usage.record("p", input_tokens=3, output_tokens=4, error=False)
            r = usage.report(days=1)
            self.assertEqual(r["presets"]["p"]["errors"], 1)
            self.assertEqual(r["presets"]["p"]["requests"], 2)


if __name__ == "__main__":
    unittest.main()
