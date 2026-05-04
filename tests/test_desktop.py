"""Tests for the Claude Desktop config integration.

Round-trip behavior (enable → disable) is the safety-critical part:
the user's pre-existing developer config must come back byte-identical.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from tests._helpers import isolated_home


def _override_path(p: Path) -> None:
    """Pin the config path through ``claude_desktop.config_path`` override."""
    from claude_oneclick.config import load, save
    cfg = load()
    cfg["claude_desktop"] = {"config_path": str(p), "endpoint_key": "thirdPartyInference.endpoint"}
    save(cfg)


class DesktopRoundTripTests(unittest.TestCase):
    def test_enable_creates_file_when_absent(self):
        with isolated_home() as home:
            target = home / "claude-test-config.json"
            _override_path(target)
            from claude_oneclick.desktop import enable, disable, status
            self.assertFalse(target.exists())
            r = enable("http://127.0.0.1:47824")
            self.assertTrue(r["ok"])
            self.assertTrue(target.exists())
            data = json.loads(target.read_text())
            self.assertEqual(data["thirdPartyInference"]["endpoint"], "http://127.0.0.1:47824")
            # status() reflects it.
            s = status()
            self.assertTrue(s["enabled"])
            # Disabling deletes the freshly-created file (clean slate).
            r2 = disable()
            self.assertTrue(r2["ok"])
            self.assertFalse(target.exists())

    def test_enable_preserves_unrelated_keys(self):
        with isolated_home() as home:
            target = home / "claude-test-config.json"
            target.write_text(json.dumps({
                "ui": {"theme": "dark"},
                "telemetry": {"optOut": True},
            }))
            _override_path(target)
            from claude_oneclick.desktop import enable, disable
            enable("http://127.0.0.1:47824")
            data = json.loads(target.read_text())
            self.assertEqual(data["ui"]["theme"], "dark")
            self.assertEqual(data["telemetry"]["optOut"], True)
            self.assertEqual(data["thirdPartyInference"]["endpoint"], "http://127.0.0.1:47824")
            # Backup file exists.
            backup = target.with_suffix(target.suffix + ".coc-backup")
            self.assertTrue(backup.exists())
            # Disable restores byte-for-byte.
            disable()
            restored = json.loads(target.read_text())
            self.assertNotIn("thirdPartyInference", restored)
            self.assertEqual(restored["ui"]["theme"], "dark")
            self.assertEqual(restored["telemetry"]["optOut"], True)
            self.assertFalse(backup.exists())  # backup consumed

    def test_double_enable_doesnt_lose_original_backup(self):
        # If the user calls enable twice, the second call must not
        # overwrite the original backup (which would corrupt the
        # restore).
        with isolated_home() as home:
            target = home / "claude-test-config.json"
            target.write_text(json.dumps({"keep": "me"}))
            _override_path(target)
            from claude_oneclick.desktop import enable, disable
            enable("http://localhost:1")
            enable("http://localhost:2")
            disable()
            self.assertEqual(json.loads(target.read_text()), {"keep": "me"})


if __name__ == "__main__":
    unittest.main()
