"""Self-diagnosis — what works, what's broken, and exactly where.

The pipeline has three nodes (Provider, Proxy, Claude Code) and two
wires (Provider↔Proxy, Proxy↔Claude). This module runs a quick health
check on each piece and reports the results in a shape the UI can
render as a wiring diagram (each piece glows green when OK, red with a
specific reason when not).

Calls are conservative — every probe has a short timeout, and we never
make a real LLM completion just to check connectivity. The provider
probe uses ``GET /v1/models`` which most providers serve cheaply.

Result shape::

    {
      "overall": "ok" | "warn" | "error",
      "active": "deepseek-v4-pro",
      "enabled": True,
      "nodes": {
        "provider": {"status": "ok"|"warn"|"error", "detail": "...", "latency_ms": 234},
        "proxy":    {"status": ..., "detail": ...},
        "claude":   {"status": ..., "detail": ...},
      },
      "wires": {
        "provider_proxy": {"status": ..., "detail": ...},
        "proxy_claude":   {"status": ..., "detail": ...},
      },
      "checked_at": 1714760000.0,
    }
"""
from __future__ import annotations

import os
import platform
import re
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from typing import Any

from claude_oneclick import discover, proxy as proxy_mod
from claude_oneclick.config import get_preset, load
from claude_oneclick.paths import env_file, proxy_log


def _now_ms() -> float:
    return time.time() * 1000.0


# ---------- node: provider --------------------------------------------------

def check_provider(preset: dict[str, Any]) -> dict[str, Any]:
    """Hit the upstream's /v1/models with the saved key. Tells us:

    - Provider reachable on the network? (DNS, TLS, firewall)
    - API key valid? (401 vs 200)
    - Model catalog populated? (count > 0)
    """
    base = preset.get("base_url") or ""
    if not base:
        return {"status": "warn", "detail": "no base_url configured for this preset"}
    if (preset.get("format") or "openai") == "anthropic":
        # Anthropic-format → no proxy involved; provider probe is just
        # whether the host resolves and a TCP connect succeeds.
        return _tcp_probe(base)
    # OpenAI-format → real /v1/models probe with the saved key.
    from claude_oneclick.config import _resolve_api_key
    api_key = _resolve_api_key(preset)
    is_local = "localhost" in base or "127.0.0.1" in base
    if not api_key and not is_local:
        return {"status": "warn", "detail": "no API key saved for this preset"}
    started = _now_ms()
    try:
        models = discover.list_models_for_preset(preset, timeout=6)
    except discover.DiscoverError as e:
        msg = str(e)
        # Explicit 401/403 = bad key; 404 = wrong base URL; everything else = network.
        if "401" in msg or "403" in msg:
            return {"status": "error", "detail": "API key rejected by provider (401/403)"}
        if "404" in msg:
            return {"status": "error", "detail": f"endpoint not found at {base}/v1/models — wrong base URL?"}
        return {"status": "error", "detail": msg[:160]}
    elapsed = _now_ms() - started
    if not models:
        return {"status": "warn", "detail": "provider returned an empty model list", "latency_ms": elapsed}
    return {
        "status": "ok",
        "detail": f"{len(models)} models available",
        "latency_ms": round(elapsed, 1),
    }


def _tcp_probe(base_url: str) -> dict[str, Any]:
    """Cheap reachability check for Anthropic-format presets."""
    import urllib.parse
    try:
        u = urllib.parse.urlparse(base_url)
        host = u.hostname or ""
        port = u.port or (443 if u.scheme == "https" else 80)
        if not host:
            return {"status": "warn", "detail": f"can't parse hostname from {base_url!r}"}
        started = _now_ms()
        with socket.create_connection((host, port), timeout=4):
            return {"status": "ok", "detail": f"reachable: {host}:{port}", "latency_ms": round(_now_ms() - started, 1)}
    except Exception as e:
        return {"status": "error", "detail": f"can't reach {base_url}: {e}"}


# ---------- node: proxy -----------------------------------------------------

def check_proxy(needed: bool) -> dict[str, Any]:
    """Is the local translation proxy running and answering /healthz?

    `needed=False` for Anthropic-format presets where the proxy isn't
    in the loop — that's not a failure, just N/A.
    """
    if not needed:
        return {"status": "ok", "detail": "not needed (Anthropic-format preset talks upstream directly)"}
    cfg = load()
    host = cfg.get("proxy", {}).get("host", "127.0.0.1")
    port = int(cfg.get("proxy", {}).get("port", 47824))
    if not proxy_mod.is_running():
        return {"status": "error", "detail": f"proxy not running on {host}:{port} — flip the toggle ON or run `claude-oneclick proxy-start`"}
    try:
        started = _now_ms()
        r = urllib.request.urlopen(f"http://{host}:{port}/healthz", timeout=2)
        if r.status == 200:
            return {"status": "ok", "detail": f"running on {host}:{port}", "latency_ms": round(_now_ms() - started, 1)}
        return {"status": "error", "detail": f"/healthz returned HTTP {r.status}"}
    except Exception as e:
        return {"status": "error", "detail": f"can't reach proxy: {e}"}


# ---------- node: Claude Code -----------------------------------------------

def check_claude_cli() -> dict[str, Any]:
    """Is the `claude` CLI installed?"""
    path = shutil.which("claude")
    if not path:
        return {"status": "warn", "detail": "`claude` CLI not on PATH (the VSCode extension still works)"}
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=4)
        version = (out.stdout or out.stderr).strip().splitlines()[0] if out.returncode == 0 else "unknown"
        return {"status": "ok", "detail": f"`claude` v{version} at {path}"}
    except Exception as e:
        return {"status": "warn", "detail": f"`claude` found but version check failed: {e}"}


# ---------- wire: provider ↔ proxy ------------------------------------------

def check_wire_provider_proxy(preset: dict[str, Any], provider_node: dict[str, Any], proxy_node: dict[str, Any]) -> dict[str, Any]:
    """The proxy can talk to the provider — that's already proven by the
    provider probe (which the proxy *would* make on a real request). So
    we mirror its result.

    For Anthropic-format presets the wire is direct (no proxy in the
    middle), so we report 'direct' instead.
    """
    if (preset.get("format") or "openai") == "anthropic":
        return {"status": provider_node["status"], "detail": "Claude Code talks to the provider directly (no proxy)"}
    if provider_node["status"] != "ok":
        return {"status": "error", "detail": "provider unreachable — see the Provider node for the cause"}
    return {"status": "ok", "detail": f"upstream call path verified ({provider_node.get('latency_ms', '?')}ms round trip)"}


# ---------- wire: proxy ↔ Claude Code ---------------------------------------

_RECENT_REQUEST_RE = re.compile(r"→ upstream", re.IGNORECASE)


def _windows_user_env(name: str) -> str | None:
    """Read a user-scope env var from HKCU\\Environment via PowerShell.
    Authoritative on Windows — reflects what new processes will inherit.
    Returns None if the call fails (PowerShell missing, etc.)."""
    try:
        ps = f"[System.Environment]::GetEnvironmentVariable('{name}', 'User')"
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=4, check=False,
        )
        if proc.returncode != 0:
            return None
        out = (proc.stdout or "").strip()
        return out or None
    except Exception:
        return None


def _expected_base_url() -> str:
    cfg = load()
    p = cfg.get("proxy", {}) or {}
    return f"http://{p.get('host', '127.0.0.1')}:{int(p.get('port', 47824))}"


def check_wire_proxy_claude(needed: bool) -> dict[str, Any]:
    """Is the env wired up so a fresh shell + Claude Code finds the proxy?

    Platform-aware. POSIX checks env.sh; Windows checks env.ps1 +
    env.cmd + the user-scope registry (HKCU\\Environment), which is
    the authoritative source for what new processes will inherit.
    """
    if not needed:
        return {"status": "ok", "detail": "Claude Code talks to the provider directly (no proxy)"}

    is_win = platform.system() == "Windows"
    expected = _expected_base_url()

    if is_win:
        # 1a. env.ps1 / env.cmd content
        from claude_oneclick.windows import env_cmd_path, env_ps1_path
        ps1 = env_ps1_path()
        cmd_p = env_cmd_path()
        ps1_ok = ps1.exists() and "ANTHROPIC_BASE_URL" in ps1.read_text(encoding="utf-8", errors="ignore")
        cmd_ok = cmd_p.exists() and "ANTHROPIC_BASE_URL" in cmd_p.read_text(encoding="utf-8", errors="ignore")
        files_have_export = ps1_ok or cmd_ok

        # 1b. registry (most authoritative — what fresh processes inherit)
        reg_value = _windows_user_env("ANTHROPIC_BASE_URL")

        if not files_have_export and not reg_value:
            return {"status": "error",
                    "detail": "ANTHROPIC_BASE_URL not set on this Windows account — "
                              "flip the toggle ON or run `claude-oneclick on`"}
        if reg_value and reg_value != expected:
            return {"status": "warn",
                    "detail": f"user-env has ANTHROPIC_BASE_URL={reg_value!r}, "
                              f"but the proxy is running at {expected}. "
                              "Flip the toggle OFF and ON to resync."}
    else:
        ef = env_file()
        has_export = False
        if ef.exists():
            text = ef.read_text(encoding="utf-8", errors="ignore")
            has_export = "export ANTHROPIC_BASE_URL=" in text
        if not has_export:
            return {"status": "error",
                    "detail": "ANTHROPIC_BASE_URL not exported in ~/.config/claude-oneclick/env.sh — "
                              "flip the toggle ON or run `claude-oneclick on`"}

    # Strongest proof: an actual request hit the proxy recently.
    log = proxy_log()
    recent = False
    if log.exists():
        try:
            mtime = log.stat().st_mtime
            if time.time() - mtime < 300:
                with log.open("rb") as f:
                    f.seek(0, os.SEEK_END)
                    size = f.tell()
                    f.seek(max(0, size - 4096))
                    tail = f.read(4096).decode("utf-8", "replace")
                if _RECENT_REQUEST_RE.search(tail):
                    recent = True
        except OSError:
            pass

    in_proc = bool(os.environ.get("ANTHROPIC_BASE_URL"))

    if recent:
        return {"status": "ok", "detail": "Claude Code is hitting the proxy (recent request in the log)"}
    if in_proc:
        return {"status": "ok", "detail": "env wired up; no traffic seen yet — open a Claude Code session"}
    if is_win:
        return {"status": "warn",
                "detail": "env is set in your user account, but no traffic seen yet. "
                          "Open a NEW terminal/VSCode window (existing ones still have the old env), "
                          "then start Claude Code there."}
    return {"status": "warn",
            "detail": "env file is correct but this UI process didn't inherit it. "
                      "Open a NEW terminal (or restart VSCode) so Claude Code picks up the env, "
                      "then run `claude` to test."}


# ---------- top-level -------------------------------------------------------

def diagnose() -> dict[str, Any]:
    cfg = load()
    enabled = bool(cfg.get("enabled"))
    active_name = cfg.get("active") or "anthropic"
    preset = get_preset(active_name, cfg) or {}
    fmt = (preset.get("format") or "openai").lower()
    proxy_needed = enabled and fmt == "openai" and active_name != "anthropic"

    # If the user is not actually routing (toggle OFF or the no-op
    # preset), every check is "N/A" and we just say so cleanly — no
    # false-positive errors.
    if not enabled or active_name == "anthropic":
        nodes = {
            "provider": {"status": "off", "detail": "no provider configured (toggle OFF or Anthropic default)"},
            "proxy":    {"status": "off", "detail": "proxy not active"},
            "claude":   check_claude_cli(),
        }
        wires = {
            "provider_proxy": {"status": "off", "detail": "—"},
            "proxy_claude":   {"status": "off", "detail": "—"},
        }
        return {
            "overall": "off",
            "active": active_name,
            "enabled": enabled,
            "format": fmt,
            "nodes": nodes,
            "wires": wires,
            "checked_at": time.time(),
            "platform": platform.system(),
        }

    provider_node = check_provider(preset)
    proxy_node = check_proxy(proxy_needed)
    claude_node = check_claude_cli()
    wire_pp = check_wire_provider_proxy(preset, provider_node, proxy_node)
    wire_pc = check_wire_proxy_claude(proxy_needed)

    statuses = [n["status"] for n in (provider_node, proxy_node, claude_node, wire_pp, wire_pc)]
    if any(s == "error" for s in statuses):
        overall = "error"
    elif any(s == "warn" for s in statuses):
        overall = "warn"
    else:
        overall = "ok"

    return {
        "overall": overall,
        "active": active_name,
        "active_label": preset.get("label") or active_name,
        "active_model": preset.get("model"),
        "enabled": enabled,
        "format": fmt,
        "nodes": {"provider": provider_node, "proxy": proxy_node, "claude": claude_node},
        "wires": {"provider_proxy": wire_pp, "proxy_claude": wire_pc},
        "checked_at": time.time(),
        "platform": platform.system(),
    }
