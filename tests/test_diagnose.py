"""Tests for the self-diagnostic wiring report."""
from __future__ import annotations

import unittest

from tests._helpers import isolated_home


class DiagnoseTests(unittest.TestCase):
    def test_off_state(self):
        # Toggle OFF + default Anthropic preset = clean "off" report,
        # never red.
        with isolated_home():
            from claude_oneclick.diagnose import diagnose
            r = diagnose()
            self.assertEqual(r["overall"], "off")
            self.assertEqual(r["enabled"], False)
            self.assertEqual(r["nodes"]["provider"]["status"], "off")
            self.assertEqual(r["wires"]["provider_proxy"]["status"], "off")
            self.assertEqual(r["wires"]["proxy_claude"]["status"], "off")

    def test_no_api_key_warns_provider(self):
        # An openai preset with no api key set should warn (not error).
        with isolated_home():
            from claude_oneclick.config import set_active, set_enabled, upsert_user_preset
            from claude_oneclick.diagnose import diagnose
            upsert_user_preset({
                "name": "test-noisy",
                "label": "Test",
                "base_url": "https://example.invalid",
                "api_key": "",  # no key
                "model": "x",
                "small_fast_model": "x",
                "format": "openai",
            })
            set_active("test-noisy")
            set_enabled(True)
            r = diagnose()
            self.assertEqual(r["nodes"]["provider"]["status"], "warn")
            self.assertIn("api key", r["nodes"]["provider"]["detail"].lower())

    def test_unreachable_provider_errors(self):
        # Pointing at an unreachable host should produce an error node.
        with isolated_home():
            from claude_oneclick.config import set_active, set_enabled, upsert_user_preset
            from claude_oneclick.diagnose import diagnose
            upsert_user_preset({
                "name": "test-unreach",
                "label": "Test",
                # 198.51.100.x is reserved for documentation — never reachable.
                "base_url": "http://198.51.100.7:9",
                "api_key": "sk-x",
                "model": "x",
                "small_fast_model": "x",
                "format": "openai",
                "request_timeout_seconds": 2,
            })
            set_active("test-unreach")
            set_enabled(True)
            r = diagnose()
            self.assertEqual(r["nodes"]["provider"]["status"], "error")
            self.assertEqual(r["overall"], "error")
            # Wire 1 mirrors the provider node.
            self.assertEqual(r["wires"]["provider_proxy"]["status"], "error")


if __name__ == "__main__":
    unittest.main()
