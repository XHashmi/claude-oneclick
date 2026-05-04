"""Optional alternative backend: claude-code-router (CCR).

Our hand-rolled translation proxy works for most cases, but some users
have hit edge cases (typically client-side spinner-stuck behavior on
specific Claude Code versions) where CCR's battle-tested SSE handling
"just works." This module lets users opt into CCR as the routing
layer instead of (or in addition to) our proxy.

CCR is published as the npm package ``@musistudio/claude-code-router``
and provides the ``ccr`` CLI. We don't vendor its source — that would
require shipping a Node.js toolchain. Instead we:

1. Detect a working Node.js >= 18 install. If none, surface a clear
   "install Node.js" message rather than failing silently.
2. Install (or ensure-installed) CCR via ``npm install -g``. Idempotent.
3. Translate the user's active preset into CCR's ``config.json`` shape
   under ``~/.claude-code-router/config.json``.
4. Start ``ccr start`` as a managed subprocess. Stop it cleanly when
   the toggle is flipped OFF.
5. Point Claude Code's env vars (``ANTHROPIC_BASE_URL``, etc) at CCR's
   listener instead of ours.

This module is best-effort and never blocks the rest of the install
when CCR isn't available — users without Node.js continue using our
built-in proxy unchanged.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

CCR_PORT_DEFAULT = 47825  # bumped from CCR's own default to avoid conflicts


# ---------- Node.js + CCR detection ----------------------------------------

def node_available() -> tuple[bool, str]:
    """Return (ok, version_or_message). Requires Node.js >= 18."""
    node = shutil.which("node")
    if not node:
        return False, "Node.js is not installed"
    try:
        proc = subprocess.run(
            [node, "--version"], capture_output=True, text=True, timeout=4,
        )
        ver = (proc.stdout or "").strip().lstrip("v")
        major = int(ver.split(".", 1)[0])
        if major < 18:
            return False, f"Node.js {ver} is too old (need >= 18)"
        return True, ver
    except Exception as e:
        return False, f"Node.js check failed: {e}"


def ccr_available() -> bool:
    """Is the `ccr` CLI on PATH and runnable?"""
    if not shutil.which("ccr"):
        return False
    try:
        proc = subprocess.run(["ccr", "--help"], capture_output=True, text=True, timeout=4)
        return proc.returncode == 0
    except Exception:
        return False


def install_ccr() -> tuple[bool, str]:
    """Install or upgrade CCR globally via npm. Idempotent."""
    npm = shutil.which("npm")
    if not npm:
        return False, "npm not found (Node.js install was incomplete)"
    try:
        proc = subprocess.run(
            [npm, "install", "-g", "@musistudio/claude-code-router"],
            capture_output=True, text=True, timeout=300,
        )
        if proc.returncode != 0:
            return False, f"npm install failed: {(proc.stderr or proc.stdout)[:400]}"
        return True, "installed"
    except Exception as e:
        return False, f"install failed: {e}"


# ---------- config translation ---------------------------------------------

def _ccr_config_path() -> Path:
    return Path.home() / ".claude-code-router" / "config.json"


def render_ccr_config(preset: dict[str, Any], *, port: int = CCR_PORT_DEFAULT,
                      api_key_token: str = "claude-oneclick") -> dict[str, Any]:
    """Translate our preset shape into CCR's config.json shape.

    CCR groups config under top-level ``Providers`` (each with a list of
    models) and a ``Router`` (which routes scenarios — default, background,
    think, longContext, webSearch — to ``provider,model`` strings).

    For our single-active-preset model: the user picks ONE preset, so we
    emit ONE provider with one model entry, and route every scenario to
    it. Users who want CCR's full multi-provider routing can edit the
    config.json directly afterward.
    """
    from claude_oneclick.config import _resolve_api_key
    base = (preset.get("base_url") or "").rstrip("/")
    if not base:
        raise ValueError("preset is missing base_url")
    if not base.endswith("/chat/completions"):
        # CCR expects the FULL chat/completions URL, not the host root.
        if base.endswith("/v1"):
            base = base + "/chat/completions"
        else:
            base = base + "/v1/chat/completions"
    name = preset.get("name") or "active"
    model = preset.get("model") or ""
    api_key = _resolve_api_key(preset)
    transformer_uses: list[Any] = []
    # CCR ships built-in transformers per provider. Map a few we know.
    if "deepseek.com" in base:
        transformer_uses.append("deepseek")
    elif "groq.com" in base:
        transformer_uses.append("groq")
    elif "openrouter.ai" in base:
        transformer_uses.append("openrouter")
    return {
        "LOG": True,
        "HOST": "127.0.0.1",
        "PORT": port,
        "APIKEY": api_key_token,
        "API_TIMEOUT_MS": int(preset.get("request_timeout_seconds") or 600) * 1000,
        "Providers": [
            {
                "name": name,
                "api_base_url": base,
                "api_key": api_key or "",
                "models": [model] if model else [],
                "transformer": ({"use": transformer_uses} if transformer_uses else {}),
            }
        ],
        "Router": {
            "default": f"{name},{model}" if model else "",
            "background": f"{name},{preset.get('small_fast_model') or model}",
            "think": f"{name},{model}",
            "longContext": f"{name},{model}",
            "webSearch": f"{name},{model}",
        },
        "NON_INTERACTIVE_MODE": False,
    }


def write_ccr_config(preset: dict[str, Any], *, port: int = CCR_PORT_DEFAULT,
                     api_key_token: str = "claude-oneclick") -> Path:
    cfg = render_ccr_config(preset, port=port, api_key_token=api_key_token)
    path = _ccr_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Back up existing config so the user doesn't lose hand-tuned routes.
    if path.exists():
        backup = path.with_suffix(f".json.coc-backup.{int(time.time())}")
        try: backup.write_bytes(path.read_bytes())
        except Exception: pass
    path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return path


# ---------- lifecycle ------------------------------------------------------

def is_running(port: int = CCR_PORT_DEFAULT, host: str = "127.0.0.1") -> bool:
    import socket
    try:
        with socket.create_connection((host, port), timeout=0.4):
            return True
    except OSError:
        return False


def start(preset: dict[str, Any], *, port: int = CCR_PORT_DEFAULT,
          api_key_token: str = "claude-oneclick") -> tuple[bool, str]:
    """Start CCR with config derived from preset. Returns (ok, message)."""
    ok, msg = node_available()
    if not ok:
        return False, msg
    if not ccr_available():
        ok2, msg2 = install_ccr()
        if not ok2:
            return False, msg2
    try:
        write_ccr_config(preset, port=port, api_key_token=api_key_token)
    except Exception as e:
        return False, f"config write failed: {e}"
    if is_running(port):
        # Already running (probably from a previous toggle); ask it to reload
        # config by restarting.
        try:
            subprocess.run(["ccr", "restart"], capture_output=True, text=True, timeout=10)
        except Exception:
            pass
        return True, "restarted"
    try:
        # ccr start runs as a daemon — it spawns its own server process and
        # detaches. We just kick it off and trust ccr's PID management.
        proc = subprocess.run(
            ["ccr", "start"], capture_output=True, text=True, timeout=15,
        )
        if proc.returncode != 0:
            return False, f"ccr start failed: {(proc.stderr or proc.stdout)[:300]}"
    except Exception as e:
        return False, f"ccr start error: {e}"
    # Wait briefly for the listener to come up.
    for _ in range(20):
        if is_running(port):
            return True, "started"
        time.sleep(0.25)
    return False, "ccr started but listener never came up"


def stop() -> tuple[bool, str]:
    if not ccr_available():
        return True, "ccr not installed"
    try:
        proc = subprocess.run(
            ["ccr", "stop"], capture_output=True, text=True, timeout=10,
        )
        if proc.returncode != 0:
            return False, f"ccr stop failed: {(proc.stderr or proc.stdout)[:300]}"
        return True, "stopped"
    except Exception as e:
        return False, f"ccr stop error: {e}"
