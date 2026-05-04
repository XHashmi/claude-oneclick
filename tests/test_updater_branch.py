"""Updater tests — branch tracking + direction-aware compare."""
from __future__ import annotations

import unittest
from unittest import mock

from tests._helpers import isolated_home


class UpdaterTrackedBranchTests(unittest.TestCase):
    def test_default_branch_is_main(self):
        with isolated_home():
            from claude_oneclick.updater import _tracked_branch
            self.assertEqual(_tracked_branch(), "main")

    def test_branch_can_be_pinned_in_config(self):
        with isolated_home():
            from claude_oneclick.config import load, save
            from claude_oneclick.updater import _tracked_branch
            cfg = load(); cfg["updater"] = {"branch": "feature/foo"}; save(cfg)
            self.assertEqual(_tracked_branch(), "feature/foo")


class CompareAheadTests(unittest.TestCase):
    def test_identical_returns_zero(self):
        from claude_oneclick.updater import _compare_ahead
        self.assertEqual(_compare_ahead("abc", "abc"), 0)

    def test_empty_returns_zero(self):
        from claude_oneclick.updater import _compare_ahead
        self.assertEqual(_compare_ahead("", "abc"), 0)
        self.assertEqual(_compare_ahead("abc", ""), 0)

    def test_ahead_status_returns_count(self):
        # Mock GitHub's compare API: remote is 3 commits ahead.
        with mock.patch("urllib.request.urlopen") as op:
            op.return_value.__enter__.return_value.read.return_value = (
                b'{"status": "ahead", "ahead_by": 3, "behind_by": 0}'
            )
            from claude_oneclick.updater import _compare_ahead
            self.assertEqual(_compare_ahead("local", "remote"), 3)

    def test_behind_returns_zero_no_downgrade(self):
        # Local is ahead of remote (e.g. user installed from feature
        # branch, updater is asked to compare against main which is
        # behind). Don't offer to "update" backwards.
        with mock.patch("urllib.request.urlopen") as op:
            op.return_value.__enter__.return_value.read.return_value = (
                b'{"status": "behind", "ahead_by": 0, "behind_by": 5}'
            )
            from claude_oneclick.updater import _compare_ahead
            self.assertEqual(_compare_ahead("local", "remote"), 0)

    def test_diverged_returns_zero(self):
        with mock.patch("urllib.request.urlopen") as op:
            op.return_value.__enter__.return_value.read.return_value = (
                b'{"status": "diverged", "ahead_by": 2, "behind_by": 4}'
            )
            from claude_oneclick.updater import _compare_ahead
            self.assertEqual(_compare_ahead("local", "remote"), 0)

    def test_network_error_returns_none(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("offline")):
            from claude_oneclick.updater import _compare_ahead
            self.assertIsNone(_compare_ahead("a", "b"))


if __name__ == "__main__":
    unittest.main()
