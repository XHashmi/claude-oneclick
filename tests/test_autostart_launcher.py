"""Smoke tests for autostart entries + desktop launcher generation.

Each platform has a different file path; we only test the one that
matches the runner's OS and skip the rest. Coverage for the *other*
OSes comes from CI (Linux/macOS/Windows matrix).
"""
from __future__ import annotations

import platform
import unittest
from pathlib import Path

from tests._helpers import isolated_home


@unittest.skipUnless(platform.system() == "Linux", "linux-only")
class LinuxAutostartTests(unittest.TestCase):
    def test_enable_creates_desktop_file(self):
        with isolated_home() as home:
            from claude_oneclick.autostart import (
                LINUX_AUTOSTART_FILE, _linux_disable, _linux_enable, _linux_is_enabled,
            )
            self.assertFalse(_linux_is_enabled())
            self.assertTrue(_linux_enable())
            self.assertTrue(LINUX_AUTOSTART_FILE.exists())
            text = LINUX_AUTOSTART_FILE.read_text()
            self.assertIn("[Desktop Entry]", text)
            self.assertIn("claude_oneclick", text)
            self.assertIn("_boot", text)
            self.assertTrue(_linux_is_enabled())
            self.assertTrue(_linux_disable())
            self.assertFalse(LINUX_AUTOSTART_FILE.exists())


@unittest.skipUnless(platform.system() == "Linux", "linux-only")
class LinuxLauncherTests(unittest.TestCase):
    def test_install_then_uninstall(self):
        with isolated_home():
            from claude_oneclick.launcher import (
                LINUX_DESKTOP_FILE, install_linux_launcher, uninstall_linux_launcher,
            )
            p = install_linux_launcher()
            self.assertIsNotNone(p)
            self.assertTrue(LINUX_DESKTOP_FILE.exists())
            text = LINUX_DESKTOP_FILE.read_text()
            self.assertIn("Name=Claude OneClick", text)
            self.assertTrue(uninstall_linux_launcher())
            self.assertFalse(LINUX_DESKTOP_FILE.exists())


@unittest.skipUnless(platform.system() == "Darwin", "macos-only")
class MacOSLauncherTests(unittest.TestCase):
    def test_install_then_uninstall(self):
        with isolated_home():
            from claude_oneclick.launcher import (
                install_macos_launcher, macos_launcher_path, uninstall_macos_launcher,
            )
            p = install_macos_launcher()
            self.assertIsNotNone(p)
            self.assertTrue(macos_launcher_path().exists())
            self.assertTrue(uninstall_macos_launcher())
            self.assertFalse(macos_launcher_path().exists())


if __name__ == "__main__":
    unittest.main()
