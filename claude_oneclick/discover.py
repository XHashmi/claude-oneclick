"""Fetch model lists from upstream providers.

Almost every OpenAI-compatible provider exposes `GET {base}/v1/models` and
returns `{"data": [{"id": "..."}, ...]}`. A few quirks:

- NVIDIA NIMs uses the same shape under `https://integrate.api.nvidia.com/v1/models`.
- Ollama uses `GET /api/tags` and returns `{"models": [{"name": "..."}]}`.
- Anthropic's `/v1/models` exists too, but we don't override its model list.

This module dispatches per-provider and returns a flat `list[str]` of model ids.
Network failures surface as `DiscoverError` so the UI can show a friendly toast.
"""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class DiscoverError(RuntimeError):
    pass


def _http_get(url: str, headers: dict[str, str], timeout: float = 10.0) -> Any:
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            return json.loads(body.decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        raise DiscoverError(f"HTTP {e.code} from {url}: {detail}") from e
    except (urllib.error.URLError, socket.timeout, ConnectionError) as e:
        raise DiscoverError(f"Network error fetching {url}: {e}") from e
    except json.JSONDecodeError as e:
        raise DiscoverError(f"Non-JSON response from {url}: {e}") from e


# ---------- per-provider adapters --------------------------------------------

def _ids_from_openai_models(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data") or []
    out: list[str] = []
    for entry in data:
        if isinstance(entry, dict):
            mid = entry.get("id") or entry.get("name")
            if isinstance(mid, str):
                out.append(mid)
    return out


def _ids_from_ollama_tags(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []
    out: list[str] = []
    for entry in payload.get("models") or []:
        if isinstance(entry, dict):
            name = entry.get("name") or entry.get("model")
            if isinstance(name, str):
                out.append(name)
    return out


def _is_ollama(base_url: str) -> bool:
    host = urllib.parse.urlparse(base_url).hostname or ""
    return host in ("localhost", "127.0.0.1", "::1") and ":11434" in base_url


# ---------- public API -------------------------------------------------------

def list_models(
    base_url: str,
    api_key: str | None = None,
    *,
    timeout: float = 10.0,
) -> list[str]:
    """Return a sorted, de-duplicated list of model ids from an upstream."""
    if not base_url:
        raise DiscoverError("base_url is empty")
    base = base_url.rstrip("/")

    if _is_ollama(base):
        # Ollama exposes `/api/tags`, not `/v1/models`.
        payload = _http_get(f"{base}/api/tags", {}, timeout=timeout)
        ids = _ids_from_ollama_tags(payload)
    else:
        # Tolerate base URLs that include `/v1` (OpenAI-SDK convention) or
        # don't (DeepSeek docs, NVIDIA NIMs docs).
        if base.endswith("/v1"):
            url = f"{base}/models"
        else:
            url = f"{base}/v1/models"
        headers = {"Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        payload = _http_get(url, headers, timeout=timeout)
        ids = _ids_from_openai_models(payload)

    # de-dupe + stable sort
    return sorted(set(ids))


def list_models_for_preset(preset: dict[str, Any], *, timeout: float = 10.0) -> list[str]:
    """Convenience wrapper that pulls base_url + api_key out of a preset dict."""
    return list_models(
        preset.get("base_url", ""),
        preset.get("api_key") or None,
        timeout=timeout,
    )
