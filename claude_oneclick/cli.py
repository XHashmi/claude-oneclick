"""Command-line interface for claude-oneclick."""
from __future__ import annotations

import argparse
import json
import platform
import sys
from typing import Any

from claude_oneclick import __version__, autostart, launcher, proxy, server, system_env, updater
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
from claude_oneclick.discover import DiscoverError, list_models_for_preset


def _print_status() -> int:
    cfg = load()
    active = cfg.get("active") or "anthropic"
    enabled = cfg.get("enabled")
    preset = get_preset(active, cfg) or {}
    fmt = preset.get("format") or "—"
    print(f"version:       claude-oneclick {__version__}")
    print(f"platform:      {platform.system()} {platform.machine()}")
    print(f"toggle:        {'ON' if enabled else 'OFF'}")
    print(f"active preset: {active}  ({preset.get('label', '')})")
    print(f"format:        {fmt}")
    if preset.get("base_url"):
        print(f"base_url:      {preset['base_url']}")
    print(f"model:         {preset.get('model','')}")
    print(f"small/fast:    {preset.get('small_fast_model','')}")
    print(f"api key set:   {'yes' if preset.get('api_key') else 'no'}")
    print(f"proxy port:    {cfg.get('proxy', {}).get('port')}")
    print(f"proxy running: {proxy.is_running()}")
    print(f"ui port:       {cfg.get('ui', {}).get('port')}")
    print(f"skip-login:    {'yes' if cfg.get('skip_vscode_login') else 'no'}")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    return _print_status()


def _cmd_on(args: argparse.Namespace) -> int:
    set_enabled(True)
    status = system_env.apply_state()
    print("toggle: ON")
    if status["env"]:
        for k, v in status["env"].items():
            display = v if k != "ANTHROPIC_AUTH_TOKEN" else "***"
            print(f"  {k}={display}")
    if status["vscode_settings_touched"]:
        print(f"vscode: patched {len(status['vscode_settings_touched'])} settings.json")
    print(f"proxy: {status['proxy']}")
    return 0


def _cmd_off(args: argparse.Namespace) -> int:
    set_enabled(False)
    status = system_env.apply_state()
    print("toggle: OFF")
    print(f"proxy: {status['proxy']}")
    return 0


def _cmd_toggle(args: argparse.Namespace) -> int:
    cfg = load()
    return _cmd_off(args) if cfg.get("enabled") else _cmd_on(args)


def _cmd_use(args: argparse.Namespace) -> int:
    try:
        set_active(args.name)
    except KeyError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    system_env.apply_state()
    print(f"active preset: {args.name}")
    return 0


def _cmd_presets(args: argparse.Namespace) -> int:
    cfg = load()
    active = cfg.get("active")
    for p in all_presets(cfg):
        marker = "*" if p["name"] == active else " "
        kind = "builtin" if p.get("builtin") else "custom"
        keyset = "•" if p.get("api_key") else " "
        print(f"{marker} {p['name']:<26} {kind:<7} {keyset} {p.get('format','-'):<9} {p.get('base_url','')}")
    return 0


def _cmd_add(args: argparse.Namespace) -> int:
    preset = {
        "name": args.name,
        "label": args.label or args.name,
        "base_url": args.base_url,
        "api_key": args.api_key or "",
        "model": args.model or "",
        "small_fast_model": args.small_fast_model or args.model or "",
        "format": args.format,
        "notes": args.notes or "",
        "reasoning_enabled": bool(args.reasoning),
        "reasoning_effort": args.reasoning_effort,
    }
    upsert_user_preset(preset)
    print(f"saved preset: {args.name}")
    return 0


def _cmd_delete(args: argparse.Namespace) -> int:
    if delete_preset(args.name):
        print(f"deleted: {args.name}")
        return 0
    print(f"not found: {args.name}", file=sys.stderr)
    return 1


def _cmd_set(args: argparse.Namespace) -> int:
    """Generic field setter: `claude-oneclick set deepseek model deepseek-v4`."""
    cfg = load()
    p = get_preset(args.name, cfg)
    if not p:
        print(f"unknown preset: {args.name}", file=sys.stderr)
        return 1
    field = args.field
    val: Any = args.value
    if field in ("retries",):
        val = int(val)
    elif field in ("retry_backoff", "request_timeout_seconds"):
        val = float(val)
    elif field in ("disable_streaming", "prompt_cache_passthrough"):
        val = val.lower() in ("1", "true", "yes", "on")
    if p.get("builtin"):
        update_override(args.name, {field: val})
    else:
        merged = dict(p)
        merged[field] = val
        upsert_user_preset(merged)
    print(f"{args.name}.{field} = {val}")
    return 0


def _cmd_set_key(args: argparse.Namespace) -> int:
    p = get_preset(args.name)
    if not p:
        print(f"unknown preset: {args.name}", file=sys.stderr)
        return 1
    if p.get("builtin"):
        update_override(args.name, {"api_key": args.key})
    else:
        merged = dict(p); merged["api_key"] = args.key
        upsert_user_preset(merged)
    print(f"key saved for {args.name}")
    return 0


def _cmd_set_header(args: argparse.Namespace) -> int:
    p = get_preset(args.name)
    if not p:
        print(f"unknown preset: {args.name}", file=sys.stderr)
        return 1
    headers = dict(p.get("extra_headers") or {})
    if args.value == "":
        headers.pop(args.header, None)
    else:
        headers[args.header] = args.value
    if p.get("builtin"):
        update_override(args.name, {"extra_headers": headers})
    else:
        merged = dict(p); merged["extra_headers"] = headers
        upsert_user_preset(merged)
    print(f"{args.name}.extra_headers updated")
    return 0


def _cmd_add_alias(args: argparse.Namespace) -> int:
    p = get_preset(args.name)
    if not p:
        print(f"unknown preset: {args.name}", file=sys.stderr)
        return 1
    aliases = dict(p.get("model_aliases") or {})
    aliases[args.alias] = args.target
    if p.get("builtin"):
        update_override(args.name, {"model_aliases": aliases})
    else:
        merged = dict(p); merged["model_aliases"] = aliases
        upsert_user_preset(merged)
    print(f"{args.alias} -> {args.target}")
    return 0


def _cmd_models(args: argparse.Namespace) -> int:
    p = get_preset(args.name)
    if not p:
        print(f"unknown preset: {args.name}", file=sys.stderr)
        return 1
    try:
        models = list_models_for_preset(p)
    except DiscoverError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    for m in models:
        print(m)
    return 0


def _cmd_ui(args: argparse.Namespace) -> int:
    cfg = load()
    if cfg.get("autostart_proxy") and cfg.get("enabled"):
        active = cfg.get("active") or "anthropic"
        preset = get_preset(active, cfg) or {}
        if (preset.get("format") or "").lower() == "openai" and active != "anthropic":
            proxy.ensure_running()
    server.serve(open_browser=not args.no_browser)
    return 0


def _cmd_proxy_start(args: argparse.Namespace) -> int:
    ok = proxy.ensure_running()
    print("running" if ok else "failed to start", file=sys.stderr if not ok else sys.stdout)
    return 0 if ok else 1


def _cmd_proxy_stop(args: argparse.Namespace) -> int:
    proxy.stop()
    print("stopped")
    return 0


def _cmd_proxy_status(args: argparse.Namespace) -> int:
    print("running" if proxy.is_running() else "stopped")
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    cfg = load()
    text = json.dumps(cfg, indent=2)
    if args.path == "-":
        print(text)
    else:
        with open(args.path, "w", encoding="utf-8") as f:
            f.write(text)
    return 0


def _cmd_import(args: argparse.Namespace) -> int:
    with open(args.path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    save(cfg)
    system_env.apply_state()
    print(f"imported {args.path}")
    return 0


def _cmd_autostart(args: argparse.Namespace) -> int:
    if args.action == "enable":
        ok = autostart.enable()
        print(f"autostart: {'enabled' if ok else 'failed'} ({autostart.describe()})")
        return 0 if ok else 1
    if args.action == "disable":
        ok = autostart.disable()
        print(f"autostart: {'disabled' if ok else 'was not enabled'}")
        return 0
    print(f"autostart: {'enabled' if autostart.is_enabled() else 'disabled'}")
    print(f"location:  {autostart.describe()}")
    return 0


def _cmd_boot(args: argparse.Namespace) -> int:
    """Internal: re-apply state at login. Called by the autostart entry."""
    system_env.apply_state()
    return 0


def _cmd_update(args: argparse.Namespace) -> int:
    if args.action == "check":
        info = updater.check(force=True)
        if info.get("error"):
            print(f"error: {info['error']}", file=sys.stderr)
            return 1
        if info["has_update"]:
            print(f"Update available — {info['current_sha'][:7]} → {info['latest_sha'][:7]}")
            if info.get("latest_message"):
                print(f"  '{info['latest_message']}'")
            print("  Run `claude-oneclick update apply` to install.")
            return 0
        print("Up to date.")
        return 0
    if args.action == "apply":
        info = updater.check(force=True)
        if not info.get("has_update"):
            print("Already up to date.")
            return 0
        if info.get("dirty"):
            print("error: working tree has local edits. Commit or stash first.", file=sys.stderr)
            return 1
        print("Updating…")
        result = updater.apply()
        if result.get("ok"):
            print("✓ Updated to", (result.get("current_sha") or "")[:7])
            return 0
        print(f"error: {result.get('error', 'update failed')}", file=sys.stderr)
        return 1
    print(f"unknown action: {args.action}", file=sys.stderr)
    return 2


def _cmd_post_install(args: argparse.Namespace) -> int:
    """Internal hook: install (or remove) shell rc / Windows env / VSCode / launcher."""
    from claude_oneclick import shell as posix_shell
    from claude_oneclick import vscode, windows

    if args.uninstall:
        posix_shell.uninstall_shell_hook()
        windows.uninstall_powershell_hook()
        windows.uninstall_cmd_autorun()
        windows.clear_user_env()
        vscode.remove_all()
        launcher.uninstall_for_current_os()
        print("removed shell + VSCode + launcher integration")
        return 0

    if platform.system() == "Windows":
        windows.write_env_files({})
        windows.install_powershell_hook()
        windows.install_cmd_autorun()
    else:
        posix_shell.install_shell_hook()
    # Make sure env.sh / env.cmd / env.ps1 reflect the *current* config
    # (which is OFF by default on first install).
    system_env.apply_state(manage_proxy=False)
    paths = launcher.install_for_current_os()
    if paths:
        print("launcher installed:")
        for p in paths:
            print(f"  {p}")
    return 0


# ---------- argparse wiring -------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="claude-oneclick", description="VPN-style toggle for Claude Code model endpoints.")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="cmd", required=False)

    sub.add_parser("status", help="show current state").set_defaults(func=_cmd_status)
    sub.add_parser("on", help="turn the active preset ON").set_defaults(func=_cmd_on)
    sub.add_parser("off", help="turn it OFF").set_defaults(func=_cmd_off)
    sub.add_parser("toggle", help="flip ON↔OFF").set_defaults(func=_cmd_toggle)

    pu = sub.add_parser("use", help="set the active preset")
    pu.add_argument("name"); pu.set_defaults(func=_cmd_use)

    sub.add_parser("presets", help="list presets").set_defaults(func=_cmd_presets)

    pa = sub.add_parser("add", help="add a custom preset")
    pa.add_argument("name")
    pa.add_argument("--label")
    pa.add_argument("--base-url", required=True)
    pa.add_argument("--api-key", default="")
    pa.add_argument("--model", default="")
    pa.add_argument("--small-fast-model", default="")
    pa.add_argument("--format", choices=["openai", "anthropic"], default="openai")
    pa.add_argument("--notes", default="")
    pa.add_argument("--reasoning", action="store_true",
                    help="enable reasoning mode (sends reasoning_effort on every request)")
    pa.add_argument("--reasoning-effort", choices=["low", "medium", "high"], default="medium")
    pa.set_defaults(func=_cmd_add)

    pd = sub.add_parser("delete", help="delete a custom preset"); pd.add_argument("name")
    pd.set_defaults(func=_cmd_delete)

    ps = sub.add_parser("set", help="set a single field on a preset")
    ps.add_argument("name"); ps.add_argument("field"); ps.add_argument("value")
    ps.set_defaults(func=_cmd_set)

    pk = sub.add_parser("set-key", help="save the API key for a preset")
    pk.add_argument("name"); pk.add_argument("key"); pk.set_defaults(func=_cmd_set_key)

    ph = sub.add_parser("set-header", help="set or remove an extra HTTP header")
    ph.add_argument("name"); ph.add_argument("header"); ph.add_argument("value", nargs="?", default="")
    ph.set_defaults(func=_cmd_set_header)

    paa = sub.add_parser("add-alias", help="map a model id to an upstream id")
    paa.add_argument("name"); paa.add_argument("alias"); paa.add_argument("target")
    paa.set_defaults(func=_cmd_add_alias)

    pm = sub.add_parser("models", help="list models live from a preset's provider")
    pm.add_argument("name"); pm.set_defaults(func=_cmd_models)

    pui = sub.add_parser("ui", help="start the web UI")
    pui.add_argument("--no-browser", action="store_true")
    pui.set_defaults(func=_cmd_ui)

    sub.add_parser("proxy-start", help="start the translation proxy").set_defaults(func=_cmd_proxy_start)
    sub.add_parser("proxy-stop", help="stop the translation proxy").set_defaults(func=_cmd_proxy_stop)
    sub.add_parser("proxy-status", help="proxy running?").set_defaults(func=_cmd_proxy_status)

    pex = sub.add_parser("export", help="export config.json"); pex.add_argument("path", nargs="?", default="-")
    pex.set_defaults(func=_cmd_export)
    pim = sub.add_parser("import", help="import a config.json"); pim.add_argument("path")
    pim.set_defaults(func=_cmd_import)

    pas = sub.add_parser("autostart", help="enable/disable starting at system login")
    pas.add_argument("action", choices=["enable", "disable", "status"], nargs="?", default="status")
    pas.set_defaults(func=_cmd_autostart)

    pup = sub.add_parser("update", help="check for / apply updates from GitHub")
    pup.add_argument("action", choices=["check", "apply"], nargs="?", default="check")
    pup.set_defaults(func=_cmd_update)

    pb = sub.add_parser("_boot", help=argparse.SUPPRESS)
    pb.set_defaults(func=_cmd_boot)

    ppi = sub.add_parser("_post_install", help=argparse.SUPPRESS)
    ppi.add_argument("--uninstall", action="store_true")
    ppi.set_defaults(func=_cmd_post_install)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        # No subcommand given → behave like `status`.
        return _print_status()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
