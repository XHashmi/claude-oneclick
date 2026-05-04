"""Web UI HTTP server.

Serves the static UI from ``claude_oneclick/ui/`` and a small JSON API.
Bound to localhost only. Read-only endpoints are unauthenticated; mutating
endpoints require a token cookie (set on the first GET) that's matched
against ``X-CSRF`` to make it harder for a random local browser tab to flip
your config.
"""
from __future__ import annotations

import json
import logging
import mimetypes
import os
import secrets
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from claude_oneclick import autostart, config as cfg_mod
from claude_oneclick import discover, proxy, system_env, updater
from claude_oneclick.config import (
    all_presets,
    delete_preset,
    get_preset,
    load,
    save,
    set_active,
    set_enabled,
    update_override,
    upsert_user_preset,
)
from claude_oneclick.paths import config_dir, proxy_log


UI_DIR = Path(__file__).parent / "ui"

# Set per-process; the cookie value the UI must echo back.
_CSRF_TOKEN = secrets.token_urlsafe(24)

# In-memory model-discovery cache: name -> (timestamp, [models])
_MODELS_CACHE: dict[str, tuple[float, list[str]]] = {}


def _models_cache_get(name: str, ttl: int) -> list[str] | None:
    item = _MODELS_CACHE.get(name)
    if not item:
        return None
    ts, models = item
    if time.time() - ts > ttl:
        return None
    return models


def _allowed_hosts(cfg: dict[str, Any]) -> set[str]:
    port = int(cfg.get("ui", {}).get("port", 47823))
    return {
        f"127.0.0.1:{port}", f"127.0.0.1",
        f"localhost:{port}", f"localhost",
        f"[::1]:{port}", f"[::1]",
    }


class _Handler(BaseHTTPRequestHandler):
    server_version = "claude-oneclick-ui/0.1"

    def log_message(self, format: str, *args: Any) -> None:
        # Quiet by default; the UI logs to stderr.
        return

    def _host_ok(self) -> bool:
        """Reject requests whose `Host` header isn't loopback.

        Defense against DNS rebinding: a hostile page resolves
        ``evil.com`` to ``127.0.0.1`` and then talks to our UI as if it
        were same-origin. Without this check the page could read every
        saved API key via ``/api/state`` (well, redacted) or
        ``/api/export`` (full plaintext). Cookies are SameSite=Strict,
        but the rebound origin IS technically the same site by then —
        the only safe defense is to reject foreign Host headers.
        """
        cfg = load()
        host = (self.headers.get("Host") or "").lower().strip()
        return host in _allowed_hosts(cfg)

    # ---------- helpers ----------------------------------------------------

    def _read_json(self) -> Any:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        return json.loads(self.rfile.read(n).decode("utf-8"))

    def _send_json(self, status: int, payload: Any, set_cookie: bool = False) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if set_cookie:
            self.send_header("Set-Cookie", f"csrf={_CSRF_TOKEN}; Path=/; SameSite=Strict")
        self.end_headers()
        self.wfile.write(body)

    def _check_csrf(self) -> bool:
        header_token = self.headers.get("X-CSRF") or ""
        return secrets.compare_digest(header_token, _CSRF_TOKEN)

    # ---------- routing ----------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        if not self._host_ok():
            self._send_json(403, {"error": "host_not_allowed"})
            return
        path, _, qs = self.path.partition("?")
        params = urllib.parse.parse_qs(qs)
        if path == "/" or path == "/index.html":
            self._serve_static("index.html", set_cookie=True)
            return
        if path.startswith("/static/"):
            self._serve_static(path[len("/static/"):])
            return
        if path == "/api/state":
            self._api_state()
            return
        if path == "/api/models":
            self._api_models(params.get("name", [""])[0], force=params.get("force", ["0"])[0] == "1")
            return
        if path == "/api/log/proxy":
            self._api_log(int(params.get("lines", ["200"])[0]))
            return
        if path == "/api/export":
            # Export contains API keys. Require CSRF so a third-party site
            # can't steal them via a same-origin request smuggled in by
            # DNS rebinding.
            if not self._check_csrf():
                self._send_json(403, {"error": "csrf"})
                return
            self._api_export()
            return
        if path == "/api/autostart":
            self._send_json(200, {
                "enabled": autostart.is_enabled(),
                "location": autostart.describe(),
            })
            return
        if path == "/api/update/check":
            force = params.get("force", ["0"])[0] == "1"
            self._send_json(200, updater.check(force=force))
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if not self._host_ok():
            self._send_json(403, {"error": "host_not_allowed"})
            return
        if not self._check_csrf():
            self._send_json(403, {"error": "csrf"})
            return
        path, _, _ = self.path.partition("?")
        try:
            body = self._read_json()
        except Exception as e:
            self._send_json(400, {"error": f"bad json: {e}"})
            return
        if path == "/api/toggle":
            self._api_toggle(bool(body.get("enabled")))
            return
        if path == "/api/use":
            self._api_use(body.get("name", ""))
            return
        if path == "/api/preset":
            self._api_upsert_preset(body)
            return
        if path == "/api/keys":
            self._api_set_key(body.get("name", ""), body.get("api_key", ""))
            return
        if path == "/api/settings":
            self._api_settings(body)
            return
        if path == "/api/proxy/restart":
            self._api_proxy_restart()
            return
        if path == "/api/import":
            self._api_import(body)
            return
        if path == "/api/autostart":
            ok = autostart.enable() if body.get("enabled") else autostart.disable()
            self._send_json(200, {"ok": ok, "enabled": autostart.is_enabled()})
            return
        if path == "/api/update/apply":
            result = updater.apply()
            self._send_json(200 if result.get("ok") else 500, result)
            return
        self._send_json(404, {"error": "not found"})

    def do_DELETE(self) -> None:  # noqa: N802
        if not self._host_ok():
            self._send_json(403, {"error": "host_not_allowed"})
            return
        if not self._check_csrf():
            self._send_json(403, {"error": "csrf"})
            return
        if self.path.startswith("/api/preset/"):
            name = urllib.parse.unquote(self.path[len("/api/preset/"):])
            ok = delete_preset(name)
            system_env.apply_state()
            self._send_json(200 if ok else 404, {"ok": ok})
            return
        self._send_json(404, {"error": "not found"})

    # ---------- static -----------------------------------------------------

    def _serve_static(self, rel: str, set_cookie: bool = False) -> None:
        # Prevent path traversal
        target = (UI_DIR / rel).resolve()
        try:
            target.relative_to(UI_DIR.resolve())
        except ValueError:
            self._send_json(400, {"error": "bad path"})
            return
        if not target.exists() or not target.is_file():
            self._send_json(404, {"error": "not found"})
            return
        ctype, _ = mimetypes.guess_type(str(target))
        ctype = ctype or "application/octet-stream"
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if set_cookie:
            self.send_header("Set-Cookie", f"csrf={_CSRF_TOKEN}; Path=/; SameSite=Strict")
        self.end_headers()
        self.wfile.write(data)

    # ---------- API --------------------------------------------------------

    def _api_state(self) -> None:
        cfg = load()
        presets = all_presets(cfg)
        # Don't ship raw API keys back to the browser; mark presence only.
        sanitized = []
        for p in presets:
            sp = dict(p)
            ak = sp.get("api_key") or ""
            sp["api_key_set"] = bool(ak)
            sp["api_key"] = ""
            sanitized.append(sp)
        self._send_json(200, {
            "csrf": _CSRF_TOKEN,
            "active": cfg.get("active"),
            "enabled": cfg.get("enabled"),
            "presets": sanitized,
            "proxy": {
                "host": cfg.get("proxy", {}).get("host"),
                "port": cfg.get("proxy", {}).get("port"),
                "running": proxy.is_running(),
            },
            "ui": cfg.get("ui", {}),
            "log_level": cfg.get("log_level"),
            "autostart_proxy": cfg.get("autostart_proxy"),
            "skip_vscode_login": cfg.get("skip_vscode_login"),
            "model_discovery": cfg.get("model_discovery"),
        })

    def _api_toggle(self, enabled: bool) -> None:
        set_enabled(enabled)
        status = system_env.apply_state()
        self._send_json(200, {"enabled": enabled, "status": status})

    def _api_use(self, name: str) -> None:
        try:
            set_active(name)
        except KeyError:
            self._send_json(404, {"error": f"unknown preset {name!r}"})
            return
        status = system_env.apply_state()
        self._send_json(200, {"active": name, "status": status})

    def _api_upsert_preset(self, body: dict[str, Any]) -> None:
        name = body.get("name") or ""
        if not name or not name.replace("-", "").replace("_", "").isalnum():
            self._send_json(400, {"error": "name must be alnum + - _"})
            return
        existing = get_preset(name) or {}
        merged = dict(existing)
        # Allow only known keys.
        for k in ("label", "base_url", "api_key", "model", "small_fast_model",
                  "format", "notes", "extra_headers", "model_aliases",
                  "sampling", "request_timeout_seconds", "retries",
                  "retry_backoff", "system_prompt_prefix",
                  "system_prompt_suffix", "disable_streaming",
                  "prompt_cache_passthrough", "reasoning_enabled",
                  "reasoning_effort", "extra_body", "routing_rules"):
            if k in body and body[k] is not None:
                merged[k] = body[k]
        merged["name"] = name
        if existing.get("builtin"):
            update_override(name, {k: v for k, v in merged.items() if k != "name"})
        else:
            upsert_user_preset(merged)
        system_env.apply_state()
        # Bust the model cache for this preset.
        _MODELS_CACHE.pop(name, None)
        self._send_json(200, {"ok": True})

    def _api_set_key(self, name: str, api_key: str) -> None:
        if not get_preset(name):
            self._send_json(404, {"error": "unknown preset"})
            return
        if get_preset(name).get("builtin"):
            update_override(name, {"api_key": api_key})
        else:
            p = dict(get_preset(name))
            p["api_key"] = api_key
            upsert_user_preset(p)
        system_env.apply_state()
        _MODELS_CACHE.pop(name, None)
        self._send_json(200, {"ok": True})

    def _api_models(self, name: str, force: bool) -> None:
        cfg = load()
        ttl = int(cfg.get("model_discovery", {}).get("cache_ttl_seconds", 600))
        if not force:
            cached = _models_cache_get(name, ttl)
            if cached is not None:
                self._send_json(200, {"models": cached, "cached": True})
                return
        preset = get_preset(name, cfg)
        if not preset:
            self._send_json(404, {"error": "unknown preset"})
            return
        try:
            models = discover.list_models_for_preset(preset)
        except discover.DiscoverError as e:
            self._send_json(502, {"error": str(e)})
            return
        _MODELS_CACHE[name] = (time.time(), models)
        self._send_json(200, {"models": models, "cached": False})

    def _api_settings(self, body: dict[str, Any]) -> None:
        cfg = load()
        for k in ("log_level", "autostart_proxy", "skip_vscode_login"):
            if k in body:
                cfg[k] = body[k]
        if "ui" in body and isinstance(body["ui"], dict):
            cfg.setdefault("ui", {}).update(body["ui"])
        if "proxy" in body and isinstance(body["proxy"], dict):
            cfg.setdefault("proxy", {}).update(body["proxy"])
        if "model_discovery" in body and isinstance(body["model_discovery"], dict):
            cfg.setdefault("model_discovery", {}).update(body["model_discovery"])
        save(cfg)
        # Settings can change the proxy port → restart proxy.
        proxy.stop()
        system_env.apply_state()
        self._send_json(200, {"ok": True})

    def _api_log(self, lines: int) -> None:
        path = proxy_log()
        if not path.exists():
            self._send_json(200, {"lines": []})
            return
        # Tail without slurping the whole file.
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            block = 4096
            data = b""
            while size > 0 and data.count(b"\n") <= lines:
                step = min(block, size)
                size -= step
                f.seek(size)
                data = f.read(step) + data
        text = data.decode("utf-8", "replace").splitlines()
        self._send_json(200, {"lines": text[-lines:]})

    def _api_proxy_restart(self) -> None:
        proxy.stop()
        proxy.ensure_running()
        self._send_json(200, {"running": proxy.is_running()})

    def _api_export(self) -> None:
        cfg = load()
        self._send_json(200, cfg)

    def _api_import(self, body: dict[str, Any]) -> None:
        if not isinstance(body, dict):
            self._send_json(400, {"error": "expected object"})
            return
        save(body)
        system_env.apply_state()
        self._send_json(200, {"ok": True})


def serve(host: str = "127.0.0.1", port: int = 47823, *, open_browser: bool = True) -> None:
    cfg = load()
    host = cfg.get("ui", {}).get("host", host)
    port = int(cfg.get("ui", {}).get("port", port))
    httpd = ThreadingHTTPServer((host, port), _Handler)
    httpd.daemon_threads = True
    url = f"http://{host}:{port}/"
    print(f"claude-oneclick UI: {url}")
    print(f"CSRF token (for `curl`): {_CSRF_TOKEN}")
    if open_browser and cfg.get("ui", {}).get("open_browser_on_launch", True):
        threading.Thread(target=lambda: (time.sleep(0.3), webbrowser.open(url)), daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
