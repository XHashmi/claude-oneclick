"""One-click launchers for every supported platform.

The generated launchers all run the same thing — ``python -m claude_oneclick
ui`` — which starts the local web UI and opens the user's browser at
http://127.0.0.1:47823. We avoid native binaries on purpose:

* Linux: a freedesktop ``.desktop`` file in ``~/.local/share/applications``.
  Architecture-independent (any CPU that runs Python runs this).
* macOS: a ``claude-oneclick.command`` script the user can double-click.
  Universal across Intel and Apple Silicon.
* Windows: a ``claude-oneclick.cmd`` and a Start-Menu ``.lnk`` shortcut.
  Both are interpreted by Windows itself (cmd.exe / explorer.exe), so the
  same files work on x86, x64, and ARM64 Windows installs.

Nothing here requires admin / sudo.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from claude_oneclick.paths import config_dir, ensure_dirs


# ---------- shared ----------------------------------------------------------

def _python_cmd() -> str:
    """The python interpreter currently running us, quoted for shells."""
    return sys.executable or "python3"


# ---------- Linux .desktop --------------------------------------------------

LINUX_DESKTOP_DIR = Path.home() / ".local/share/applications"
LINUX_DESKTOP_FILE = LINUX_DESKTOP_DIR / "claude-oneclick.desktop"


def _linux_desktop_contents() -> str:
    py = _python_cmd()
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Claude OneClick\n"
        "Comment=Toggle Claude Code between Anthropic and custom model endpoints\n"
        f"Exec={py} -m claude_oneclick ui\n"
        "Icon=preferences-system-network\n"
        "Terminal=false\n"
        "Categories=Development;Utility;\n"
        "Keywords=claude;deepseek;nvidia;llm;proxy;\n"
        "StartupNotify=false\n"
    )


def install_linux_launcher() -> Path | None:
    if platform.system() != "Linux":
        return None
    LINUX_DESKTOP_DIR.mkdir(parents=True, exist_ok=True)
    LINUX_DESKTOP_FILE.write_text(_linux_desktop_contents(), encoding="utf-8")
    LINUX_DESKTOP_FILE.chmod(0o755)
    # Refresh the application menu cache if available.
    if shutil.which("update-desktop-database"):
        subprocess.run(
            ["update-desktop-database", str(LINUX_DESKTOP_DIR)],
            check=False, capture_output=True,
        )
    return LINUX_DESKTOP_FILE


def uninstall_linux_launcher() -> bool:
    if LINUX_DESKTOP_FILE.exists():
        LINUX_DESKTOP_FILE.unlink()
        return True
    return False


# ---------- macOS .command --------------------------------------------------

def macos_launcher_path() -> Path:
    return Path.home() / "Applications" / "claude-oneclick.command"


def install_macos_launcher() -> Path | None:
    if platform.system() != "Darwin":
        return None
    p = macos_launcher_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    py = _python_cmd()
    p.write_text(
        "#!/bin/bash\n"
        "set -e\n"
        f'exec "{py}" -m claude_oneclick ui\n',
        encoding="utf-8",
    )
    p.chmod(0o755)
    return p


def uninstall_macos_launcher() -> bool:
    p = macos_launcher_path()
    if p.exists():
        p.unlink()
        return True
    return False


# ---------- Windows .cmd + .lnk --------------------------------------------

def windows_cmd_path() -> Path:
    return config_dir() / "claude-oneclick.cmd"


def windows_start_menu_path() -> Path:
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData/Roaming")
    return Path(appdata) / "Microsoft/Windows/Start Menu/Programs/Claude OneClick.lnk"


def _windows_cmd_contents() -> str:
    py = _python_cmd()
    # Quote the python path for cmd.exe in case it has spaces (Program Files).
    return (
        "@echo off\r\n"
        "@rem One-click launcher for claude-oneclick. Works on x86, x64, ARM64.\r\n"
        f'"{py}" -m claude_oneclick ui %*\r\n'
    )


def install_windows_launcher() -> list[Path]:
    if platform.system() != "Windows":
        return []
    ensure_dirs()
    out: list[Path] = []
    cmd_path = windows_cmd_path()
    cmd_path.write_text(_windows_cmd_contents(), encoding="utf-8")
    out.append(cmd_path)

    # Create a Start Menu .lnk using PowerShell's WScript.Shell COM object.
    # Pure Python alternative would need pywin32; we sidestep that.
    lnk = windows_start_menu_path()
    lnk.parent.mkdir(parents=True, exist_ok=True)
    ps = (
        "$ws = New-Object -ComObject WScript.Shell;"
        f"$s = $ws.CreateShortcut('{lnk}');"
        f"$s.TargetPath = '{cmd_path}';"
        f"$s.WorkingDirectory = '{cmd_path.parent}';"
        "$s.IconLocation = 'shell32.dll,167';"
        "$s.Description = 'Toggle Claude Code between Anthropic and custom model endpoints';"
        "$s.Save();"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            check=False, capture_output=True,
        )
        if lnk.exists():
            out.append(lnk)
    except FileNotFoundError:
        # PowerShell missing on a stripped-down Windows install; the .cmd
        # launcher still works, the Start Menu entry just won't appear.
        pass
    return out


def uninstall_windows_launcher() -> list[Path]:
    out: list[Path] = []
    for p in (windows_cmd_path(), windows_start_menu_path()):
        if p.exists():
            p.unlink()
            out.append(p)
    return out


# ---------- dispatch --------------------------------------------------------

def install_for_current_os() -> list[Path]:
    s = platform.system()
    if s == "Linux":
        out = install_linux_launcher()
        return [out] if out else []
    if s == "Darwin":
        out = install_macos_launcher()
        return [out] if out else []
    if s == "Windows":
        return install_windows_launcher()
    return []


def uninstall_for_current_os() -> int:
    s = platform.system()
    n = 0
    if s == "Linux" and uninstall_linux_launcher():
        n += 1
    elif s == "Darwin" and uninstall_macos_launcher():
        n += 1
    elif s == "Windows":
        n += len(uninstall_windows_launcher())
    return n
