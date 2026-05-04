"""Per-preset, per-day token + cost ledger.

The proxy already gets ``usage.{prompt_tokens,completion_tokens}`` back
from upstream on every call. We tally those into a small JSON file so
the UI can show "today: 12.3K in / 4.7K out · ≈ $0.42 (DeepSeek V4 Pro
· 23 requests)".

Storage shape::

    {
      "<preset-name>": {
        "<YYYY-MM-DD>": {
          "input": 12345,
          "output": 4567,
          "requests": 23,
          "errors": 1
        }
      }
    }

File path: ``~/.config/claude-oneclick/usage.json`` (mode 0600). One
file per user; small enough that we just rewrite it whole on each
record (atomic via .tmp + rename).

Cost estimation is best-effort: each preset can carry
``cost_per_1m_input`` and ``cost_per_1m_output`` floats; if both are
set, ``estimate_cost()`` returns a USD figure for the totals. Built-in
presets ship sensible defaults for the paid tiers; free presets get
zero. Users can override per-preset.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import threading
from typing import Any

from claude_oneclick.paths import config_dir, ensure_dirs


_LOCK = threading.Lock()


def _path():
    return config_dir() / "usage.json"


def _today() -> str:
    return _dt.date.today().isoformat()


def _load() -> dict[str, Any]:
    p = _path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _save(data: dict[str, Any]) -> None:
    ensure_dirs()
    p = _path()
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(p)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def record(preset: str, *, input_tokens: int = 0, output_tokens: int = 0,
           error: bool = False) -> None:
    """Add one request's tokens to the ledger. Thread-safe."""
    with _LOCK:
        data = _load()
        bucket = data.setdefault(preset, {}).setdefault(_today(), {
            "input": 0, "output": 0, "requests": 0, "errors": 0,
        })
        bucket["input"] += int(input_tokens or 0)
        bucket["output"] += int(output_tokens or 0)
        bucket["requests"] += 1
        if error:
            bucket["errors"] += 1
        _save(data)


def report(days: int = 7) -> dict[str, Any]:
    """Summarize the last ``days`` days. Includes per-preset and total totals."""
    data = _load()
    today = _dt.date.today()
    window = {(today - _dt.timedelta(days=i)).isoformat() for i in range(days)}
    presets: dict[str, Any] = {}
    total_in = total_out = total_req = total_err = 0
    for preset, days_map in data.items():
        agg = {"input": 0, "output": 0, "requests": 0, "errors": 0}
        for date, b in days_map.items():
            if date in window:
                agg["input"] += b.get("input", 0)
                agg["output"] += b.get("output", 0)
                agg["requests"] += b.get("requests", 0)
                agg["errors"] += b.get("errors", 0)
        if agg["requests"]:
            presets[preset] = agg
            total_in += agg["input"]
            total_out += agg["output"]
            total_req += agg["requests"]
            total_err += agg["errors"]
    return {
        "days": days,
        "today": today.isoformat(),
        "presets": presets,
        "total": {"input": total_in, "output": total_out,
                  "requests": total_req, "errors": total_err},
    }


def estimate_cost(preset_obj: dict[str, Any], input_tokens: int, output_tokens: int) -> float | None:
    """USD estimate. Returns None if the preset has no pricing config."""
    cin = preset_obj.get("cost_per_1m_input")
    cout = preset_obj.get("cost_per_1m_output")
    if cin is None and cout is None:
        return None
    cost = 0.0
    if cin is not None:
        cost += (input_tokens / 1_000_000.0) * float(cin)
    if cout is not None:
        cost += (output_tokens / 1_000_000.0) * float(cout)
    return round(cost, 4)
