"""Dispatcher that wires `config.json` state into the OS.

Single entry point: ``apply_state()``. Reads current config + enabled flag,
computes the env map, and writes it to:

* shell rc files (POSIX) **or** ``setx`` + PowerShell ``$PROFILE`` +
  cmd.exe AutoRun (Windows)
* every VSCode user ``settings.json`` we can find
* the proxy daemon (start/stop/restart as required)
"""
from __future__ import annotations

import platform
from typing import Any

from claude_oneclick import shell, vscode, windows
from claude_oneclick.config import get_preset, load


def _is_windows() -> bool:
    return platform.system() == "Windows"


def compute_env(cfg: dict[str, Any] | None = None) -> dict[str, str]:
    """The env-var map that should currently be in effect."""
    cfg = cfg or load()
    enabled = bool(cfg.get("enabled"))
    active = cfg.get("active") or "anthropic"
    if not enabled or active == "anthropic":
        return {}
    preset = get_preset(active, cfg) or {}
    proxy = cfg.get("proxy", {})
    return shell.env_for_preset(
        preset,
        proxy.get("host", "127.0.0.1"),
        int(proxy.get("port", 47824)),
        skip_vscode_login=bool(cfg.get("skip_vscode_login", True)),
    )


def apply_state(*, manage_proxy: bool = True) -> dict[str, Any]:
    """Recompute everything OS-side from the current config.

    Returns a small status dict suitable for logging or returning from the
    UI's ``/api/state`` endpoint.
    """
    cfg = load()
    env = compute_env(cfg)

    # 1. Env files
    if _is_windows():
        windows.write_env_files(env)
        windows.apply_user_env(env)
    else:
        # Always rewrite env.sh so the toggle state is reflected even
        # when nothing exports.
        shell.write_env_sh()

    # 2. VSCode
    try:
        vscode_paths = vscode.apply_env(env)
    except Exception:
        vscode_paths = []

    # 3. Proxy lifecycle
    proxy_status: str = "off"
    if manage_proxy:
        # Imported lazily to keep CLI startup snappy and to avoid a hard
        # dependency loop with proxy.py (which imports config).
        from claude_oneclick import proxy

        active = cfg.get("active") or "anthropic"
        preset = get_preset(active, cfg) or {}
        fmt = (preset.get("format") or "openai").lower()
        want_proxy = bool(cfg.get("enabled")) and fmt == "openai" and active != "anthropic"
        if want_proxy:
            proxy.ensure_running()
            proxy_status = "running"
        else:
            proxy.stop()
            proxy_status = "off"

    return {
        "env": env,
        "vscode_settings_touched": [str(p) for p in vscode_paths],
        "proxy": proxy_status,
        "platform": platform.system(),
    }
