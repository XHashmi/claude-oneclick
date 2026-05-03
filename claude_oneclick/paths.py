"""Filesystem paths used across the package."""
from __future__ import annotations

import os
from pathlib import Path


def config_dir() -> Path:
    """~/.config/claude-oneclick (respects XDG_CONFIG_HOME)."""
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "claude-oneclick"


def config_file() -> Path:
    return config_dir() / "config.json"


def env_file() -> Path:
    """The single shell-sourced file. Empty when toggle is OFF."""
    return config_dir() / "env.sh"


def pid_file() -> Path:
    """PID of the running translation proxy, if any."""
    return config_dir() / "proxy.pid"


def proxy_log() -> Path:
    return config_dir() / "proxy.log"


def server_pid_file() -> Path:
    return config_dir() / "ui.pid"


def ensure_dirs() -> None:
    config_dir().mkdir(parents=True, exist_ok=True)


# Marker lines used when injecting/removing our block from external files
# (shell rc, VSCode settings). Keep these stable — they're the contract for
# clean uninstalls.
SHELL_BEGIN = "# >>> claude-oneclick >>>"
SHELL_END = "# <<< claude-oneclick <<<"
VSCODE_KEY = "claude-oneclick"
