"""Read/write config.json — the source of truth for presets + state."""
from __future__ import annotations

import json
from typing import Any

from claude_oneclick.paths import config_file, ensure_dirs
from claude_oneclick.presets import BUILTIN_PRESETS, builtin_by_name


# ---------- defaults ---------------------------------------------------------

def _default_config() -> dict[str, Any]:
    return {
        "version": 2,
        "active": "anthropic",
        "enabled": False,
        "proxy": {
            "host": "127.0.0.1",
            # 47824 — unassigned by IANA, not a default for any major app.
            # 8118 (Privoxy) and 7878 (Radarr) are off-limits.
            "port": 47824,
        },
        "ui": {
            "host": "127.0.0.1",
            # 47823 — same reasoning as the proxy port.
            "port": 47823,
            "theme": "auto",            # "auto" | "light" | "dark"
            "open_browser_on_launch": True,
        },
        "log_level": "info",            # "info" | "debug"
        "model_discovery": {
            "cache_ttl_seconds": 600,
        },
        "autostart_proxy": True,
        # When True, set CLAUDE_CODE_SKIP_LOGIN=1 in the system env. This,
        # combined with ANTHROPIC_AUTH_TOKEN (already exported), makes the
        # Claude Code VSCode extension and CLI bypass the OAuth flow and
        # authenticate using the env-var token directly.
        "skip_vscode_login": True,
        # User-defined presets (built-ins live in code, not here).
        "user_presets": [],
        # Per-preset partial overrides, keyed by preset name. Each value is
        # a dict of any of: api_key, base_url, model, small_fast_model,
        # format, notes, extra_headers, model_aliases, sampling, timeouts,
        # system_prompt_prefix, system_prompt_suffix, disable_streaming,
        # prompt_cache_passthrough, routing_rules.
        "overrides": {},
    }


_PRESET_FIELDS_WITH_DEFAULTS: dict[str, Any] = {
    "label": "",
    "base_url": "",
    "api_key": "",
    "model": "",
    "small_fast_model": "",
    "format": "openai",
    "notes": "",
    "extra_headers": {},
    "model_aliases": {},
    "sampling": {
        "temperature": None,
        "top_p": None,
        "top_k": None,
        "max_tokens": None,
    },
    "request_timeout_seconds": 600,
    "retries": 2,
    "retry_backoff": 1.5,
    "system_prompt_prefix": "",
    "system_prompt_suffix": "",
    "disable_streaming": False,
    "prompt_cache_passthrough": False,
    # When True, sends OpenAI-standard `reasoning_effort: "medium"` on every
    # request. Honored by DeepSeek V4 Pro/Flash, OpenAI o-series, and a few
    # other reasoning-capable upstreams. Silently ignored by everyone else.
    "reasoning_enabled": False,
    "reasoning_effort": "medium",  # "low" | "medium" | "high"
    # Free-form JSON object merged into every outbound /v1/chat/completions
    # body. Use this to target reasoning fields on upstreams that don't
    # speak OpenAI's `reasoning_effort` — e.g. `{"reasoning": true}`,
    # `{"thinking": {"budget_tokens": 4096}}`, vendor-specific knobs, etc.
    # Reasoning-effort templating: any string value "{{effort}}" gets
    # replaced by the slider's current value (low|medium|high) when the
    # reasoning toggle is ON.
    "extra_body": {},
    "routing_rules": [],
}


def _migrate(cfg: dict[str, Any]) -> dict[str, Any]:
    """Best-effort forward migration of older config files."""
    if cfg.get("version", 1) < 2:
        cfg.setdefault("ui", {}).setdefault("theme", "auto")
        cfg["ui"].setdefault("open_browser_on_launch", True)
        cfg.setdefault("log_level", "info")
        cfg.setdefault("model_discovery", {"cache_ttl_seconds": 600})
        cfg.setdefault("autostart_proxy", True)
        cfg["version"] = 2
    return cfg


def _fill_preset_defaults(p: dict[str, Any]) -> dict[str, Any]:
    out = dict(p)
    for k, default in _PRESET_FIELDS_WITH_DEFAULTS.items():
        if k not in out or out[k] is None:
            # Fresh copies for mutables.
            out[k] = default if not isinstance(default, (dict, list)) else (
                dict(default) if isinstance(default, dict) else list(default)
            )
    return out


# ---------- load/save --------------------------------------------------------

def load() -> dict[str, Any]:
    ensure_dirs()
    path = config_file()
    if not path.exists():
        cfg = _default_config()
        save(cfg)
        return cfg
    with path.open("r", encoding="utf-8") as f:
        cfg = json.load(f)
    # Forward-compat: fill in any missing top-level keys.
    defaults = _default_config()
    for k, v in defaults.items():
        cfg.setdefault(k, v)
    return _migrate(cfg)


def save(cfg: dict[str, Any]) -> None:
    ensure_dirs()
    path = config_file()
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, sort_keys=False)
    tmp.replace(path)
    # API keys live in this file; lock it down on POSIX. (chmod is a no-op
    # on Windows.)
    try:
        import os
        os.chmod(path, 0o600)
    except OSError:
        pass


# ---------- presets ----------------------------------------------------------

def all_presets(cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Built-ins first, then user presets. Overrides applied on top."""
    cfg = cfg or load()
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for p in BUILTIN_PRESETS:
        merged = dict(p)
        ovr = cfg.get("overrides", {}).get(p["name"], {})
        merged.update({k: v for k, v in ovr.items() if v is not None})
        out.append(merged)
        seen.add(p["name"])
    for p in cfg.get("user_presets", []):
        if p["name"] in seen:
            continue
        merged = dict(p)
        merged["builtin"] = False
        out.append(merged)
    return out


def get_preset(name: str, cfg: dict[str, Any] | None = None) -> dict[str, Any] | None:
    for p in all_presets(cfg):
        if p["name"] == name:
            return p
    return None


def upsert_user_preset(preset: dict[str, Any]) -> None:
    cfg = load()
    name = preset["name"]
    # If it shadows a built-in, store as override instead.
    if builtin_by_name(name):
        cfg.setdefault("overrides", {})[name] = {
            k: preset.get(k)
            for k in (
                "base_url",
                "api_key",
                "model",
                "small_fast_model",
                "format",
                "notes",
            )
            if preset.get(k) is not None
        }
    else:
        ups = cfg.setdefault("user_presets", [])
        for i, p in enumerate(ups):
            if p["name"] == name:
                ups[i] = preset
                break
        else:
            ups.append(preset)
    save(cfg)


def delete_preset(name: str) -> bool:
    cfg = load()
    changed = False
    if name in cfg.get("overrides", {}):
        del cfg["overrides"][name]
        changed = True
    ups = cfg.get("user_presets", [])
    new = [p for p in ups if p["name"] != name]
    if len(new) != len(ups):
        cfg["user_presets"] = new
        changed = True
    if cfg.get("active") == name:
        cfg["active"] = "anthropic"
        cfg["enabled"] = False
        changed = True
    if changed:
        save(cfg)
    return changed


def set_active(name: str) -> dict[str, Any]:
    cfg = load()
    if not get_preset(name, cfg):
        raise KeyError(f"Unknown preset: {name}")
    cfg["active"] = name
    save(cfg)
    return cfg


def set_enabled(enabled: bool) -> dict[str, Any]:
    cfg = load()
    cfg["enabled"] = bool(enabled)
    save(cfg)
    return cfg


def update_override(name: str, fields: dict[str, Any]) -> None:
    """Partial update for a built-in preset (api_key, model, etc.)."""
    cfg = load()
    cfg.setdefault("overrides", {}).setdefault(name, {}).update(fields)
    save(cfg)


def set_api_key_for_group(name: str, api_key: str) -> list[str]:
    """Save an API key for ``name`` AND every other preset in the same group.

    Providers issue one API key per account, and that key works across
    every model variant they host. So if you paste your DeepSeek key
    into "DeepSeek V4 Pro", you almost certainly want it to also apply
    to "DeepSeek V4 Flash", "DeepSeek Chat", "DeepSeek R1", etc.
    Same for NVIDIA NIMs across Llama / Nemotron / DeepSeek-V4 / GLM.

    Returns the list of preset names that were updated, so the caller
    can surface "saved key on N presets" feedback.
    """
    cfg = load()
    target = get_preset(name, cfg)
    if not target:
        raise KeyError(f"Unknown preset: {name}")
    group = target.get("group")
    # Don't propagate within "Custom" or "Default" — those aren't a
    # provider account; each user-created preset is its own thing.
    propagate = group and group not in ("Custom", "Default")

    updated: list[str] = []
    for p in all_presets(cfg):
        if p["name"] != name:
            if not propagate or p.get("group") != group:
                continue
        # Only overwrite if the destination either has no key or has the
        # same one already (so we don't clobber a deliberate override).
        existing = p.get("api_key") or ""
        if existing and existing != api_key and p["name"] != name:
            continue
        if p.get("builtin"):
            cfg.setdefault("overrides", {}).setdefault(p["name"], {})["api_key"] = api_key
        else:
            for up in cfg.get("user_presets", []):
                if up["name"] == p["name"]:
                    up["api_key"] = api_key
                    break
        updated.append(p["name"])
    save(cfg)
    return updated
