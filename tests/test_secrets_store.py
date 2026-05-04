"""Tests for the OS-keychain secrets backend.

Hardware-dependent: the actual keychain calls are skipped on CI runners
without a backend. The behavior tests (resolver fallback, set-via-
keychain when enabled) use a monkey-patched in-memory backend.
"""
from __future__ import annotations

import unittest

from tests._helpers import isolated_home


class FakeBackend:
    """In-memory replacement for secrets_store, used by the resolver tests."""
    store: dict[str, str] = {}

    @classmethod
    def reset(cls):
        cls.store = {}

    @classmethod
    def available(cls): return True

    @classmethod
    def set(cls, group, key): cls.store[group] = key

    @classmethod
    def get(cls, group): return cls.store.get(group)

    @classmethod
    def delete(cls, group):
        return cls.store.pop(group, None) is not None


class KeychainResolverTests(unittest.TestCase):
    def setUp(self):
        FakeBackend.reset()

    def _patch(self):
        # Make config.py see our fake backend.
        from claude_oneclick import config as cfg_mod
        # The lazy-import inside _resolve_api_key dispatches to
        # claude_oneclick.secrets_store.{available,get,set,delete}.
        # Patch the module-level reference.
        import claude_oneclick.secrets_store as real
        real.available = FakeBackend.available
        real.get = FakeBackend.get
        real.set = FakeBackend.set
        real.delete = FakeBackend.delete

    def test_resolver_falls_back_to_keychain_when_enabled(self):
        with isolated_home():
            self._patch()
            from claude_oneclick.config import (
                _resolve_api_key, all_presets, load, save, set_api_key_for_group,
            )
            cfg = load(); cfg["use_keychain"] = True; save(cfg)
            # Stash a key in the fake keychain at the group level.
            FakeBackend.store["DeepSeek"] = "sk-from-keychain"
            ds = next(p for p in all_presets() if p["name"] == "deepseek-v4-pro")
            self.assertEqual(_resolve_api_key(ds), "sk-from-keychain")

    def test_resolver_prefers_explicit_field(self):
        with isolated_home():
            self._patch()
            from claude_oneclick.config import (
                _resolve_api_key, get_preset, load, save, update_override,
            )
            cfg = load(); cfg["use_keychain"] = True; save(cfg)
            FakeBackend.store["DeepSeek"] = "sk-keychain"
            update_override("deepseek-v4-pro", {"api_key": "sk-explicit"})
            p = get_preset("deepseek-v4-pro")
            self.assertEqual(_resolve_api_key(p), "sk-explicit")

    def test_set_writes_to_keychain_and_clears_plaintext(self):
        with isolated_home():
            self._patch()
            from claude_oneclick.config import (
                all_presets, load, save, set_api_key_for_group,
            )
            cfg = load(); cfg["use_keychain"] = True; save(cfg)
            updated = set_api_key_for_group("deepseek-v4-pro", "sk-new")
            # All DeepSeek presets are listed as "updated" but their
            # api_key fields stay empty (key lives in the keychain).
            self.assertGreaterEqual(len(updated), 4)
            self.assertEqual(FakeBackend.store.get("DeepSeek"), "sk-new")
            for p in all_presets():
                if p.get("group") == "DeepSeek":
                    # Plaintext field should be empty; resolver pulls from keychain.
                    self.assertEqual(p.get("api_key", ""), "")


class KeychainBackendInstalledTest(unittest.TestCase):
    """Smoke-test the real platform backend if one is present."""

    def test_set_get_delete_roundtrip(self):
        from claude_oneclick import secrets_store
        if not secrets_store.available():
            self.skipTest("no keychain backend on this machine")
        # Use a unique group so we never collide with the user's real keys.
        import uuid
        group = f"_coc_test_{uuid.uuid4().hex[:8]}"
        try:
            secrets_store.set(group, "test-value-do-not-care")
            self.assertEqual(secrets_store.get(group), "test-value-do-not-care")
        finally:
            secrets_store.delete(group)


if __name__ == "__main__":
    unittest.main()
