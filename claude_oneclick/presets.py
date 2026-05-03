"""Built-in presets for popular Claude Code-compatible providers.

A preset is a dict with these keys:

    name              short id used by `claude-oneclick use <name>`
    label             human-readable label for the UI
    base_url          upstream API base (e.g. https://api.deepseek.com)
    api_key_env       env var the user should populate, OR an explicit key
                      stored in config (api_key) — UI sets `api_key`
    api_key           literal API key (set by user via UI/CLI)
    model             model id passed as ANTHROPIC_MODEL
    small_fast_model  model id passed as ANTHROPIC_SMALL_FAST_MODEL
    format            "openai" — go through translation proxy
                      "anthropic" — talk to upstream directly
    notes             freeform string shown in UI
    builtin           True for the presets shipped in this file
"""
from __future__ import annotations

from typing import Any


BUILTIN_PRESETS: list[dict[str, Any]] = [
    {
        "name": "deepseek",
        "label": "DeepSeek (latest chat alias)",
        "base_url": "https://api.deepseek.com",
        "api_key": "",
        "model": "deepseek-chat",
        "small_fast_model": "deepseek-chat",
        "format": "openai",
        "notes": "Stable alias DeepSeek points at their latest chat model. "
                 "Click 'Refresh model list' to see what's actually live.",
        "builtin": True,
    },
    {
        "name": "deepseek-v4",
        "label": "DeepSeek V4",
        "base_url": "https://api.deepseek.com",
        "api_key": "",
        "model": "deepseek-v4",
        "small_fast_model": "deepseek-chat",
        "format": "openai",
        "notes": "Pins to DeepSeek-V4 explicitly. If the upstream id differs, "
                 "use 'Refresh model list' to pick the live one.",
        "builtin": True,
    },
    {
        "name": "deepseek-v4-reasoner",
        "label": "DeepSeek V4 Reasoner",
        "base_url": "https://api.deepseek.com",
        "api_key": "",
        "model": "deepseek-v4-reasoner",
        "small_fast_model": "deepseek-v4",
        "format": "openai",
        "notes": "DeepSeek-V4 reasoner for the main slot, V4 chat for the "
                 "small/fast slot.",
        "builtin": True,
    },
    {
        "name": "deepseek-reasoner",
        "label": "DeepSeek Reasoner (R1)",
        "base_url": "https://api.deepseek.com",
        "api_key": "",
        "model": "deepseek-reasoner",
        "small_fast_model": "deepseek-chat",
        "format": "openai",
        "notes": "DeepSeek-R1 for the main model, the chat alias for "
                 "small/fast.",
        "builtin": True,
    },
    {
        "name": "nvidia-nims-llama",
        "label": "NVIDIA NIMs — Llama 3.1 405B",
        "base_url": "https://integrate.api.nvidia.com",
        "api_key": "",
        "model": "meta/llama-3.1-405b-instruct",
        "small_fast_model": "meta/llama-3.1-70b-instruct",
        "format": "openai",
        "notes": "NVIDIA-hosted Llama 3.1. Get a key at build.nvidia.com.",
        "builtin": True,
    },
    {
        "name": "nvidia-nims-nemotron",
        "label": "NVIDIA NIMs — Nemotron 70B",
        "base_url": "https://integrate.api.nvidia.com",
        "api_key": "",
        "model": "nvidia/llama-3.1-nemotron-70b-instruct",
        "small_fast_model": "meta/llama-3.1-8b-instruct",
        "format": "openai",
        "notes": "NVIDIA Nemotron tuned Llama 70B.",
        "builtin": True,
    },
    {
        "name": "nvidia-nims-deepseek-r1",
        "label": "NVIDIA NIMs — DeepSeek R1",
        "base_url": "https://integrate.api.nvidia.com",
        "api_key": "",
        "model": "deepseek-ai/deepseek-r1",
        "small_fast_model": "meta/llama-3.1-8b-instruct",
        "format": "openai",
        "notes": "DeepSeek R1 hosted on NVIDIA NIMs.",
        "builtin": True,
    },
    {
        "name": "openrouter",
        "label": "OpenRouter (Anthropic passthrough)",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": "",
        "model": "anthropic/claude-sonnet-4",
        "small_fast_model": "anthropic/claude-haiku-4.5",
        "format": "openai",
        "notes": "OpenRouter routes to many providers; uses OpenAI format.",
        "builtin": True,
    },
    {
        "name": "groq",
        "label": "Groq",
        "base_url": "https://api.groq.com/openai",
        "api_key": "",
        "model": "llama-3.3-70b-versatile",
        "small_fast_model": "llama-3.1-8b-instant",
        "format": "openai",
        "notes": "Very fast inference; smaller context windows.",
        "builtin": True,
    },
    {
        "name": "together",
        "label": "Together AI",
        "base_url": "https://api.together.xyz",
        "api_key": "",
        "model": "deepseek-ai/DeepSeek-V3",
        "small_fast_model": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
        "format": "openai",
        "notes": "Together AI hosted open models.",
        "builtin": True,
    },
    {
        "name": "fireworks",
        "label": "Fireworks AI",
        "base_url": "https://api.fireworks.ai/inference",
        "api_key": "",
        "model": "accounts/fireworks/models/deepseek-v3",
        "small_fast_model": "accounts/fireworks/models/llama-v3p1-8b-instruct",
        "format": "openai",
        "notes": "Fireworks-hosted open models.",
        "builtin": True,
    },
    {
        "name": "ollama",
        "label": "Ollama (local)",
        "base_url": "http://localhost:11434",
        "api_key": "ollama",
        "model": "llama3.1:70b",
        "small_fast_model": "llama3.1:8b",
        "format": "openai",
        "notes": "Talks to a local Ollama instance. No real API key needed.",
        "builtin": True,
    },
    {
        "name": "anthropic",
        "label": "Anthropic (default, no override)",
        "base_url": "",
        "api_key": "",
        "model": "",
        "small_fast_model": "",
        "format": "anthropic",
        "notes": "The 'no-op' preset. Turning ON with this active is the same as OFF.",
        "builtin": True,
    },
]


def builtin_by_name(name: str) -> dict[str, Any] | None:
    for p in BUILTIN_PRESETS:
        if p["name"] == name:
            return dict(p)
    return None


def builtin_names() -> list[str]:
    return [p["name"] for p in BUILTIN_PRESETS]
