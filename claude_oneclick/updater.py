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


def _read_buildinfo_sha() -> str | None:
    """SHA stamped by `_post_install` — works for ZIP installs too."""
    try:
        from claude_oneclick import _buildinfo  # type: ignore
        sha = getattr(_buildinfo, "COMMIT_SHA", None)
        return sha if isinstance(sha, str) and sha else None
    except ImportError:
        return None


def _local_head_sha(root: Path) -> str | None:
    """Best available local SHA: live git first, then the install stamp."""
    if _is_git_checkout(root):
        rc, out, _ = _run(["git", "rev-parse", "HEAD"], cwd=root, timeout=10)
        if rc == 0 and out:
            return out
    return _read_buildinfo_sha()


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
    has_buildinfo = _read_buildinfo_sha() is not None
    current = _local_head_sha(root)
    dirty = _local_dirty(root) if is_git else False
    latest, msg = (None, None)
    error: str | None = None

    # We can compare versions if we have ANY local SHA — git OR buildinfo.
    if current:
        latest, msg = _remote_head_sha()
        if latest is None:
            error = "Couldn't reach github.com to compare versions."
    else:
        error = ("No local version stamp found. Re-run `claude-oneclick "
                 "_post_install` to pin the current install.")

    has_update = bool(current and latest and current != latest)
    # Decide which update method we'd use.
    update_method = "git" if is_git else ("zip" if has_buildinfo else "none")

    result = {
        "is_git": is_git,
        "has_buildinfo": has_buildinfo,
        "update_method": update_method,
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
    """Update the install. Two paths:

    * Git checkout: ``git pull --ff-only`` + ``pip install --user -e .``
    * ZIP install: download the latest branch ZIP from GitHub, extract over
      the install directory, then ``pip install --user -e .``

    For the ZIP path we never delete files we don't recognize — only files
    that ALSO appear in the new tree get overwritten. So the user's
    ``_buildinfo.py``, runtime caches, etc. survive.
    """
    root = _package_root()
    if _is_git_checkout(root):
        return _apply_git(root)
    if _read_buildinfo_sha() is not None or _package_root().exists():
        return _apply_zip(root)
    return {"ok": False, "error": "Don't know how to update this install."}


def _apply_git(root: Path) -> dict[str, Any]:
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
        return {"ok": False, "error": "pip install failed: " + (err2 or out2),
                "step": "pip", "pull_output": out1}
    _restamp(root)
    _cache["result"] = None; _cache["checked_at"] = 0.0
    return {"ok": True, "pull_output": out1, "pip_output": out2,
            "current_sha": _local_head_sha(root), "method": "git"}


def _apply_zip(root: Path) -> dict[str, Any]:
    """Download main.zip from GitHub, extract over the install."""
    import io
    import shutil
    import tempfile
    import zipfile

    url = f"https://codeload.github.com/{GITHUB_OWNER}/{GITHUB_REPO}/zip/refs/heads/{DEFAULT_BRANCH}"
    try:
        req = urllib.request.Request(url, headers={
            "Accept": "application/zip",
            "User-Agent": f"claude-oneclick/{claude_oneclick.__version__}",
        })
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
    except Exception as e:
        return {"ok": False, "error": f"Couldn't download {url}: {e}", "step": "download"}

    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        names = zf.namelist()
        if not names:
            return {"ok": False, "error": "Downloaded ZIP was empty", "step": "extract"}
        # ZIPs from GitHub start with `<repo>-<branch>/`. Strip that prefix
        # so files land at the install root.
        common = names[0].split("/", 1)[0] + "/"
        if not all(n.startswith(common) for n in names):
            return {"ok": False, "error": "Unexpected ZIP layout — refusing to extract", "step": "extract"}

        with tempfile.TemporaryDirectory(prefix="coc-update-") as tmp:
            tmp_path = Path(tmp)
            zf.extractall(tmp_path)
            new_root = tmp_path / common.rstrip("/")
            if not new_root.exists():
                return {"ok": False, "error": "ZIP didn't expand to the expected path", "step": "extract"}

            # Copy each file into the install root, creating dirs as needed.
            # Skip dotfiles like .git (none in a branch ZIP anyway) and our
            # own runtime artifacts.
            SKIP_NAMES = {"__pycache__", ".pytest_cache", "_buildinfo.py"}
            copied = 0
            for src in new_root.rglob("*"):
                if any(part in SKIP_NAMES for part in src.parts):
                    continue
                if src.is_dir():
                    continue
                rel = src.relative_to(new_root)
                dst = root / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                copied += 1
    except zipfile.BadZipFile as e:
        return {"ok": False, "error": f"Downloaded ZIP was corrupt: {e}", "step": "extract"}
    except Exception as e:
        return {"ok": False, "error": f"Extract failed: {e}", "step": "extract"}

    # Refresh the editable install in case pyproject.toml changed.
    rc, out, err = _run(
        [sys.executable, "-m", "pip", "install", "--user", "-e", str(root)],
        cwd=root, timeout=180,
    )
    if rc != 0:
        return {"ok": False, "error": "pip install failed: " + (err or out),
                "step": "pip", "files_copied": copied}

    sha = _restamp(root)
    _cache["result"] = None; _cache["checked_at"] = 0.0
    return {"ok": True, "files_copied": copied, "pip_output": out,
            "current_sha": sha, "method": "zip"}


def _restamp(root: Path) -> str | None:
    """Rewrite _buildinfo.py with whatever commit we just pulled."""
    import time as _t
    sha = _local_head_sha(root) or _remote_head_sha()[0]
    if not sha:
        return None
    bi = root / "claude_oneclick" / "_buildinfo.py"
    bi.write_text(
        '# Auto-generated by claude-oneclick updater. Not git-tracked.\n'
        f'COMMIT_SHA = {sha!r}\n'
        f'INSTALLED_AT = {_t.time()!r}\n',
        encoding="utf-8",
    )
    return sha
