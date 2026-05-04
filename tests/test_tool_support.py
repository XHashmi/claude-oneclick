"""Heuristic for which models drive Claude Code's tool loop."""
from __future__ import annotations

import unittest

from claude_oneclick.tool_support import supports_tools


class ToolSupportTests(unittest.TestCase):
    def test_known_good(self):
        for m in [
            "claude-sonnet-4-6",
            "deepseek-v4-pro",
            "deepseek-chat",
            "deepseek-r1",
            "meta/llama-3.1-405b-instruct",
            "meta/llama-3.3-70b-instruct",
            "qwen2.5-72b-instruct",
            "mistralai/mistral-large-2407",
            "nvidia/nemotron-70b-instruct",
            "gpt-4o",
            "google/gemini-1.5-pro",
            "zhipuai/glm-4.5",
        ]:
            self.assertTrue(supports_tools(m), f"{m} should be true")

    def test_known_bad(self):
        for m in [
            "meta-llama/llama-2-13b",
            "codellama/codellama-34b",
            "deepseek-coder-v1",
            "microsoft/phi-3-mini-4k-instruct",
            "openai/text-embedding-3-large",
            "stable-diffusion-3",
        ]:
            self.assertFalse(supports_tools(m), f"{m} should be false")

    def test_unknown(self):
        self.assertIsNone(supports_tools(None))
        self.assertIsNone(supports_tools(""))
        self.assertIsNone(supports_tools("some-niche-model-7b"))

    def test_bad_overrides_good(self):
        # llama-2-13b contains 'llama' (good prefix) but 'llama-2' (bad).
        # The bad pattern should win.
        self.assertFalse(supports_tools("meta/llama-2-70b-chat"))


class ResolveApiKeyHostScopingTests(unittest.TestCase):
    def test_only_falls_back_to_same_host_siblings(self):
        from tests._helpers import isolated_home
        from claude_oneclick.config import (
            _resolve_api_key,
            save,
            upsert_user_preset,
        )
        with isolated_home():
            # Two custom presets with the SAME group but DIFFERENT hosts:
            # the empty-key one must NOT inherit from the other.
            upsert_user_preset({
                "name": "groq-llama",
                "label": "Groq",
                "group": "Other hosted",
                "base_url": "https://api.groq.com/openai",
                "api_key": "groq-secret-key",
                "model": "llama-3.3-70b",
                "format": "openai",
            })
            upsert_user_preset({
                "name": "openrouter",
                "label": "OpenRouter",
                "group": "Other hosted",
                "base_url": "https://openrouter.ai/api/v1",
                "api_key": "",  # explicitly missing
                "model": "anthropic/claude-3.5",
                "format": "openai",
            })
            from claude_oneclick.config import get_preset
            openrouter = get_preset("openrouter")
            self.assertEqual(_resolve_api_key(openrouter), "")  # different host → no fallback

    def test_falls_back_within_same_host(self):
        from tests._helpers import isolated_home
        from claude_oneclick.config import (
            _resolve_api_key,
            get_preset,
            upsert_user_preset,
        )
        with isolated_home():
            upsert_user_preset({
                "name": "ds-pro",
                "label": "DeepSeek Pro",
                "group": "DeepSeek",
                "base_url": "https://api.deepseek.com",
                "api_key": "ds-key",
                "model": "deepseek-v4-pro",
                "format": "openai",
            })
            upsert_user_preset({
                "name": "ds-flash",
                "label": "DeepSeek Flash",
                "group": "DeepSeek",
                "base_url": "https://api.deepseek.com",
                "api_key": "",
                "model": "deepseek-v4-flash",
                "format": "openai",
            })
            self.assertEqual(_resolve_api_key(get_preset("ds-flash")), "ds-key")


if __name__ == "__main__":
    unittest.main()
