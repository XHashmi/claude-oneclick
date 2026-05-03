import os
import unittest
from pathlib import Path

from tests._helpers import isolated_home


class ShellTests(unittest.TestCase):
    def test_install_and_uninstall_idempotent(self):
        with isolated_home() as home:
            # Pre-create a bashrc with existing content.
            bashrc = home / ".bashrc"
            bashrc.write_text("# user content\nexport FOO=bar\n", encoding="utf-8")
            from claude_oneclick.shell import install_shell_hook, uninstall_shell_hook
            touched = install_shell_hook()
            self.assertIn(bashrc, touched)
            text = bashrc.read_text()
            self.assertIn("# >>> claude-oneclick >>>", text)
            self.assertIn("# user content", text)
            self.assertIn("export FOO=bar", text)
            # Re-running install must not duplicate.
            install_shell_hook()
            self.assertEqual(bashrc.read_text().count("# >>> claude-oneclick >>>"), 1)
            # Uninstall removes our block, leaves user content.
            uninstall_shell_hook()
            after = bashrc.read_text()
            self.assertNotIn("claude-oneclick", after)
            self.assertIn("export FOO=bar", after)

    def test_env_sh_renders_when_off(self):
        with isolated_home():
            from claude_oneclick.shell import write_env_sh
            from claude_oneclick.paths import env_file
            env = write_env_sh()
            self.assertEqual(env, {})
            self.assertIn("toggle is OFF", env_file().read_text())

    def test_env_sh_renders_when_on(self):
        with isolated_home():
            from claude_oneclick.config import set_active, set_enabled, update_override
            from claude_oneclick.shell import write_env_sh
            from claude_oneclick.paths import env_file
            update_override("deepseek", {"api_key": "sk-xxx", "model": "deepseek-v4"})
            set_active("deepseek")
            set_enabled(True)
            env = write_env_sh()
            self.assertIn("ANTHROPIC_BASE_URL", env)
            self.assertIn("ANTHROPIC_AUTH_TOKEN", env)
            self.assertIn("CLAUDE_CODE_SKIP_LOGIN", env)
            text = env_file().read_text()
            self.assertIn("export ANTHROPIC_BASE_URL=", text)


if __name__ == "__main__":
    unittest.main()
