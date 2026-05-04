import json
import unittest

from tests._helpers import isolated_home


class ConfigTests(unittest.TestCase):
    def test_first_load_writes_defaults(self):
        with isolated_home() as home:
            from claude_oneclick.config import load
            cfg = load()
            self.assertEqual(cfg["active"], "anthropic")
            self.assertFalse(cfg["enabled"])
            self.assertEqual(cfg["proxy"]["port"], 47824)
            self.assertEqual(cfg["ui"]["port"], 47823)
            self.assertTrue(cfg["skip_vscode_login"])
            # written to disk
            cfg_file = home / ".config/claude-oneclick/config.json"
            self.assertTrue(cfg_file.exists())

    def test_set_active_and_enabled(self):
        with isolated_home():
            from claude_oneclick.config import load, set_active, set_enabled
            cfg = set_active("deepseek-chat")
            self.assertEqual(cfg["active"], "deepseek-chat")
            cfg = set_enabled(True)
            self.assertTrue(cfg["enabled"])
            again = load()
            self.assertEqual(again["active"], "deepseek-chat")
            self.assertTrue(again["enabled"])

    def test_user_preset_roundtrip(self):
        with isolated_home():
            from claude_oneclick.config import all_presets, get_preset, upsert_user_preset, delete_preset
            upsert_user_preset({
                "name": "my-custom",
                "label": "My Custom",
                "base_url": "https://example.com",
                "api_key": "sk-test",
                "model": "x",
                "small_fast_model": "x",
                "format": "openai",
            })
            p = get_preset("my-custom")
            self.assertIsNotNone(p)
            self.assertEqual(p["api_key"], "sk-test")
            self.assertFalse(p.get("builtin"))
            self.assertTrue(any(pp["name"] == "my-custom" for pp in all_presets()))
            self.assertTrue(delete_preset("my-custom"))
            self.assertIsNone(get_preset("my-custom"))

    def test_builtin_override_via_overrides(self):
        with isolated_home():
            from claude_oneclick.config import get_preset, update_override
            update_override("deepseek-chat", {"api_key": "sk-real", "model": "deepseek-v4"})
            p = get_preset("deepseek-chat")
            self.assertEqual(p["api_key"], "sk-real")
            self.assertEqual(p["model"], "deepseek-v4")
            # Must still flag as built-in.
            self.assertTrue(p.get("builtin"))

    def test_migration_from_v1(self):
        with isolated_home() as home:
            cfg_path = home / ".config/claude-oneclick"
            cfg_path.mkdir(parents=True)
            (cfg_path / "config.json").write_text(json.dumps({
                "version": 1,
                "active": "deepseek-chat",
                "enabled": False,
                "proxy": {"host": "127.0.0.1", "port": 47824},
                "ui": {"host": "127.0.0.1", "port": 47823},
                "user_presets": [],
                "overrides": {},
            }))
            from claude_oneclick.config import load
            cfg = load()
            self.assertEqual(cfg["version"], 2)
            self.assertIn("skip_vscode_login", cfg)
            self.assertIn("model_discovery", cfg)


if __name__ == "__main__":
    unittest.main()
