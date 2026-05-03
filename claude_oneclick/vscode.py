"""VSCode user settings.json integration.

We surgically edit (or create) the user-scope `settings.json` to set
`terminal.integrated.env.linux` / `.osx` / `.windows`. The Claude Code
VSCode extension spawns child processes through that env, so this is what
makes the toggle apply inside VSCode without restarting the editor.

We always write a single managed key under each platform map:

    "terminal.integrated.env.linux": {
        "ANTHROPIC_BASE_URL": "...",
        ...
    }

On uninstall (or when toggle is OFF) we strip the keys we own.
"""
from __future__ import annotations

import json
import platform
import re
from pathlib import Path
from typing import Any

# Keys we manage. Anything outside this set is left untouched.
MANAGED_KEYS = {
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_SMALL_FAST_MODEL",
    "CLAUDE_CODE_SKIP_LOGIN",
}


def _settings_paths() -> list[Path]:
    """Best-effort locations of the user-scope VSCode settings.json.

    We touch *all* that exist (Code, Code - Insiders, VSCodium) so the
    toggle applies regardless of which build the user runs.
    """
    home = Path.home()
    candidates: list[Path] = []
    sysname = platform.system()
    if sysname == "Linux":
        roots = [home / ".config/Code/User", home / ".config/Code - Insiders/User", home / ".config/VSCodium/User"]
    elif sysname == "Darwin":
        base = home / "Library/Application Support"
        roots = [base / "Code/User", base / "Code - Insiders/User", base / "VSCodium/User"]
    else:
        # Windows: %APPDATA%/Code/User
        appdata = Path(home / "AppData/Roaming")
        roots = [appdata / "Code/User", appdata / "Code - Insiders/User", appdata / "VSCodium/User"]
    for r in roots:
        candidates.append(r / "settings.json")
    return candidates


def _platform_env_key() -> str:
    s = platform.system()
    if s == "Darwin":
        return "terminal.integrated.env.osx"
    if s == "Windows":
        return "terminal.integrated.env.windows"
    return "terminal.integrated.env.linux"


# ---------- forgiving JSONC parser/writer ------------------------------------
# VSCode uses JSON-with-comments. We strip comments to parse, but we don't
# rewrite formatting wholesale — we read/modify/write the dict and accept
# losing comments, which matches how VSCode itself rewrites the file.

_LINE_COMMENT = re.compile(r"//[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")


def _parse_jsonc(text: str) -> dict[str, Any]:
    if not text.strip():
        return {}
    no_block = _BLOCK_COMMENT.sub("", text)
    no_line = _LINE_COMMENT.sub("", no_block)
    cleaned = _TRAILING_COMMA.sub(r"\1", no_line)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        # Settings file has more exotic JSONC than we can handle. Bail
        # rather than corrupt it.
        raise
    if not isinstance(data, dict):
        return {}
    return data


def _dump(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=4) + "\n"


# ---------- public API -------------------------------------------------------

def apply_env(env: dict[str, str]) -> list[Path]:
    """Write our env vars into every existing VSCode user settings.json.

    Empty `env` → strip the managed keys (used when toggle is OFF).
    Returns the paths actually touched.
    """
    touched: list[Path] = []
    plat_key = _platform_env_key()
    for path in _settings_paths():
        if not path.parent.exists():
            continue
        if not path.exists():
            # Don't create settings.json for VSCode flavors that aren't
            # actually installed.
            continue
        try:
            text = path.read_text(encoding="utf-8")
            data = _parse_jsonc(text)
        except Exception:
            continue

        existing = data.get(plat_key) or {}
        if not isinstance(existing, dict):
            existing = {}

        # Strip our managed keys.
        for k in list(existing.keys()):
            if k in MANAGED_KEYS:
                del existing[k]

        # Add fresh values.
        for k, v in env.items():
            existing[k] = v

        if existing:
            data[plat_key] = existing
        elif plat_key in data:
            del data[plat_key]

        new_text = _dump(data)
        if new_text != text:
            path.write_text(new_text, encoding="utf-8")
            touched.append(path)
    return touched


def remove_all() -> list[Path]:
    """Strip every managed key from every settings.json we can find."""
    return apply_env({})
