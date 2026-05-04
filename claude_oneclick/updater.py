"""Self-update support.

The repo is the source of truth. We detect a newer version by comparing
the **local git HEAD** of the package directory against the latest
commit on the configured remote branch (default ``main``) via the
GitHub API. If the local install isn't a git checkout (e.g. someone
pip-installed a wheel) we say "unknown" instead of "no updates" so the
UI doesn't lie.

Apply runs ``git pull --ff-only`` followed by ``pip install --user -e .``
so the editable install picks up any pyproject.toml changes. We refuse
to pull if the working tree is dirty — never silently overwrite local
edits.

Designed to work without network at install time (the updater only
fetches when explicitly checked) and without any non-stdlib deps.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import claude_oneclick

GITHUB_OWNER = "xhashmi"
GITHUB_REPO = "claude-oneclick"
DEFAULT_BRANCH = "main"

# In-process cache: how long to remember a single GitHub commit lookup.
_CHECK_TTL_SECONDS = 60 * 60  # 1 hour
_cache: dict[str, Any] = {"checked_at": 0.0, "result": None}


def _package_root() -> Path:
    """Best-effort path to the *checkout* the package was installed from."""
    # claude_oneclick/__init__.py lives at <repo>/claude_oneclick/__init__.py
    return Path(claude_oneclick.__file__).resolve().parent.parent


def _run(cmd: list[str], cwd: Path | None = None, timeout: float = 120.0) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            cmd, cwd=str(cwd) if cwd else None,
            capture_output=True, text=True, check=False, timeout=timeout,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except FileNotFoundError as e:
        return 127, "", str(e)
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"


def _is_git_checkout(root: Path) -> bool:
    return (root / ".git").exists()


def _local_head_sha(root: Path) -> str | None:
    if not _is_git_checkout(root):
        return None
    rc, out, _ = _run(["git", "rev-parse", "HEAD"], cwd=root, timeout=10)
    return out if rc == 0 else None


def _local_dirty(root: Path) -> bool:
    rc, out, _ = _run(["git", "status", "--porcelain"], cwd=root, timeout=10)
    return rc == 0 and bool(out)


def _remote_head_sha() -> tuple[str | None, str | None]:
    """Returns (sha, commit_message_first_line) of the latest commit on the
    configured remote branch. Pulls from the public GitHub REST API — no
    auth needed for public repos.
    """
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/commits/{DEFAULT_BRANCH}"
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": f"claude-oneclick/{claude_oneclick.__version__}",
    })
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode("utf-8"))
            sha = data.get("sha")
            msg = ((data.get("commit") or {}).get("message") or "").splitlines()[0:1]
            return sha, (msg[0] if msg else "")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ConnectionError, json.JSONDecodeError):
        return None, None


def check(*, force: bool = False) -> dict[str, Any]:
    """Lightweight check. Result is cached for 1 hour unless ``force=True``.

    Returns a dict shaped like:

        {
          "is_git": bool,
          "current_sha": "abc1234..." | None,
          "latest_sha":  "def5678..." | None,
          "latest_message": "Add foo" | None,
          "has_update":  bool,
          "dirty":       bool,    # local working tree has uncommitted edits
          "checked_at":  unix_ts,
          "package_root": "/home/.../claude-oneclick",
          "version":     "0.1.0",
          "error":       "..." | None,
        }
    """
    if not force and _cache["result"] is not None:
        if time.time() - _cache["checked_at"] < _CHECK_TTL_SECONDS:
            return dict(_cache["result"])

    root = _package_root()
    is_git = _is_git_checkout(root)
    current = _local_head_sha(root)
    dirty = _local_dirty(root) if is_git else False
    latest, msg = (None, None)
    error: str | None = None
    if is_git and current:
        latest, msg = _remote_head_sha()
        if latest is None:
            error = "Couldn't reach github.com to compare versions."
    elif not is_git:
        error = "Not a git checkout — install via the repo (git clone …) to enable updates."

    has_update = bool(is_git and current and latest and current != latest)
    result = {
        "is_git": is_git,
        "current_sha": current,
        "latest_sha": latest,
        "latest_message": msg,
        "has_update": has_update,
        "dirty": dirty,
        "checked_at": time.time(),
        "package_root": str(root),
        "version": claude_oneclick.__version__,
        "error": error,
    }
    _cache["result"] = result
    _cache["checked_at"] = time.time()
    return dict(result)


def apply() -> dict[str, Any]:
    """Run ``git pull --ff-only`` then ``pip install --user -e .``.

    Refuses if the working tree is dirty. Returns a dict with stdout/stderr
    so the UI can show what happened.
    """
    root = _package_root()
    if not _is_git_checkout(root):
        return {"ok": False, "error": "Not a git checkout — can't self-update."}
    if _local_dirty(root):
        return {"ok": False, "error": "Working tree has local edits. Commit or stash them first."}

    rc1, out1, err1 = _run(["git", "pull", "--ff-only"], cwd=root, timeout=60)
    if rc1 != 0:
        return {"ok": False, "error": "git pull failed: " + (err1 or out1), "step": "pull"}

    rc2, out2, err2 = _run(
        [sys.executable, "-m", "pip", "install", "--user", "-e", str(root)],
        cwd=root, timeout=180,
    )
    if rc2 != 0:
        return {
            "ok": False, "error": "pip install failed: " + (err2 or out2),
            "step": "pip", "pull_output": out1,
        }

    # Bust the cache so the next /api/update/check shows up-to-date.
    _cache["result"] = None
    _cache["checked_at"] = 0.0

    return {
        "ok": True,
        "pull_output": out1,
        "pip_output": out2,
        "current_sha": _local_head_sha(root),
    }
