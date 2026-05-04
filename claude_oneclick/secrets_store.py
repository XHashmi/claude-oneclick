"""OS-keychain storage for API keys.

Replaces the plaintext-in-config.json default for users who'd rather
keep their provider keys in their OS credentials store.

Three backends, picked at runtime by ``platform.system()`` and the
presence of the right CLI:

* **macOS** — ``security add-generic-password`` /
  ``security find-generic-password``. Always available on macOS.
* **Linux** — ``secret-tool`` from libsecret-tools. Available in
  GNOME/KDE installs by default; absent on minimal headless boxes.
  When unavailable, ``available()`` returns False and callers fall
  back to plaintext.
* **Windows** — ``cmdkey /generic:...`` / direct PowerShell call to
  ``[System.Net.NetworkCredential]`` for retrieval, since cmdkey
  doesn't print passwords back. Available on every Windows since 7.

We store one entry per provider *group* (``DeepSeek``, ``NVIDIA NIMs``,
``Other hosted``, etc.) since keys are per-account, not per-preset.
Service name: ``claude-oneclick``. Account: the group name.

Caller contract: ``set(group, key)`` writes; ``get(group)`` reads;
``delete(group)`` clears; ``available()`` reports whether the
backend is usable. Errors raise ``KeychainError``.
"""
from __future__ import annotations

import platform
import shutil
import subprocess
from typing import Any


SERVICE = "claude-oneclick"


class KeychainError(RuntimeError):
    pass


def _run(cmd: list[str], *, input_text: str | None = None) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            cmd, input=input_text, capture_output=True, text=True,
            check=False, timeout=10,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError as e:
        raise KeychainError(f"backend not installed: {e}")
    except subprocess.TimeoutExpired:
        raise KeychainError(f"keychain backend timed out: {' '.join(cmd[:1])}")


# ---------- macOS (Keychain) ------------------------------------------------

def _mac_set(group: str, key: str) -> None:
    # `-U` updates if exists. `-w` is the password value (last so it's hidden
    # in argv listings less than via env, but on macOS this is the standard
    # path the system uses).
    rc, _, err = _run([
        "security", "add-generic-password", "-U",
        "-s", SERVICE, "-a", group, "-w", key,
    ])
    if rc != 0:
        raise KeychainError(f"security add-generic-password failed: {err.strip()}")


def _mac_get(group: str) -> str | None:
    rc, out, _ = _run([
        "security", "find-generic-password", "-s", SERVICE, "-a", group, "-w",
    ])
    if rc != 0:
        return None
    return out.strip("\n")


def _mac_delete(group: str) -> bool:
    rc, _, _ = _run([
        "security", "delete-generic-password", "-s", SERVICE, "-a", group,
    ])
    return rc == 0


def _mac_available() -> bool:
    return shutil.which("security") is not None


# ---------- Linux (libsecret) ----------------------------------------------

def _linux_set(group: str, key: str) -> None:
    rc, _, err = _run(
        ["secret-tool", "store", "--label", f"{SERVICE} ({group})",
         "service", SERVICE, "account", group],
        input_text=key,
    )
    if rc != 0:
        raise KeychainError(f"secret-tool store failed: {err.strip()}")


def _linux_get(group: str) -> str | None:
    rc, out, _ = _run(
        ["secret-tool", "lookup", "service", SERVICE, "account", group],
    )
    if rc != 0:
        return None
    val = out.rstrip("\n")
    return val or None


def _linux_delete(group: str) -> bool:
    rc, _, _ = _run(
        ["secret-tool", "clear", "service", SERVICE, "account", group],
    )
    return rc == 0


def _linux_available() -> bool:
    return shutil.which("secret-tool") is not None


# ---------- Windows (Credential Manager) -----------------------------------

def _win_set(group: str, key: str) -> None:
    target = f"{SERVICE}/{group}"
    rc, _, err = _run([
        "cmdkey", f"/generic:{target}", f"/user:{group}", f"/pass:{key}",
    ])
    if rc != 0:
        raise KeychainError(f"cmdkey failed: {err.strip()}")


def _win_get(group: str) -> str | None:
    """Read a credential by P/Invoking advapi32!CredRead via PowerShell.

    The previous implementation relied on the third-party CredentialManager
    PS module, which isn't installed on stock Windows — meaning any key
    saved via cmdkey was effectively un-readable on the same machine.
    This version uses only built-in Windows APIs (advapi32.dll), so it
    works on every Windows 10/11 install with no extra modules.
    """
    target = f"{SERVICE}/{group}".replace("'", "''")
    ps = (
        "Add-Type -Namespace CocCred -Name Native -MemberDefinition @\"\n"
        "  [DllImport(\"advapi32.dll\", SetLastError=true, CharSet=CharSet.Unicode)]\n"
        "  public static extern bool CredRead(string target, int type, int flags, out IntPtr cred);\n"
        "  [DllImport(\"advapi32.dll\")]\n"
        "  public static extern void CredFree(IntPtr buffer);\n"
        "  [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]\n"
        "  public struct CREDENTIAL {\n"
        "    public uint Flags; public uint Type;\n"
        "    public string TargetName; public string Comment;\n"
        "    public System.Runtime.InteropServices.ComTypes.FILETIME LastWritten;\n"
        "    public uint CredentialBlobSize; public IntPtr CredentialBlob;\n"
        "    public uint Persist; public uint AttributeCount; public IntPtr Attributes;\n"
        "    public string TargetAlias; public string UserName;\n"
        "  }\n"
        "\"@;\n"
        f"$ptr=[IntPtr]::Zero;$ok=[CocCred.Native]::CredRead('{target}',1,0,[ref]$ptr);"
        "if(-not $ok){exit 1};"
        "try{"
        "  $c=[System.Runtime.InteropServices.Marshal]::PtrToStructure($ptr,[type]'CocCred.Native+CREDENTIAL');"
        "  if($c.CredentialBlobSize -eq 0){''}else{"
        "    [System.Runtime.InteropServices.Marshal]::PtrToStringUni($c.CredentialBlob,[int]($c.CredentialBlobSize/2))"
        "  }"
        "}finally{[CocCred.Native]::CredFree($ptr)}"
    )
    rc, out, _ = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps])
    if rc != 0:
        return None
    val = out.strip("\r\n").strip()
    return val or None


def _win_delete(group: str) -> bool:
    target = f"{SERVICE}/{group}"
    rc, _, _ = _run(["cmdkey", f"/delete:{target}"])
    return rc == 0


def _win_available() -> bool:
    return shutil.which("cmdkey") is not None


# ---------- dispatcher ------------------------------------------------------

def _backend() -> str:
    s = platform.system()
    if s == "Darwin": return "mac"
    if s == "Linux": return "linux"
    if s == "Windows": return "win"
    return "none"


def available() -> bool:
    b = _backend()
    if b == "mac":   return _mac_available()
    if b == "linux": return _linux_available()
    if b == "win":   return _win_available()
    return False


def describe() -> str:
    b = _backend()
    return {"mac": "macOS Keychain", "linux": "GNOME/KDE Secret Service (libsecret)",
            "win": "Windows Credential Manager"}.get(b, "(none)")


def set(group: str, key: str) -> None:  # noqa: A001 (shadowing builtin: tiny module API)
    b = _backend()
    if b == "mac":   return _mac_set(group, key)
    if b == "linux": return _linux_set(group, key)
    if b == "win":   return _win_set(group, key)
    raise KeychainError("no keychain backend on this OS")


def get(group: str) -> str | None:
    b = _backend()
    if b == "mac":   return _mac_get(group)
    if b == "linux": return _linux_get(group)
    if b == "win":   return _win_get(group)
    return None


def delete(group: str) -> bool:
    b = _backend()
    if b == "mac":   return _mac_delete(group)
    if b == "linux": return _linux_delete(group)
    if b == "win":   return _win_delete(group)
    return False
