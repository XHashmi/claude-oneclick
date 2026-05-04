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
from claude_oneclick import desktop as desktop_mod
from claude_oneclick import diagnose as diagnose_mod
from claude_oneclick import discover, proxy, system_env, updater
from claude_oneclick import usage as usage_mod
from claude_oneclick.config import (
    all_presets,
    delete_preset,
    get_preset,
    load,
    save,
    set_active,
    set_api_key_for_group,
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
        if path == "/api/provider-models":
            self._api_provider_models(params.get("group", [""])[0])
            return
        if path == "/api/diagnose":
            self._send_json(200, diagnose_mod.diagnose())
            return
        if path == "/api/desktop/status":
            self._send_json(200, desktop_mod.status())
            return
        if path == "/api/usage":
            try:
                days = max(1, min(90, int(params.get("days", ["7"])[0])))
            except ValueError:
                days = 7
            self._send_json(200, usage_mod.report(days=days))
            return
        if path == "/api/keychain/status":
            from claude_oneclick import secrets_store
            cfg = load()
            self._send_json(200, {
                "enabled": bool(cfg.get("use_keychain")),
                "available": secrets_store.available(),
                "backend": secrets_store.describe(),
            })
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
        if path == "/api/resync":
            # Force-reapply env files, VSCode settings, and proxy lifecycle
            # from the current config. Use when the toggle was flipped but
            # the system didn't pick up — common on Windows after a reboot
            # or when env.ps1 / registry got out of sync with config.json.
            status = system_env.apply_state()
            self._send_json(200, {"ok": True, "status": status})
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
        if path == "/api/desktop/toggle":
            cfg = load()
            p = cfg.get("proxy", {})
            base = f"http://{p.get('host', '127.0.0.1')}:{int(p.get('port', 47824))}"
            url = body.get("url") or base
            result = desktop_mod.enable(url) if body.get("enabled") else desktop_mod.disable()
            self._send_json(200 if result.get("ok") else 500, result)
            return
        if path == "/api/test-preset":
            self._api_test_preset(body.get("name", ""))
            return
        if path == "/api/keychain/toggle":
            cfg = load(); cfg["use_keychain"] = bool(body.get("enabled")); save(cfg)
            self._send_json(200, {"ok": True, "enabled": cfg["use_keychain"]})
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
        # Use the *resolved* key (own → keychain → sibling fallback) so
        # the UI's "saved" badge matches what the proxy will actually
        # send. Without this, siblings in a group looked unkeyed even
        # though the request would succeed.
        from claude_oneclick.config import _resolve_api_key
        from claude_oneclick.tool_support import supports_tools
        sanitized = []
        for p in presets:
            sp = dict(p)
            resolved = _resolve_api_key(p, cfg)
            sp["api_key_set"] = bool(resolved)
            sp["api_key_source"] = (
                "own" if (p.get("api_key") or "")
                else ("group" if resolved else "none")
            )
            sp["api_key"] = ""
            sp["tools_ok"] = supports_tools(p.get("model"))
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
                  "reasoning_effort", "extra_body", "routing_rules",
                  "response_format"):
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
        try:
            updated = set_api_key_for_group(name, api_key)
        except KeyError:
            self._send_json(404, {"error": "unknown preset"})
            return
        system_env.apply_state()
        # Bust the model-discovery cache for every preset we just keyed.
        for n in updated:
            _MODELS_CACHE.pop(n, None)
        self._send_json(200, {"ok": True, "updated": updated})

    def _api_test_preset(self, name: str) -> None:
        """End-to-end smoke test: send a tiny /v1/messages through our own
        proxy and assert a non-empty response. Proves the entire pipeline
        (env wiring, translation, auth, upstream, response shape) works,
        not just that /v1/models returns something.
        """
        import time as _t
        cfg = load()
        preset = get_preset(name, cfg)
        if not preset:
            self._send_json(404, {"ok": False, "error": "unknown preset"})
            return
        if (preset.get("format") or "openai").lower() == "anthropic":
            self._send_json(400, {"ok": False, "error": "test-preset only works for openai-format presets"})
            return
        # Pre-flight: catch the obvious config holes before paying for a
        # round trip and a vague "test failed" message.
        if not (preset.get("base_url") or "").strip():
            self._send_json(200, {"ok": False, "error": "no Base URL set on this preset"})
            return
        from claude_oneclick.config import _resolve_api_key
        if not _resolve_api_key(preset):
            self._send_json(200, {"ok": False, "error": "no API key saved on this preset (paste one in the API key field, then click Save changes)"})
            return
        # Make sure the proxy is up before pinging it.
        proxy.ensure_running()
        p = cfg.get("proxy", {})
        proxy_host = p.get("host", "127.0.0.1")
        proxy_port = int(p.get("port", 47824))
        try:
            payload = json.dumps({
                "model": preset.get("model") or name,
                "messages": [{"role": "user", "content": "Reply with just OK."}],
                "max_tokens": 20,
                "stream": False,
            }).encode("utf-8")
            # Per-request preset override → no need to flip the active
            # preset or touch global env state.
            req = urllib.request.Request(
                f"http://{proxy_host}:{proxy_port}/v1/messages",
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "X-CoC-Preset": name,
                },
                method="POST",
            )
            t0 = _t.time()
            with urllib.request.urlopen(req, timeout=30) as r:
                resp = json.loads(r.read().decode("utf-8"))
            elapsed = round((_t.time() - t0) * 1000, 1)
            text = ""
            tool_calls = 0
            for b in resp.get("content") or []:
                if isinstance(b, dict):
                    if b.get("type") == "text":
                        text += b.get("text") or ""
                    elif b.get("type") == "tool_use":
                        tool_calls += 1
            stop_reason = resp.get("stop_reason")
            # Any well-formed response = success. Empty text + no tool +
            # error stop_reason = failure (with detail).
            ok = bool(text.strip()) or tool_calls > 0 or stop_reason in ("end_turn", "stop_sequence", "max_tokens")
            preview = text[:200] if text else (f"({tool_calls} tool call(s))" if tool_calls else "(empty response)")
            # When ok=False give the user something concrete to act on.
            # Most "empty response" cases are: model id wrong (200 with
            # nothing in content), provider rate-limited the no-cost
            # ping, or the model itself doesn't support tool use and
            # silently returned empty.
            if ok:
                error_msg = None
            else:
                bits = [
                    f"upstream returned 200 with no usable content (stop_reason={stop_reason!r}).",
                    "Common causes:",
                    f"  - model id '{preset.get('model') or name}' is not recognized by the upstream",
                    "  - the model id is recognized but doesn't support tool/function calling",
                    "  - upstream silently rate-limited the test request",
                    "Try clicking Refresh on the Models row to fetch the live catalog and pick a model from there.",
                ]
                error_msg = "\n".join(bits)
            self._send_json(200, {
                "ok": ok,
                "latency_ms": elapsed,
                "response_text": preview,
                "stop_reason": stop_reason,
                "usage": resp.get("usage"),
                "preset": name,
                "model": resp.get("model"),
                "raw_response": resp if not ok else None,  # surface for debugging
                "error": error_msg,
            })
        except urllib.error.HTTPError as e:
            try:
                err_body = e.read().decode("utf-8", "replace")
            except Exception:
                err_body = str(e)
            # Try to surface upstream's own error message for clarity.
            try:
                parsed = json.loads(err_body)
                msg = (parsed.get("error") or {}).get("message") or parsed.get("message") or err_body
            except Exception:
                msg = err_body
            # 404 from the upstream usually means the base URL is wrong
            # (missing /v1, missing /api, etc.). Probe the obvious
            # variants and tell the user exactly which one works.
            hint = ""
            if e.code == 404:
                fix = self._suggest_base_url(preset)
                if fix:
                    hint = f"\n\nHint: GET {fix}/v1/models worked — change Base URL to {fix} and Save."
            self._send_json(200, {"ok": False, "error": f"HTTP {e.code}: {str(msg)[:400]}{hint}"})
        except urllib.error.URLError as e:
            self._send_json(200, {"ok": False, "error": f"can't reach proxy at {proxy_host}:{proxy_port} — {e.reason}"})
        except Exception as e:
            self._send_json(200, {"ok": False, "error": f"{type(e).__name__}: {e}"})

    def _suggest_base_url(self, preset: dict) -> str | None:
        """Probe likely Base URL variants for the preset's host.

        Most "404 from upstream" failures are a missing/extra ``/v1`` or
        ``/api`` segment. We try the obvious siblings of the saved URL
        and return the first one whose ``/v1/models`` answers 2xx (or
        even 401, which proves the path is right but the key is wrong).
        Returns the *base* (without ``/v1/models``) so the user can
        paste it back into the form.
        """
        from claude_oneclick.config import _resolve_api_key
        base = (preset.get("base_url") or "").rstrip("/")
        if not base:
            return None
        api_key = _resolve_api_key(preset)
        # Reduce to the bare host first.
        import urllib.parse
        u = urllib.parse.urlparse(base)
        if not u.scheme or not u.netloc:
            return None
        host = f"{u.scheme}://{u.netloc}"
        path = (u.path or "").rstrip("/")
        # Build a deduplicated, ordered list of candidates.
        candidates: list[str] = []
        for c in (host, host + "/v1", host + "/api", host + "/api/v1", base):
            if c not in candidates:
                candidates.append(c)
        # Move the saved base to last so we don't re-try the failing one first.
        if base in candidates:
            candidates.remove(base)
            candidates.append(base)
        for cand in candidates:
            url = cand + "/v1/models"
            try:
                req = urllib.request.Request(url, method="GET")
                if api_key:
                    req.add_header("Authorization", f"Bearer {api_key}")
                with urllib.request.urlopen(req, timeout=4) as r:
                    if 200 <= r.status < 300:
                        return cand
            except urllib.error.HTTPError as e:
                # 401/403 still proves the path resolved.
                if e.code in (401, 403):
                    return cand
            except Exception:
                continue
        return None

    def _api_provider_models(self, group: str) -> None:
        """Live catalog for a whole provider group (e.g. "NVIDIA NIMs").

        Picks the first preset in that group that has both a base_url and
        a saved api_key, calls discover.list_models on it, and returns the
        result. Lets the sidebar render "extra cards" beyond the
        hardcoded shortcuts without the user having to pre-pick a
        specific shortcut.
        """
        if not group:
            self._send_json(400, {"error": "missing group"})
            return
        cfg = load()
        candidate = None
        for p in all_presets(cfg):
            if p.get("group") == group and p.get("base_url"):
                candidate = p
                if p.get("api_key"):
                    break  # prefer a preset with a key
        if not candidate:
            self._send_json(404, {"error": f"no preset with a base_url in group '{group}'"})
            return
        if not candidate.get("api_key") and "localhost" not in (candidate.get("base_url") or ""):
            self._send_json(400, {"error": f"save an API key on any preset in '{group}' first"})
            return
        try:
            models = discover.list_models_for_preset(candidate)
        except discover.DiscoverError as e:
            self._send_json(502, {"error": str(e)})
            return
        # Filter out the model ids that are already in hardcoded shortcuts
        # so the "extra" list is truly extra.
        already = {p.get("model") for p in all_presets(cfg) if p.get("group") == group}
        extras = [m for m in models if m not in already]
        from claude_oneclick.tool_support import supports_tools
        # Wrap each model with a tool-support hint so the UI can flag
        # the ones that won't drive Claude Code's tool loop.
        annotated = [{"id": m, "tools_ok": supports_tools(m)} for m in models]
        annotated_extras = [{"id": m, "tools_ok": supports_tools(m)} for m in extras]
        self._send_json(200, {
            "group": group,
            "base_url": candidate["base_url"],
            "all_models": models,
            "extra_models": extras,
            "models_annotated": annotated,
            "extras_annotated": annotated_extras,
            "via_preset": candidate["name"],
        })

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
