"""Per-OS "start at login / boot" hooks.

Three backends, picked by ``platform.system()``:

* **Linux** — drops a ``.desktop`` file into ``~/.config/autostart/``.
  Every freedesktop session manager (GNOME, KDE, Xfce, Cinnamon, …)
  honors that directory at login.
* **macOS** — writes a LaunchAgent plist to
  ``~/Library/LaunchAgents/com.claude-oneclick.boot.plist`` and runs
  ``launchctl load -w`` to register it. Reverses with ``launchctl unload``
  + plist delete.
* **Windows** — writes a single value under
  ``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run`` named
  ``ClaudeOneClick``. The Windows shell processes that key for the
  current user at every interactive logon.

What actually runs at boot: ``python -m claude_oneclick _boot``. That
command re-applies the saved state — re-exports env vars (Windows) and
starts the proxy if the toggle was ON when the user last shut down. It
exits immediately; nothing keeps running in the background unless the
proxy daemon needed to come up.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


def _python_cmd() -> str:
    return sys.executable or "python3"


# ---------- Linux ----------------------------------------------------------

LINUX_AUTOSTART_DIR = Path.home() / ".config/autostart"
LINUX_AUTOSTART_FILE = LINUX_AUTOSTART_DIR / "claude-oneclick.desktop"


def _linux_desktop() -> str:
    py = _python_cmd()
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Claude OneClick (boot hook)\n"
        "Comment=Re-apply Claude OneClick env + proxy at login\n"
        f"Exec={py} -m claude_oneclick _boot\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
        "Hidden=false\n"
        "NoDisplay=true\n"
    )


def _linux_enable() -> bool:
    LINUX_AUTOSTART_DIR.mkdir(parents=True, exist_ok=True)
    LINUX_AUTOSTART_FILE.write_text(_linux_desktop(), encoding="utf-8")
    LINUX_AUTOSTART_FILE.chmod(0o644)
    return True


def _linux_disable() -> bool:
    if LINUX_AUTOSTART_FILE.exists():
        LINUX_AUTOSTART_FILE.unlink()
        return True
    return False


def _linux_is_enabled() -> bool:
    return LINUX_AUTOSTART_FILE.exists()


# ---------- macOS ----------------------------------------------------------

MAC_PLIST = Path.home() / "Library/LaunchAgents/com.claude-oneclick.boot.plist"
MAC_LABEL = "com.claude-oneclick.boot"


def _mac_plist_xml() -> str:
    py = _python_cmd()
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        '<dict>\n'
        f'  <key>Label</key><string>{MAC_LABEL}</string>\n'
        '  <key>ProgramArguments</key>\n'
        '  <array>\n'
        f'    <string>{py}</string>\n'
        '    <string>-m</string>\n'
        '    <string>claude_oneclick</string>\n'
        '    <string>_boot</string>\n'
        '  </array>\n'
        '  <key>RunAtLoad</key><true/>\n'
        '  <key>KeepAlive</key><false/>\n'
        '  <key>StandardOutPath</key>\n'
        f'  <string>{Path.home() / ".config/claude-oneclick/boot.log"}</string>\n'
        '  <key>StandardErrorPath</key>\n'
        f'  <string>{Path.home() / ".config/claude-oneclick/boot.log"}</string>\n'
        '</dict>\n'
        '</plist>\n'
    )


def _mac_enable() -> bool:
    MAC_PLIST.parent.mkdir(parents=True, exist_ok=True)
    MAC_PLIST.write_text(_mac_plist_xml(), encoding="utf-8")
    MAC_PLIST.chmod(0o644)
    # `launchctl load -w` makes it persist across reboots. Best-effort:
    # if launchctl isn't on PATH (very rare), the plist still loads at
    # next login because that's also when launchd scans the directory.
    if shutil.which("launchctl"):
        subprocess.run(
            ["launchctl", "load", "-w", str(MAC_PLIST)],
            check=False, capture_output=True,
        )
    return True


def _mac_disable() -> bool:
    if MAC_PLIST.exists():
        if shutil.which("launchctl"):
            subprocess.run(
                ["launchctl", "unload", "-w", str(MAC_PLIST)],
                check=False, capture_output=True,
            )
        MAC_PLIST.unlink()
        return True
    return False


def _mac_is_enabled() -> bool:
    return MAC_PLIST.exists()


# ---------- Windows --------------------------------------------------------

WIN_RUN_KEY = "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run"
WIN_VALUE_NAME = "ClaudeOneClick"


def _win_command() -> str:
    py = _python_cmd()
    # Quote the python path because it usually lives in Program Files.
    return f'"{py}" -m claude_oneclick _boot'


def _win_enable() -> bool:
    reg = shutil.which("reg") or "reg"
    rc = subprocess.run(
        [reg, "add", WIN_RUN_KEY, "/V", WIN_VALUE_NAME,
         "/T", "REG_SZ", "/D", _win_command(), "/F"],
        check=False, capture_output=True,
    )
    return rc.returncode == 0


def _win_disable() -> bool:
    reg = shutil.which("reg") or "reg"
    rc = subprocess.run(
        [reg, "delete", WIN_RUN_KEY, "/V", WIN_VALUE_NAME, "/F"],
        check=False, capture_output=True,
    )
    return rc.returncode == 0


def _win_is_enabled() -> bool:
    reg = shutil.which("reg") or "reg"
    rc = subprocess.run(
        [reg, "query", WIN_RUN_KEY, "/V", WIN_VALUE_NAME],
        check=False, capture_output=True,
    )
    return rc.returncode == 0


# ---------- dispatcher -----------------------------------------------------

def enable() -> bool:
    s = platform.system()
    if s == "Linux": return _linux_enable()
    if s == "Darwin": return _mac_enable()
    if s == "Windows": return _win_enable()
    return False


def disable() -> bool:
    s = platform.system()
    if s == "Linux": return _linux_disable()
    if s == "Darwin": return _mac_disable()
    if s == "Windows": return _win_disable()
    return False


def is_enabled() -> bool:
    s = platform.system()
    if s == "Linux": return _linux_is_enabled()
    if s == "Darwin": return _mac_is_enabled()
    if s == "Windows": return _win_is_enabled()
    return False


def describe() -> str:
    """Human-readable description of where the autostart entry lives."""
    s = platform.system()
    if s == "Linux": return f"~/.config/autostart/claude-oneclick.desktop"
    if s == "Darwin": return f"~/Library/LaunchAgents/com.claude-oneclick.boot.plist"
    if s == "Windows": return f"{WIN_RUN_KEY}\\{WIN_VALUE_NAME}"
    return "(unsupported platform)"
