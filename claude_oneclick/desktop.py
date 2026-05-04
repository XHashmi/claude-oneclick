"""Claude Desktop developer-mode config manager.

The Claude Desktop app added a developer menu that lets you point it at
a custom API endpoint. Enabling it disables some chat features (the
user confirmed this) — so we treat it as opt-in *per surface*, with a
guaranteed-clean revert.

How we find the config
----------------------

Electron apps store user data under platform-conventional dirs:

  macOS:   ~/Library/Application Support/Claude/
  Windows: %APPDATA%\\Claude\\
  Linux:   ~/.config/Claude/

The actual filename + JSON-key path inside it depends on which Claude
Desktop version added the developer menu. We try a few plausible
locations in order. If none match, the user can pin the path manually
in config.json under ``claude_desktop.config_path`` /
``claude_desktop.endpoint_key`` and we'll honor that.

Safety rails
------------

* Before any write we copy the existing file to ``<file>.coc-backup``.
  ``disable()`` restores from that backup byte-for-byte. We never edit
  a file we didn't first back up.
* If the config file doesn't exist, ``enable()`` creates one containing
  *only* our keys. ``disable()`` then deletes it (clean uninstall).
* Inside the JSON we manage one well-known key path; everything else
  is left untouched.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
from pathlib import Path
from typing import Any

from claude_oneclick.config import load


# Candidate filenames in the Claude Desktop user-data dir.
# Tried in order; first existing file wins. Order chosen from what the
# Claude Desktop "Developer" menu exposes:
#   - "Open Developer Config File..." -> developer-config.json (most likely)
#   - "Open App Config File..."        -> claude_desktop_config.json or
#                                         config.json
# plus a few permissive fallbacks for older / future filenames.
_CANDIDATE_FILENAMES = (
    "developer-config.json",
    "developer_config.json",
    "developerConfig.json",
    "claude_desktop_config.json",
    "config.json",
    "settings.json",
)

# JSON-key path (dot-separated) we'll write our endpoint into.
# The Developer menu calls this feature "Configure Third-Party Inference",
# so we prefer that key name first. Override-able via config.json:
#   "claude_desktop": {
#     "config_path": "/explicit/path/to/file.json",
#     "endpoint_key": "thirdPartyInference.endpoint"
#   }
_DEFAULT_KEY = "thirdPartyInference.endpoint"


def _user_data_dir() -> Path:
    s = platform.system()
    home = Path.home()
    if s == "Darwin":
        return home / "Library/Application Support/Claude"
    if s == "Windows":
        appdata = os.environ.get("APPDATA") or str(home / "AppData/Roaming")
        return Path(appdata) / "Claude"
    return home / ".config/Claude"


def _candidate_paths(override: str | None) -> list[Path]:
    if override:
        return [Path(override).expanduser()]
    base = _user_data_dir()
    return [base / name for name in _CANDIDATE_FILENAMES]


def _resolve_existing_path(override: str | None) -> Path | None:
    for p in _candidate_paths(override):
        if p.exists():
            return p
    return None


def _resolve_target_path(override: str | None) -> Path:
    """Where to write when no existing file is found."""
    if override:
        return Path(override).expanduser()
    return _user_data_dir() / _CANDIDATE_FILENAMES[0]


def _settings() -> tuple[str | None, str]:
    """User-overridable bits, pulled from claude_oneclick config."""
    cfg = load()
    cd = cfg.get("claude_desktop", {})
    return cd.get("config_path"), (cd.get("endpoint_key") or _DEFAULT_KEY)


# ---------- read/write helpers ---------------------------------------------

def _set_path(d: dict[str, Any], key: str, value: Any) -> None:
    """``set_path({}, "a.b.c", 1) -> {"a": {"b": {"c": 1}}}``"""
    parts = key.split(".")
    node = d
    for p in parts[:-1]:
        if not isinstance(node.get(p), dict):
            node[p] = {}
        node = node[p]
    node[parts[-1]] = value


def _del_path(d: dict[str, Any], key: str) -> None:
    parts = key.split(".")
    node = d
    stack: list[tuple[dict[str, Any], str]] = []
    for p in parts[:-1]:
        if not isinstance(node.get(p), dict):
            return
        stack.append((node, p))
        node = node[p]
    node.pop(parts[-1], None)
    # Garbage-collect empty intermediate dicts so we don't leave litter.
    for parent, k in reversed(stack):
        if isinstance(parent.get(k), dict) and not parent[k]:
            parent.pop(k, None)


# ---------- public API -----------------------------------------------------

def status() -> dict[str, Any]:
    override, key = _settings()
    existing = _resolve_existing_path(override)
    enabled = False
    detail = ""
    if existing and existing.exists():
        try:
            data = json.loads(existing.read_text(encoding="utf-8"))
            cur = data
            for part in key.split("."):
                cur = cur.get(part) if isinstance(cur, dict) else None
                if cur is None:
                    break
            enabled = bool(cur)
            detail = f"endpoint = {cur}" if cur else "no override set"
        except Exception as e:
            detail = f"couldn't parse: {e}"
    else:
        detail = "Claude Desktop config file not found"
    return {
        "platform": platform.system(),
        "user_data_dir": str(_user_data_dir()),
        "config_path": str(existing) if existing else None,
        "endpoint_key": key,
        "enabled": enabled,
        "detail": detail,
    }


def enable(base_url: str) -> dict[str, Any]:
    """Point Claude Desktop at ``base_url``. Backs up any existing config."""
    override, key = _settings()
    target = _resolve_existing_path(override) or _resolve_target_path(override)
    target.parent.mkdir(parents=True, exist_ok=True)

    # Back up before any edit.
    backup = target.with_suffix(target.suffix + ".coc-backup")
    if target.exists() and not backup.exists():
        shutil.copy2(target, backup)

    if target.exists():
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("root is not a JSON object")
        except Exception as e:
            return {"ok": False, "error": f"can't parse {target}: {e}"}
    else:
        data = {}
        # Mark fresh-create so disable() can delete cleanly.
        marker = target.with_suffix(target.suffix + ".coc-fresh")
        marker.write_text("created by claude-oneclick", encoding="utf-8")

    _set_path(data, key, base_url)
    target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass

    return {"ok": True, "config_path": str(target), "endpoint_key": key, "endpoint": base_url}


def disable() -> dict[str, Any]:
    """Reverse ``enable``: restore backup, or delete if we created the file."""
    override, key = _settings()
    target = _resolve_existing_path(override)
    if not target:
        return {"ok": True, "detail": "no Claude Desktop config to revert"}

    backup = target.with_suffix(target.suffix + ".coc-backup")
    fresh_marker = target.with_suffix(target.suffix + ".coc-fresh")

    if fresh_marker.exists():
        # We created the file from scratch; safe to delete it whole.
        try:
            target.unlink()
            fresh_marker.unlink()
            return {"ok": True, "detail": f"removed {target}"}
        except OSError as e:
            return {"ok": False, "error": str(e)}

    if backup.exists():
        try:
            shutil.copy2(backup, target)
            backup.unlink()
            return {"ok": True, "detail": f"restored {target} from backup"}
        except OSError as e:
            return {"ok": False, "error": str(e)}

    # No backup, no fresh marker — best we can do is strip our key.
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            _del_path(data, key)
            target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return {"ok": True, "detail": f"stripped {key} from {target}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
